"""Render the Telegram report chart as a PNG (Pillow).

Colors match the Insights page (validated on the dark surface):
download #0ea5c9, upload #e0458f, single-series bars #0ea5c9.
"""
import io
import time

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 680
BG = (13, 22, 40)
INK1, INK2, INK3 = (230, 237, 246), (159, 176, 198), (107, 127, 153)
GRID = (32, 44, 66)
DOWN, UP, BAR = (14, 165, 201), (224, 69, 143), (14, 165, 201)
FONT = "/usr/share/fonts/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(FONT_B if bold else FONT, size)
    except OSError:
        return ImageFont.load_default()


def _gb(b: float) -> str:
    if b >= 2**30:
        return f"{b / 2**30:.1f} GB"
    if b >= 2**20:
        return f"{b / 2**20:.0f} MB"
    return f"{b / 2**10:.0f} KB"


def _nice(v: float) -> float:
    """Top of the axis in the same 1024-based unit the labels use; splits into 4 clean ticks."""
    if v <= 0:
        return 1
    unit = 1
    while v / unit >= 1024 and unit < 2**40:
        unit *= 1024
    x = v / unit
    p = 10 ** len(str(int(x))) / 10
    m = x / p
    return (1 if m <= 1 else 2 if m <= 2 else 4 if m <= 4 else 8 if m <= 8 else 10) * p * unit


def _bar(d: ImageDraw.ImageDraw, x0, y0, x1, y1, color, round_top=True):
    if y1 - y0 < 1:
        return
    r = min(4, (x1 - x0) / 2, y1 - y0)
    if round_top:
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=color)
        d.rectangle([x0, y0 + r, x1, y1], fill=color)  # square at the baseline
    else:
        d.rectangle([x0, y0, x1, y1], fill=color)


def report_png(ins: dict, key: str) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    title = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days"}[key]
    t = ins["totals"]
    d.text((40, 28), f"Netwatch · {title}", font=_font(30, True), fill=INK1)
    d.text((40, 70), f"Total {_gb(t['down'] + t['up'])}   ↓ {_gb(t['down'])}   ↑ {_gb(t['up'])}",
           font=_font(18), fill=INK2)

    # ---- left: stacked bars per day (or hour) ----
    if key == "today":
        bars = [(f"{h['hour']:02d}", h["down"], h["up"]) for h in ins.get("hourly", [])]
        sub = "Data per hour"
    else:
        bars = [(x["day"][5:], x["down"], x["up"]) for x in ins["daily"]]
        sub = "Data per day"
    L, T, R, B = 110, 150, 700, 610
    d.text((40, 116), sub, font=_font(16, True), fill=INK2)
    lx = R - 230
    d.rounded_rectangle([lx, 120, lx + 12, 132], 3, fill=DOWN)
    d.text((lx + 18, 116), "Download", font=_font(15), fill=INK2)
    d.rounded_rectangle([lx + 120, 120, lx + 132, 132], 3, fill=UP)
    d.text((lx + 138, 116), "Upload", font=_font(15), fill=INK2)
    top = _nice(max([b[1] + b[2] for b in bars] + [1]))
    f12 = _font(13)
    for i in range(5):
        y = B - (B - T) * i / 4
        d.line([L, y, R, y], fill=GRID, width=1)
        lab = _gb(top * i / 4) if i else "0"
        d.text((L - 10 - d.textlength(lab, font=f12), y - 8), lab, font=f12, fill=INK3)
    if bars:
        slot = (R - L) / len(bars)
        bw = max(3, min(46, slot * 0.62))
        every = max(1, -(-len(bars) // 10))
        for i, (lab, dn, up) in enumerate(bars):
            x0 = L + slot * i + (slot - bw) / 2
            dh = (B - T) * dn / top
            uh = (B - T) * up / top
            if dh:
                _bar(d, x0, B - dh, x0 + bw, B, DOWN, round_top=not uh)
            if uh:
                gap = 2 if dh else 0  # 2px surface gap between stacked segments
                _bar(d, x0, B - dh - gap - uh, x0 + bw, B - dh - gap, UP)
            if i % every == 0 or i == len(bars) - 1:
                d.text((x0 + bw / 2 - d.textlength(lab, font=f12) / 2, B + 8), lab, font=f12, fill=INK3)

    # ---- right: top devices ----
    X0, X1 = 770, 1160
    d.text((X0, 116), "Top devices", font=_font(16, True), fill=INK2)
    devs = [x for x in ins["devices"] if not x.get("self")][:7]
    dmax = max([x["down"] + x["up"] for x in devs] + [1])
    f15, f14 = _font(15), _font(14)
    y = 156
    for x in devs:
        name = x["label"] if len(x["label"]) <= 26 else x["label"][:25] + "…"
        val = _gb(x["down"] + x["up"])
        d.text((X0, y), name, font=f15, fill=INK1)
        d.text((X1 - d.textlength(val, font=f14), y + 1), val, font=f14, fill=INK2)
        d.rounded_rectangle([X0, y + 24, X1, y + 34], 4, fill=(22, 33, 54))
        w = max(3, (X1 - X0) * (x["down"] + x["up"]) / dmax)
        d.rounded_rectangle([X0, y + 24, X0 + w, y + 34], 4, fill=BAR)
        y += 62

    # ---- footer ----
    cap = ins["capacity"]
    lat = ins["health"]["internet"]["avg_rtt"]
    foot = []
    if lat is not None:
        foot.append(f"Latency {lat:.0f} ms")
    foot.append(f"{len(ins['health']['outages'])} outage(s)")
    if cap["peak_bps"]:
        foot.append(f"Peak {cap['peak_bps'] * 8 / 1e6:.0f} Mb/s")
    if cap["saturated_minutes"]:
        foot.append(f"Port limit hit {cap['saturated_minutes']} min")
    d.text((40, H - 40), "   ·   ".join(foot), font=_font(15), fill=INK3)
    stamp = time.strftime("%a %d %b %H:%M")
    d.text((W - 40 - d.textlength(stamp, font=_font(14)), H - 40), stamp, font=_font(14), fill=INK3)

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
