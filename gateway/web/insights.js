"use strict";

const $ = (s) => document.querySelector(s);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtBytes = (n) => {
  n = n || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i >= 3 || (i && n < 10) ? 1 : 0)} ${u[i]}`;
};
const fmtMbps = (Bps) => `${((Bps || 0) * 8 / 1e6).toFixed((Bps * 8) / 1e6 < 10 ? 1 : 0)} Mb/s`;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const ICON = { gateway: "🛡️", phone: "📱", tablet: "📱", computer: "💻", tv: "📺", console: "🎮",
  speaker: "🔊", iot: "💡", unknown: "📡" };

let range = new URLSearchParams(location.search).get("range") || "7d";
let data = null;

// ---------- tooltip ----------
const tip = $("#tip");
function showTip(e, html) {
  tip.innerHTML = html;
  tip.classList.remove("hidden");
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > innerHeight - 8) y = e.clientY - h - pad;
  tip.style.left = `${x}px`;
  tip.style.top = `${y}px`;
}
const hideTip = () => tip.classList.add("hidden");
// Scrolling moves charts under a stationary cursor without firing mouseleave.
addEventListener("scroll", hideTip, { passive: true });
const row = (color, label, value) =>
  `<div class="r"><span>${color ? `<i style="background:${color}"></i>` : ""}${esc(label)}</span><b>${esc(value)}</b></div>`;

// ---------- canvas helpers ----------
function setup(cv) {
  const dpr = devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(h * dpr);
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "11px Inter, system-ui, sans-serif";
  return [ctx, w, h];
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), m = v / p;
  // Only maxima that split into 4 clean ticks.
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 4 ? 4 : m <= 8 ? 8 : 10) * p;
}
function niceBytesMax(v) {
  // Step in the same 1024-based unit the labels use, so ticks read 1 GB, 2 GB…
  if (v <= 0) return 1;
  const k = Math.min(4, Math.floor(Math.log(v) / Math.log(1024)));
  const unit = 1024 ** k;
  return niceMax(v / unit) * unit;
}
function roundTop(ctx, x, y, w, h, r) {
  // Bar anchored on the baseline: square bottom, rounded data-end.
  r = Math.min(r, w / 2, h);
  ctx.beginPath();
  ctx.moveTo(x, y + h);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h);
  ctx.closePath();
  ctx.fill();
}
function yAxis(ctx, left, top, bottom, w, max, fmt) {
  ctx.strokeStyle = css("--grid");
  ctx.fillStyle = css("--ink-3");
  ctx.lineWidth = 1;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const y = bottom - ((bottom - top) * i) / 4;
    ctx.beginPath(); ctx.moveTo(left, Math.round(y) + 0.5); ctx.lineTo(w, Math.round(y) + 0.5); ctx.stroke();
    ctx.fillText(fmt((max * i) / 4), left - 6, y);
  }
}

// ---------- stacked bars: data per day (or per hour for today) ----------
function drawUsage() {
  const cv = $("#c-daily");
  const [ctx, w, h] = setup(cv);
  let bars;
  if (range === "today") {
    bars = data.hourly.map((h) => ({ label: `${String(h.hour).padStart(2, "0")}:00`, down: h.down, up: h.up }));
    $("#usage-title").textContent = "Data per hour today";
  } else {
    bars = data.daily.map((d) => ({ label: d.day, down: d.down, up: d.up }));
    $("#usage-title").textContent = "Data per day";
  }
  const left = 58, top = 10, bottom = h - 24;
  const max = niceBytesMax(Math.max(...bars.map((b) => b.down + b.up), 1));
  yAxis(ctx, left, top, bottom, w, max, fmtBytes);
  const slot = (w - left) / Math.max(1, bars.length);
  const bw = Math.max(3, Math.min(42, slot * 0.62));
  const y = (v) => ((bottom - top) * v) / max;
  const hits = [];
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  bars.forEach((b, i) => {
    const x = left + slot * i + (slot - bw) / 2;
    const dh = y(b.down), uh = y(b.up);
    ctx.fillStyle = css("--c-down");
    if (dh > 0) {
      if (uh > 0) ctx.fillRect(x, bottom - dh, bw, dh);
      else roundTop(ctx, x, bottom - dh, bw, dh, 4);
    }
    if (uh > 0) {
      ctx.fillStyle = css("--c-up");
      // 2px surface gap between stacked segments.
      roundTop(ctx, x, bottom - dh - uh - (dh > 0 ? 2 : 0), bw, uh, 4);
    }
    hits.push({ x0: left + slot * i, x1: left + slot * (i + 1), b });
    const every = Math.ceil(bars.length / 10);
    if (i % every === 0 || i === bars.length - 1) {
      ctx.fillStyle = css("--ink-3");
      const lab = range === "today" ? b.label : b.label.slice(5);
      ctx.fillText(lab, x + bw / 2, bottom + 6);
    }
  });
  cv.onmousemove = (e) => {
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left;
    const hit = hits.find((t) => mx >= t.x0 && mx < t.x1);
    if (!hit) return hideTip();
    const b = hit.b;
    showTip(e, `<div class="h">${esc(b.label)}</div>` +
      row(css("--c-down"), "Download", fmtBytes(b.down)) + row(css("--c-up"), "Upload", fmtBytes(b.up)) +
      row("", "Total", fmtBytes(b.down + b.up)));
  };
  cv.onmouseleave = hideTip;
}

// ---------- horizontal bars: devices and apps ----------
function hbars(el, items, empty) {
  if (!items.length) { el.innerHTML = `<div class="empty">${empty}</div>`; return; }
  const max = Math.max(...items.map((i) => i.value), 1);
  el.innerHTML = items.map((it, idx) => `
    <div class="hbar" data-i="${idx}">
      <div class="name">${it.icon ? it.icon + " " : ""}${esc(it.name)}${it.sub ? `<small>${esc(it.sub)}</small>` : ""}</div>
      <div class="track"><div class="fill" style="width:${(it.value / max) * 100}%"></div></div>
      <div class="val">${esc(it.display)}</div>
    </div>`).join("");
  el.querySelectorAll(".hbar").forEach((r) => {
    r.addEventListener("mousemove", (e) => showTip(e, items[+r.dataset.i].tip));
    r.addEventListener("mouseleave", hideTip);
  });
}

function drawDevices() {
  hbars($("#devices"), data.devices.slice(0, 12).map((d) => ({
    name: d.label, icon: ICON[d.type] || ICON.unknown, value: d.down + d.up,
    display: fmtBytes(d.down + d.up), sub: `${(d.share * 100).toFixed(0)}% of all data`,
    tip: `<div class="h">${esc(d.label)}</div>` + row(css("--c-down"), "Download", fmtBytes(d.down)) +
      row(css("--c-up"), "Upload", fmtBytes(d.up)) + row("", "Share", `${(d.share * 100).toFixed(1)}%`),
  })), "No traffic recorded in this period yet.");
}

function drawApps() {
  hbars($("#apps"), data.services.map((s) => ({
    name: s.service, value: s.bytes, display: fmtBytes(s.bytes),
    sub: s.top.length ? s.top.map((t) => t.label).slice(0, 2).join(", ") : "",
    tip: `<div class="h">${esc(s.service)}: ${fmtBytes(s.bytes)}</div>` +
      s.top.map((t) => row("", t.label, fmtBytes(t.bytes))).join(""),
  })), "App breakdown builds up as routed devices use the internet. Bypassed devices aren't counted.");
}

// ---------- heatmap ----------
function heatColor(t) {
  // Single-hue sequential ramp: near-surface navy -> cyan -> pale cyan.
  const stops = [[18, 38, 63], [14, 90, 120], [14, 165, 201], [142, 230, 247]];
  const p = Math.min(0.9999, Math.max(0, t)) * (stops.length - 1);
  const i = Math.floor(p), f = p - i, a = stops[i], b = stops[i + 1];
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * f)).join(",")})`;
}
function drawHeat() {
  const cv = $("#c-heat");
  const [ctx, w, h] = setup(cv);
  const left = 40, top = 4, bottom = h - 20;
  const cw = (w - left) / 24, ch = (bottom - top) / 7;
  const max = Math.max(...data.heatmap.flat(), 1);
  ctx.textBaseline = "middle";
  for (let d = 0; d < 7; d++) {
    ctx.fillStyle = css("--ink-3");
    ctx.textAlign = "right";
    ctx.fillText(DAYS[d], left - 8, top + ch * d + ch / 2);
    for (let hr = 0; hr < 24; hr++) {
      const v = data.heatmap[d][hr];
      ctx.fillStyle = v ? heatColor(Math.sqrt(v / max)) : "rgba(148,163,184,0.05)";
      // 2px gap between cells.
      ctx.beginPath();
      ctx.roundRect(left + cw * hr + 1, top + ch * d + 1, cw - 2, ch - 2, 3);
      ctx.fill();
    }
  }
  ctx.textAlign = "center";
  ctx.fillStyle = css("--ink-3");
  for (let hr = 0; hr < 24; hr += 3) ctx.fillText(`${String(hr).padStart(2, "0")}h`, left + cw * hr + cw / 2, bottom + 10);
  cv.onmousemove = (e) => {
    const r = cv.getBoundingClientRect();
    const hr = Math.floor((e.clientX - r.left - left) / cw), d = Math.floor((e.clientY - r.top - top) / ch);
    if (hr < 0 || hr > 23 || d < 0 || d > 6) return hideTip();
    showTip(e, `<div class="h">${DAYS[d]} ${String(hr).padStart(2, "0")}:00–${String(hr + 1).padStart(2, "0")}:00</div>` +
      row("", range === "today" ? "Today" : "Total in period", fmtBytes(data.heatmap[d][hr])));
  };
  cv.onmouseleave = hideTip;
}

// ---------- line charts with crosshair ----------
function lineChart(cv, series, opts) {
  const [ctx, w, h] = setup(cv);
  const left = 52, top = 10, bottom = h - 22;
  const all = series.flatMap((s) => s.points);
  if (!all.length) {
    ctx.fillStyle = css("--ink-3");
    ctx.textAlign = "left";
    ctx.fillText(opts.empty, left, top + 20);
    cv.onmousemove = null;
    return;
  }
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  const maxV = niceMax(Math.max(opts.minMax || 0, ...all.map((p) => p.v ?? 0)));
  yAxis(ctx, left, top, bottom, w, maxV, opts.fmt);
  const X = (t) => left + ((t - t0) / Math.max(1, t1 - t0)) * (w - left - 4);
  const Y = (v) => bottom - (v / maxV) * (bottom - top);

  if (opts.limit) {
    ctx.strokeStyle = css("--st-serious");
    ctx.setLineDash([5, 4]);
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(left, Y(opts.limit.v)); ctx.lineTo(w, Y(opts.limit.v)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = css("--ink-2");
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(opts.limit.label, left + 6, Y(opts.limit.v) + 4);
  }
  for (const s of series) {
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    let pen = false;
    for (const p of s.points) {
      if (p.v == null) { pen = false; continue; }
      const x = X(p.t), y = Y(p.v);
      if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      pen = true;
    }
    ctx.stroke();
  }
  // time labels
  ctx.fillStyle = css("--ink-3");
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const span = t1 - t0;
  let prevLab = "";
  for (let i = 0; i <= 4; i++) {
    const t = t0 + (span * i) / 4;
    const d = new Date(t * 1000);
    const lab = span > 2 * 86400 ? d.toLocaleDateString([], { month: "short", day: "numeric" })
      : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    if (lab === prevLab) continue;
    prevLab = lab;
    ctx.fillText(lab, Math.min(w - 20, Math.max(left + 16, X(t))), bottom + 6);
  }
  const snapshot = ctx.getImageData(0, 0, cv.width, cv.height);
  cv.onmousemove = (e) => {
    const r = cv.getBoundingClientRect(), mx = e.clientX - r.left;
    if (mx < left) return hideTip();
    const t = t0 + ((mx - left) / (w - left - 4)) * (t1 - t0);
    ctx.putImageData(snapshot, 0, 0);
    ctx.strokeStyle = "rgba(230,237,246,0.35)";
    ctx.lineWidth = 1;
    const x = Math.round(X(t)) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke();
    let html = `<div class="h">${new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</div>`;
    for (const s of series) {
      let best = null;
      for (const p of s.points) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
      if (!best) continue;
      if (best.v != null) {
        // >=8px marker with a 2px surface ring.
        ctx.fillStyle = css("--chart-surface");
        ctx.beginPath(); ctx.arc(X(best.t), Y(best.v), 6, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.arc(X(best.t), Y(best.v), 4, 0, Math.PI * 2); ctx.fill();
      }
      html += row(s.color, s.name, best.v == null ? "no reply" : opts.fmt(best.v)) +
        (best.extra ? `<div class="r"><span class="muted">${esc(best.extra)}</span></div>` : "");
    }
    showTip(e, html);
  };
  cv.onmouseleave = () => { ctx.putImageData(snapshot, 0, 0); hideTip(); };
}

function drawLatency() {
  const h = data.health;
  document.querySelectorAll(".router-label").forEach((e) => (e.textContent = data.router_name || "Router"));
  const toPts = (s) => s.series.map((p) => ({ t: p.ts, v: p.rtt, extra: p.loss ? `${p.loss}% loss` : "" }));
  lineChart($("#c-lat"), [
    { name: "Internet", color: css("--c-net"), points: toPts(h.internet) },
    { name: data.router_name || "Router", color: css("--c-router"), points: toPts(h.router) },
  ], { fmt: (v) => `${Math.round(v)} ms`, minMax: 20, empty: "Collecting latency samples (every 30 s)…" });
  $("#outages").innerHTML = h.outages.length
    ? h.outages.slice(-6).reverse().map((o) => `<div><span class="status critical">Outage</span> ${new Date(o.start * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })} · ${o.minutes} min${o.end ? "" : " (ongoing)"}</div>`).join("")
    : `<div><span class="status good">OK</span> No internet outages in this period.</div>`;
}

function drawCapacity() {
  const c = data.capacity;
  lineChart($("#c-cap"), [
    { name: "Peak (per minute)", color: css("--c-bar"), points: c.series.map((p) => ({ t: p.ts, v: p.peak })) },
  ], {
    fmt: fmtMbps, minMax: c.limit_bps ? c.limit_bps * 1.1 : 0, empty: "Collecting speed samples…",
    ...(c.limit_bps ? { limit: { v: c.limit_bps, label: `This machine's ${c.link_mbps} Mb/s link (≈ ${fmtMbps(c.limit_bps)} usable)` } } : {}),
  });
  const upgrade = c.link_mbps && c.link_mbps < 1000 ? " A gigabit network adapter would raise the cap." : "";
  $("#cap-note").textContent = c.saturated_minutes
    ? `Traffic through Netwatch maxed out this machine's ${c.link_mbps} Mb/s link for ${c.saturated_minutes} min in this period.${upgrade} Until then, use ⚡ Full speed on the heaviest devices.`
    : c.peak_bps
      ? `Peak use was ${fmtMbps(c.peak_bps)}.` + (c.link_mbps ? " This machine's link hasn't held you back in this period." : "")
      : "";
}

// ---------- tiles + unusual ----------
function drawTiles() {
  const t = data.totals, total = t.down + t.up;
  $("#t-total").textContent = fmtBytes(total);
  $("#t-total-sub").textContent = `↓ ${fmtBytes(t.down)} · ↑ ${fmtBytes(t.up)}`;
  const dev = data.devices.find((d) => !d.self) || data.devices[0];
  $("#t-dev").textContent = dev ? dev.label : "–";
  $("#t-dev-sub").textContent = dev ? `${fmtBytes(dev.down + dev.up)} · ${(dev.share * 100).toFixed(0)}%` : "";
  const app = data.services.find((s) => !s.service.startsWith("Unknown")) || data.services[0];
  $("#t-app").textContent = app ? app.service : "–";
  $("#t-app-sub").textContent = app ? fmtBytes(app.bytes) : "";
  const lat = data.health.internet;
  $("#t-lat").textContent = lat.avg_rtt != null ? `${lat.avg_rtt} ms` : "–";
  $("#t-lat-sub").textContent = lat.loss_pct != null ? `${lat.loss_pct}% packet loss · ${data.health.outages.length} outage(s)` : "collecting…";
  $("#t-peak").textContent = data.capacity.peak_bps ? fmtMbps(data.capacity.peak_bps) : "–";
  $("#t-peak-sub").textContent = data.capacity.peak_at
    ? `${new Date(data.capacity.peak_at * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false })}${data.capacity.link_mbps ? ` · link ${data.capacity.link_mbps} Mb/s` : ""}` : "";

  const label = { warning: "Check", serious: "Limit", critical: "Outage" };
  $("#unusual").innerHTML = data.unusual.length
    ? data.unusual.map((u) => `<div class="item"><span class="status ${u.severity}">${label[u.severity] || "Note"}</span><span>${esc(u.text)}</span></div>`).join("")
    : `<div class="item"><span class="status good">All clear</span><span>Nothing unusual in this period.</span></div>`;
}

// ---------- load ----------
function renderAll() {
  if (!data) return;
  drawTiles(); drawUsage(); drawDevices(); drawApps(); drawHeat(); drawLatency(); drawCapacity();
}
async function load() {
  const r = await fetch(`/api/insights?range=${range}`);
  if (r.status === 401) { location.href = "/login"; return; }
  data = await r.json();
  const since = new Date(data.since * 1000).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  $("#subtitle").textContent = `Since ${since}. Updated ${new Date().toLocaleTimeString([], { hour12: false })}.`;
  renderAll();
}
document.querySelectorAll(".seg button").forEach((b) => {
  b.classList.toggle("on", b.dataset.range === range);
  b.addEventListener("click", () => {
    range = b.dataset.range;
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    history.replaceState(null, "", `?range=${range}`);
    load();
  });
});
addEventListener("resize", renderAll);
load();
setInterval(load, 60000);
