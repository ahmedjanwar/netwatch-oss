"""Compute the Insights page (and the Telegram report) from stored data."""
import time
from collections import defaultdict

from . import config as C
from .discovery import guess_type



def link_mbps() -> float | None:
    """This machine's link speed in Mb/s: what the user set, else what the network card reports."""
    if C.LINK_MBPS:
        return C.LINK_MBPS
    try:
        speed = int(open(f"/sys/class/net/{C.IFACE}/speed").read().strip())
        return float(speed) if speed > 0 else None  # -1 on Wi-Fi and virtual cards
    except (OSError, ValueError):
        return None


def saturated_bps() -> float | None:
    """Traffic above ~85% of the real-world ceiling (~94% of the link speed) counts as maxed out."""
    mbps = link_mbps()
    return 0.85 * mbps * 0.94 * 1e6 / 8 if mbps else None
RANGES = {"today": None, "7d": 7, "30d": 30}


def _midnight(days_back: int = 0) -> float:
    t = time.localtime(time.time() - days_back * 86400)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _downsample(points: list[tuple[float, float | None, float]], buckets: int) -> list[dict]:
    """Average RTT / max loss per time bucket, so long ranges stay light."""
    if not points:
        return []
    t0, t1 = points[0][0], points[-1][0]
    width = max(30.0, (t1 - t0) / buckets)
    out: dict[int, list] = {}
    for ts, rtt, loss in points:
        b = out.setdefault(int((ts - t0) // width), [0.0, 0, 0.0, ts])
        if rtt is not None:
            b[0] += rtt
            b[1] += 1
        b[2] = max(b[2], loss)
    return [{"ts": round(v[3]), "rtt": round(v[0] / v[1], 1) if v[1] else None, "loss": v[2]}
            for _, v in sorted(out.items())]


def _outages(points: list[tuple[float, float | None, float]]) -> list[dict]:
    """Two or more consecutive full-loss samples count as an outage."""
    res, start, run, last = [], None, 0, None
    for ts, _rtt, loss in points:
        if loss >= 100:
            run += 1
            if start is None:
                start = ts
        else:
            if run >= 2:
                res.append({"start": round(start), "end": round(ts), "minutes": max(1, round((ts - start) / 60))})
            start, run = None, 0
        last = ts
    if run >= 2:
        res.append({"start": round(start), "end": None, "minutes": max(1, round((last - start) / 60))})
    return res


def compute(engine, range_key: str = "7d") -> dict:
    days = RANGES.get(range_key, 7)
    since = _midnight(0) if days is None else _midnight(days - 1)
    now = time.time()
    store = engine.store
    labels = {mac: engine.label(d) for mac, d in engine.devices.items()}
    types = {mac: ("gateway" if d.is_self else guess_type(d.vendor, d.hostname, d.name))
             for mac, d in engine.devices.items()}

    # usage per day / per device (persisted minutes + the current unflushed minute)
    per_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    per_dev: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in store.daily_usage(since):
        per_day[r["day"]][0] += r["down"]
        per_day[r["day"]][1] += r["up"]
        per_dev[r["mac"]][0] += r["down"]
        per_dev[r["mac"]][1] += r["up"]
    today = _day(now)
    for mac, (up, down) in engine._minute_acc.items():
        per_day[today][0] += down
        per_day[today][1] += up
        per_dev[mac][0] += down
        per_dev[mac][1] += up

    day_list = []
    n_days = 1 if days is None else days
    for i in range(n_days - 1, -1, -1):
        d = _day(_midnight(i))
        day_list.append({"day": d, "down": per_day[d][0], "up": per_day[d][1]})

    total_down = sum(v[0] for v in per_dev.values())
    total_up = sum(v[1] for v in per_dev.values())
    total = total_down + total_up
    devices = sorted(({
        "mac": mac, "label": labels.get(mac, mac), "type": types.get(mac, "unknown"),
        "down": v[0], "up": v[1], "share": (v[0] + v[1]) / total if total else 0,
        "self": engine.devices[mac].is_self if mac in engine.devices else False,
    } for mac, v in per_dev.items() if v[0] + v[1] > 0), key=lambda x: -(x["down"] + x["up"]))

    # services
    svc: dict[str, dict] = {}
    for r in store.services_since(_day(since)):
        e = svc.setdefault(r["service"], {"service": r["service"], "bytes": 0, "by": defaultdict(int)})
        e["bytes"] += r["bytes"]
        e["by"][r["mac"]] += r["bytes"]
    for (mac, name), b in engine._svc_acc.items():
        e = svc.setdefault(name, {"service": name, "bytes": 0, "by": defaultdict(int)})
        e["bytes"] += b
        e["by"][mac] += b
    svc_list = sorted(svc.values(), key=lambda e: -e["bytes"])
    svc_total = sum(e["bytes"] for e in svc_list) or 1
    services_out = [{
        "service": e["service"], "bytes": e["bytes"], "share": e["bytes"] / svc_total,
        "top": [{"label": labels.get(m, m), "bytes": b}
                for m, b in sorted(e["by"].items(), key=lambda x: -x[1])[:3]],
    } for e in svc_list[:12]]

    # heatmap: rows Mon..Sun, 24 hours (sqlite %w: 0=Sunday)
    heat = [[0] * 24 for _ in range(7)]
    for r in store.hourly_usage(since):
        heat[(r["dow"] + 6) % 7][r["hour"]] += r["bytes"]

    # today, per hour, split by direction
    hourly = [{"hour": h, "down": 0, "up": 0} for h in range(24)]
    for r in store.hourly_split(_midnight(0)):
        hourly[r["hour"]]["down"] += r["down"]
        hourly[r["hour"]]["up"] += r["up"]
    hour_now = time.localtime(now).tm_hour
    for _mac, (up, down) in engine._minute_acc.items():
        hourly[hour_now]["down"] += down
        hourly[hour_now]["up"] += up

    # internet health
    hp = defaultdict(list)
    for r in store.health_since(max(since, now - 30 * 86400)):
        hp[r["target"]].append((r["ts"], r["rtt"], r["loss"]))
    health = {}
    for target in ("internet", "router"):
        pts = hp.get(target, [])
        rtts = [p[1] for p in pts if p[1] is not None]
        health[target] = {
            "avg_rtt": round(sum(rtts) / len(rtts), 1) if rtts else None,
            "loss_pct": round(sum(p[2] for p in pts) / len(pts), 2) if pts else None,
            "series": _downsample(pts, 240),
        }
    health["outages"] = _outages(hp.get("internet", []))

    # port capacity
    tp = store.throughput_since(since)
    peak = max((r["peak"] for r in tp), default=0.0)
    peak_row = max(tp, key=lambda r: r["peak"], default=None)
    mbps = link_mbps()
    limit = saturated_bps()
    capacity = {
        "limit_bps": mbps * 0.94 * 1e6 / 8 if mbps else None,  # None: speed unknown (Wi-Fi, VM)
        "link_mbps": mbps,
        "peak_bps": peak,
        "peak_at": peak_row["minute"] if peak_row else None,
        "saturated_minutes": sum(1 for r in tp if r["peak"] >= limit) if limit else 0,
        "series": [{"ts": r["minute"], "peak": round(r["peak"])} for r in tp][-2000:],
    }

    return {
        "range": range_key, "since": since, "generated": now, "router_name": engine.router_name(),
        "totals": {"down": total_down, "up": total_up},
        "daily": day_list,
        "devices": devices,
        "services": services_out,
        "heatmap": heat,
        "hourly": hourly[:hour_now + 1],
        "health": health,
        "capacity": capacity,
        "unusual": _unusual(engine, labels, types, capacity, health),
    }


def _unusual(engine, labels, types, capacity, health) -> list[dict]:
    out = []
    store = engine.store
    now = time.time()

    # 1. a device using far more than its usual daily amount
    hist: dict[str, list[int]] = defaultdict(list)
    for r in store.daily_usage(_midnight(7)):
        if r["day"] != _day(now):
            hist[r["mac"]].append(r["up"] + r["down"])
    for mac, (up, down) in engine.today.items():
        today = up + down
        past = hist.get(mac, [])
        if len(past) >= 2 and today > 200 * 2**20:
            avg = sum(past) / 7
            if avg and today >= 3 * avg:
                out.append({"kind": "spike", "mac": mac, "severity": "warning",
                            "text": f"{labels.get(mac, mac)} used {today / 2**30:.1f} GB today, "
                                    f"{today / avg:.0f}× its daily average."})

    # 2. smart-home devices talking to a domain they never used before
    for r in store.new_domains(now - 86400):
        mac = r["mac"]
        d = engine.devices.get(mac)
        if not d or types.get(mac) != "iot" or d.first_seen > now - 2 * 86400:
            continue  # only flag devices with an established history
        out.append({"kind": "new-domain", "mac": mac, "severity": "warning",
                    "text": f"{labels.get(mac, mac)} contacted a new domain: {r['domain']}"})

    # 3. hitting this machine's link speed
    if capacity["saturated_minutes"]:
        mbps = link_mbps()
        upgrade = " A gigabit network adapter would remove this cap." if mbps and mbps < 1000 else ""
        out.append({"kind": "capacity", "mac": "", "severity": "serious",
                    "text": f"Traffic through Netwatch maxed out this machine's {mbps:.0f} Mb/s link for "
                            f"{capacity['saturated_minutes']} min in this period.{upgrade}"})

    # 4. outages
    if health["outages"]:
        mins = sum(o["minutes"] for o in health["outages"])
        out.append({"kind": "outage", "mac": "", "severity": "critical",
                    "text": f"{len(health['outages'])} internet outage(s), {mins} min in total."})

    # 5. devices waiting for approval
    pending = [d for d in engine.devices.values() if not d.approved]
    if pending:
        out.append({"kind": "pending", "mac": "", "severity": "warning",
                    "text": f"{len(pending)} device(s) waiting for approval."})
    return out[:30]


def report_text(engine, range_key: str = "7d") -> str:
    """Plain-HTML summary for Telegram."""
    import html
    ins = compute(engine, range_key)

    def gb(b: float) -> str:
        if b >= 2**30:
            return f"{b / 2**30:.2f} GB"
        return f"{b / 2**20:.0f} MB" if b >= 2**20 else f"{b / 2**10:.0f} KB"

    title = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days"}[range_key]
    t = ins["totals"]
    lines = [f"📊 <b>Netwatch report: {title}</b>",
             f"Total: {gb(t['down'] + t['up'])} (↓ {gb(t['down'])} · ↑ {gb(t['up'])})"]
    if ins["devices"]:
        lines.append("\n<b>Top devices</b>")
        for d in ins["devices"][:5]:
            lines.append(f"• {html.escape(d['label'])}: {gb(d['down'] + d['up'])} ({d['share'] * 100:.0f}%)")
    if ins["services"]:
        lines.append("\n<b>Top apps</b>")
        for s in [x for x in ins["services"] if x["bytes"] >= 2**20][:5]:
            lines.append(f"• {html.escape(s['service'])}: {gb(s['bytes'])}")
    h = ins["health"]
    if h["internet"]["avg_rtt"] is not None:
        lines.append(f"\n<b>Internet</b>: avg latency {h['internet']['avg_rtt']} ms, "
                     f"{len(h['outages'])} outage(s)")
    cap = ins["capacity"]
    if cap["peak_bps"]:
        lines.append(f"Peak speed: {cap['peak_bps'] * 8 / 1e6:.0f} Mb/s"
                     + (f" · maxed out the {link_mbps():.0f} Mb/s link for {cap['saturated_minutes']} min"
                        if cap["saturated_minutes"] else ""))
    if ins["unusual"]:
        lines.append("\n<b>Worth a look</b>")
        for u in ins["unusual"][:6]:
            lines.append(f"• {html.escape(u['text'])}")
    return "\n".join(lines)
