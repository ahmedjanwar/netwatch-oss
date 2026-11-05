"use strict";

// ---------- helpers ----------
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtRate = (Bps) => {
  const b = (Bps || 0) * 8;
  if (b < 1e3) return `${b.toFixed(0)} b/s`;
  if (b < 1e6) return `${(b / 1e3).toFixed(1)} Kb/s`;
  if (b < 1e9) return `${(b / 1e6).toFixed(1)} Mb/s`;
  return `${(b / 1e9).toFixed(2)} Gb/s`;
};
const fmtBytes = (n) => {
  n = n || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${u[i]}`;
};
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const ago = (ts) => {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
};
const ICON = { gateway: "🛡️", phone: "📱", tablet: "📱", watch: "⌚", computer: "💻", tv: "📺", console: "🎮",
  speaker: "🔊", iot: "💡", unknown: "📡" };
const intensity = (Bps) => (Bps > 0 ? Math.min(1, Math.log10(1 + Bps * 8) / 9) : 0);
const COLORS = { down: "#22d3ee", up: "#f472b6", bad: "#f43f5e", muted: "#334155", text: "#dbe7f5" };

// ---------- state ----------
let snap = null;
let selected = null;
let byMac = new Map();
const NEW_WINDOW = 24 * 3600;

// ---------- websocket ----------
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onmessage = (e) => { snap = JSON.parse(e.data); onSnapshot(); };
  ws.onclose = (e) => {
    setConn(false);
    if (e.code === 4401) { location.href = "/login"; return; }
    setTimeout(connect, 2000);
  };
  setInterval(() => ws.readyState === 1 && ws.send("ping"), 25000);
}
function setConn(on) {
  const el = $("#conn");
  el.textContent = on ? "live" : "offline";
  el.className = `conn ${on ? "on" : "off"}`;
}

function onSnapshot() {
  byMac = new Map(snap.devices.map((d) => [d.mac, d]));
  const devs = snap.devices.filter((d) => !d.self);
  $("#k-down").textContent = fmtRate(snap.totals.down);
  $("#k-up").textContent = fmtRate(snap.totals.up);
  $("#k-online").textContent = `${devs.filter((d) => d.online).length} / ${devs.length}`;
  $("#k-blocked").textContent = devs.filter((d) => d.blocked).length;
  $("#k-mode").textContent = snap.mode === "gateway" ? "GATEWAY" : "MONITOR";
  $("#k-mode").style.color = snap.mode === "gateway" ? "#34d399" : "#fcd34d";
  $("#banner").classList.toggle("hidden", snap.mode === "gateway");
  const ab = snap.adblock || {};
  const shieldBtn = $("#shield-toggle");
  shieldBtn.classList.toggle("on", !!ab.enabled);
  shieldBtn.setAttribute("aria-pressed", ab.enabled ? "true" : "false");
  if (!shieldBtn.classList.contains("busy")) {
    $("#k-shield").textContent = ab.enabled ? `${(ab.blocked_today || 0).toLocaleString()} blocked` : "OFF";
  }
  const uncovered = snap.mode === "gateway" && (snap.uptime || 0) >= 120 && !(snap.dns_coverage || {}).device_queries_10m;
  shieldBtn.classList.toggle("warn", !!ab.enabled && uncovered);
  shieldBtn.title = ab.enabled && uncovered ? "On, but devices aren't using Netwatch DNS (see the DNS panel)" : "Turn the ad blocker on or off";
  map.sync(snap);
  renderList();
  if (selected) renderDetail();
  renderFeeds();
  drawTotals();
}

// ---------- device list ----------
function sortDevices(list) {
  return list.sort((a, b) =>
    (b.self - a.self) || (b.online - a.online) || ((b.up + b.down) - (a.up + a.down)) ||
    a.label.localeCompare(b.label));
}
function renderList() {
  const q = $("#search").value.trim().toLowerCase();
  const now = Date.now() / 1000;
  const list = sortDevices(snap.devices.filter((d) =>
    !q || [d.label, d.ip, d.mac, d.vendor, d.hostname].some((v) => (v || "").toLowerCase().includes(q))));
  $("#device-list").innerHTML = list.map((d) => {
    const tags = (d.self ? `<span class="tag gw">gateway</span>` : "") +
      (!d.approved ? `<span class="tag pending">needs approval</span>` : "") +
      (d.blocked ? `<span class="tag blocked">blocked</span>` : "") +
      (d.bypass ? `<span class="tag bypass">⚡ full speed</span>` : "") +
      (d.paused_until ? `<span class="tag paused">⏸ until ${new Date(d.paused_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</span>` : "") +
      (!d.self && now - d.first_seen < NEW_WINDOW ? `<span class="tag new">new</span>` : "");
    const active = d.up + d.down > 0;
    return `<div class="dev ${d.online ? "" : "offline"} ${d.blocked ? "blocked" : ""} ${d.approved ? "" : "pending"}" data-mac="${esc(d.mac)}">
      <div class="ic">${ICON[d.type] || ICON.unknown}</div>
      <div style="min-width:0"><div class="nm">${esc(d.label)}${tags}</div>
        <div class="ip">${esc(d.ip)} · ${esc(d.vendor || d.mac)}</div></div>
      <div class="rt">${active ? `<div class="d">↓ ${fmtRate(d.down)}</div><div class="u">↑ ${fmtRate(d.up)}</div>`
        : `<div class="ip">${d.online ? "idle" : ago(d.last_seen)}</div>`}</div>
    </div>`;
  }).join("") || `<div class="empty">No devices match.</div>`;
}
$("#device-list").addEventListener("click", (e) => {
  const row = e.target.closest(".dev");
  if (row) select(row.dataset.mac);
});
$("#search").addEventListener("input", renderList);

// ---------- detail ----------
let detailTimer = null;
let armTimer = null;
function select(mac) {
  selected = mac;
  $("#list-view").classList.toggle("hidden", !!mac);
  $("#detail-view").classList.toggle("hidden", !mac);
  clearInterval(detailTimer);
  disarm();
  if (!mac) { renderList(); return; }
  $("#d-name").value = byMac.get(mac)?.label || "";
  $("#d-chart")._data = null;
  $("#d-domains").innerHTML = "";
  renderDetail();
  loadDetailExtras();
  detailTimer = setInterval(loadDetailExtras, 30000);
}
$("#back").addEventListener("click", () => select(null));

async function loadDetailExtras() {
  const mac = selected;
  if (!mac) return;
  const [hist, doms] = await Promise.all([
    fetch(`/api/devices/${mac}/history?minutes=60`).then((r) => r.json()).catch(() => []),
    fetch(`/api/devices/${mac}/domains`).then((r) => r.json()).catch(() => []),
  ]);
  if (mac !== selected) return;
  $("#d-chart")._data = hist;
  drawDeviceChart();
  const max = Math.max(1, ...doms.map((d) => d.count));
  $("#d-domains").innerHTML = doms.length ? doms.map((d) => `
    <div><div class="domain"><span>${esc(d.domain)}</span><span class="muted">${d.count}</span></div>
    <div class="domain bar" style="width:${(d.count / max) * 100}%"></div></div>`).join("")
    : `<div class="empty">No DNS lookups recorded yet${snap.mode === "monitor" ? " (needs gateway mode)" : ""}.</div>`;
}

function renderDetail() {
  const d = byMac.get(selected);
  if (!d) { select(null); return; }
  const icon = $("#d-icon");
  icon.textContent = ICON[d.type] || ICON.unknown;
  icon.style.borderColor = d.blocked ? COLORS.bad : d.online ? COLORS.down : COLORS.muted;
  if (document.activeElement !== $("#d-name")) $("#d-name").value = d.label;
  $("#d-approve").classList.toggle("hidden", d.approved);
  const pausedTill = d.paused_until ? new Date(d.paused_until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  $("#d-pause").classList.toggle("hidden", d.self || !!d.paused_until || d.blocked || !d.approved);
  $("#d-pause").classList.toggle("disabled", d.bypass);
  $("#d-pause").title = d.bypass ? "Turn off full speed to pause this device" : "";
  $("#d-resume").classList.toggle("hidden", !d.paused_until);
  $("#d-resume").textContent = `▶ Resume now (paused until ${pausedTill})`;
  $("#d-status").innerHTML = d.paused_until ? `<span style="color:#c7d2fe">⏸ Paused until ${pausedTill} · no internet</span>` : !d.approved ? `<span style="color:#fbbf24">● Waiting for approval · no internet</span>` : d.blocked ? `<span style="color:${COLORS.bad}">● Blocked from internet</span>`
    : d.online ? `<span class="on">● Online</span>${d.routed ? " · routed via gateway" : " · not routed via gateway"}`
      : `● Offline · last seen ${ago(d.last_seen)}`;
  $("#d-down").textContent = fmtRate(d.down);
  $("#d-up").textContent = fmtRate(d.up);
  $("#d-today").textContent = fmtBytes(d.today_up + d.today_down);
  const meta = [
    ["IP", d.ip], ["MAC", d.mac], ["Vendor", d.vendor || "unknown"], ["Hostname", d.hostname || "—"],
    ["Type", d.type], ["First seen", new Date(d.first_seen * 1000).toLocaleString()],
    ["Today", `↓ ${fmtBytes(d.today_down)}  ↑ ${fmtBytes(d.today_up)}`],
  ];
  $("#d-meta").innerHTML = meta.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");

  const bp = $("#d-bypass");
  bp.disabled = d.self || d.blocked || !d.approved;
  bp.classList.toggle("on", d.bypass);
  bp.textContent = d.bypass ? "⚡ Full speed is ON (click to route through Netwatch)" : "⚡ Full speed: bypass Netwatch";
  $("#d-bypass-note").textContent = d.self ? "" : d.blocked
    ? "Unblock this device to use full speed."
    : d.bypass
      ? "This device talks to your router directly, so Netwatch doesn't slow it down. Its DNS lookups still show here, but its traffic doesn't, and it can't be blocked. Takes effect when it reconnects (toggle its Wi-Fi or restart it)."
      : "Sends this device's traffic straight to your router instead of through this machine, for full speed. You'll still see its DNS lookups, but not its traffic, and you can't block it. Takes effect when it reconnects.";

  const btn = $("#d-block");
  btn.disabled = d.self || d.bypass;
  btn.classList.toggle("unblock", d.blocked);
  if (!btn.classList.contains("armed")) btn.textContent = d.self ? "This is the gateway" :
    d.bypass ? "Can't block while full speed is on" :
    d.blocked ? "Unblock internet access" : "Block internet access";

  $("#d-conn-count").textContent = d.flow_count ? `(${d.flow_count})` : "";
  $("#d-flows").innerHTML = d.flows.length ? d.flows.map((f) => `
    <div class="flow">
      <div class="host" title="${esc(f.remote)}">${esc(f.domain || f.remote)}</div>
      <div class="bytes"><span class="d">↓${fmtBytes(f.down)}</span> <span class="u">↑${fmtBytes(f.up)}</span></div>
      <div class="sub">${esc(f.proto)} ${esc(f.remote)}:${f.port}</div>
      <div class="sub" style="text-align:right">${f.down_rate + f.up_rate > 0 ? fmtRate(f.down_rate + f.up_rate) : ""}</div>
    </div>`).join("")
    : `<div class="empty">${d.routed ? "No active connections." : "Not routed through the gateway, so connections aren't visible."}</div>`;
}

async function patchDevice(body) {
  const r = await fetch(`/api/devices/${selected}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const d = await r.json();
  byMac.set(d.mac, d);
  const i = snap.devices.findIndex((x) => x.mac === d.mac);
  if (i >= 0) snap.devices[i] = d;
  renderDetail();
}

$("#d-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") e.target.blur();
  if (e.key === "Escape") { e.target.value = byMac.get(selected)?.label || ""; e.target.blur(); }
});
$("#d-name").addEventListener("blur", (e) => {
  const d = byMac.get(selected);
  const name = e.target.value.trim();
  if (d && name && name !== d.label) patchDevice({ name }).catch((err) => alertInline(err.message));
});

function disarm() {
  clearTimeout(armTimer);
  $("#d-block").classList.remove("armed");
}
$("#d-approve").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-verdict]");
  if (!b || !selected) return;
  b.disabled = true;
  try {
    const r = await fetch(`/api/devices/${selected}/decide`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verdict: b.dataset.verdict }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();
    byMac.set(d.mac, d);
    renderDetail();
  } catch (err) { alertInline(err.message); }
  b.disabled = false;
});

$("#d-pause").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-pause]");
  if (!b || !selected) return;
  const body = b.dataset.pause === "am" ? { until_morning: true } : { minutes: +b.dataset.pause };
  await deviceAction(`/api/devices/${selected}/pause`, body);
});
$("#d-resume").addEventListener("click", () => deviceAction(`/api/devices/${selected}/resume`, {}));
async function deviceAction(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();
    byMac.set(d.mac, d);
    const i = snap.devices.findIndex((x) => x.mac === d.mac);
    if (i >= 0) snap.devices[i] = d;
    renderDetail();
  } catch (err) { alertInline(err.message); }
}

$("#shield-toggle").addEventListener("click", async () => {
  const btn = $("#shield-toggle");
  const turnOn = !btn.classList.contains("on");
  btn.classList.add("busy");
  $("#k-shield").textContent = turnOn ? "turning on…" : "turning off…";
  try {
    const r = await fetch("/api/adblock", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: turnOn }),
    });
    if (!r.ok) throw new Error("failed");
    const st = await r.json();
    snap.adblock = { enabled: st.enabled, blocked_today: st.blocked_today };
    btn.classList.toggle("on", st.enabled);
    $("#k-shield").textContent = st.enabled ? `${st.blocked_today.toLocaleString()} blocked` : "OFF";
  } catch { $("#k-shield").textContent = "error"; }
  btn.classList.remove("busy");
});

$("#d-bypass").addEventListener("click", async () => {
  const d = byMac.get(selected);
  if (!d || d.self) return;
  const bp = $("#d-bypass");
  bp.disabled = true;
  try { await patchDevice({ bypass: !d.bypass }); } catch (err) { alertInline(err.message); }
  bp.disabled = false;
});

$("#d-block").addEventListener("click", async () => {
  const d = byMac.get(selected);
  if (!d || d.self) return;
  const btn = $("#d-block");
  if (!d.blocked && !btn.classList.contains("armed")) {
    btn.classList.add("armed");
    btn.textContent = "Click again to confirm block";
    armTimer = setTimeout(() => { disarm(); renderDetail(); }, 4000);
    return;
  }
  disarm();
  btn.disabled = true;
  try { await patchDevice({ blocked: !d.blocked }); } catch (err) { alertInline(err.message); }
  btn.disabled = false;
});
function alertInline(msg) {
  const s = $("#d-status");
  s.innerHTML = `<span style="color:${COLORS.bad}">Error: ${esc(msg)}</span>`;
}

$("#logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  location.href = "/login";
});

// ---------- feeds ----------
function dnsWarning() {
  const cov = snap.dns_coverage || {};
  if (snap.mode !== "gateway" || cov.device_queries_10m > 0 || (snap.uptime || 0) < 120) return "";
  return `<div class="dns-warn"><b>⚠ Your devices aren't using Netwatch for DNS.</b> ` +
    (cov.ipv6_ra
      ? `Your router still has IPv6 on, so devices use your internet provider's IPv6 DNS instead. That means no lookups show here and <b>the ad blocker can't reach them</b>. Turn off IPv6 on your router's LAN completely (DHCPv6 alone isn't enough): <a href="/settings#setup">setup guide, step 2</a>.`
      : `No device lookups in the last 10 minutes. Devices with a VPN or custom DNS skip Netwatch. Try <a href="/settings#shield">Force Netwatch DNS</a>.`) + `</div>`;
}
function renderFeeds() {
  const name = (mac) => esc(byMac.get(mac)?.label || mac);
  $("#dns-feed").innerHTML = dnsWarning() + snap.dns.slice().reverse().filter((q) => !byMac.get(q.mac)?.self || !dnsWarning()).map((q) => q.blocked
    ? `<div class="blocked-q"><span class="t">${clock(q.ts)}</span><span class="shield">🛡</span> <span class="who">${name(q.mac)}</span> ✕ ${esc(q.domain)}</div>`
    : `<div><span class="t">${clock(q.ts)}</span><span class="who">${name(q.mac)}</span> → ${esc(q.domain)}</div>`
  ).join("") || `<div class="empty">${snap.mode === "monitor"
    ? "DNS lookups appear here once devices use the gateway as their DNS server." : "Waiting for lookups…"}</div>`;
  $("#events").innerHTML = snap.events.slice().reverse().map((e) =>
    `<div class="ev-${esc(e.kind)}"><span class="t">${clock(e.ts)}</span>${esc(e.message)}</div>`
  ).join("") || `<div class="empty">Nothing yet.</div>`;
}

// ---------- charts ----------
function setupCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return [ctx, w, h];
}

function drawTotals() {
  const cv = $("#total-chart");
  const [ctx, w, h] = setupCanvas(cv);
  const pts = snap.history;
  if (pts.length < 2) return;
  const max = Math.max(1, ...pts.map((p) => Math.max(p[1], p[2])));
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v) => h - 14 - (v / max) * (h - 24);
  for (const [idx, color] of [[2, COLORS.down], [1, COLORS.up]]) {
    ctx.beginPath();
    ctx.moveTo(x(pts[0][0]), y(pts[0][idx]));
    for (const p of pts) ctx.lineTo(x(p[0]), y(p[idx]));
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = color;
    ctx.shadowBlur = 8;
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.lineTo(w, h - 14);
    ctx.lineTo(0, h - 14);
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, color + "33");
    g.addColorStop(1, color + "00");
    ctx.fillStyle = g;
    ctx.fill();
  }
  ctx.fillStyle = "#6b7f99";
  ctx.font = "10px ui-monospace, monospace";
  ctx.fillText(`peak ${fmtRate(max)}`, 4, 10);
}

function drawDeviceChart() {
  const cv = $("#d-chart");
  const [ctx, w, h] = setupCanvas(cv);
  const rows = cv._data || [];
  const now = Math.floor(Date.now() / 60000) * 60;
  const buckets = new Map(rows.map((r) => [r.minute, r]));
  const n = 60, bw = w / n, mid = h * 0.62;
  const max = Math.max(1, ...rows.map((r) => Math.max(r.down, r.up)));
  ctx.fillStyle = "rgba(56,189,248,0.12)";
  ctx.fillRect(0, mid, w, 1);
  for (let i = 0; i < n; i++) {
    const r = buckets.get(now - (n - i) * 60);
    if (!r) continue;
    const dh = (r.down / max) * (mid - 4), uh = (r.up / max) * (h - mid - 4);
    ctx.fillStyle = COLORS.down;
    ctx.fillRect(i * bw + 1, mid - dh, bw - 2, dh);
    ctx.fillStyle = COLORS.up;
    ctx.fillRect(i * bw + 1, mid + 1, bw - 2, uh);
  }
  if (!rows.length) {
    ctx.fillStyle = "#6b7f99";
    ctx.font = "11px system-ui";
    ctx.fillText("No traffic recorded in the last hour", 8, 16);
  }
}

// ---------- network map ----------
const map = (() => {
  const cv = $("#map");
  const nodes = new Map(); // mac -> node
  let particles = [];
  let hub = { x: 0, y: 0 }, router = { x: 0, y: 0 }, cloud = { x: 0, y: 0 };
  let hover = null;
  let crowded = false;
  let scale = 1;     // node size factor for small screens
  let compact = false; // phone-size map: labels only on focus/activity
  let W = 0, H = 0;
  let data = null;

  function sync(s) {
    data = s;
    const devs = s.devices.filter((d) => !d.self)
      .sort((a, b) => a.first_seen - b.first_seen || a.mac.localeCompare(b.mac));
    const seen = new Set();
    devs.forEach((d, i) => {
      seen.add(d.mac);
      let n = nodes.get(d.mac);
      if (!n) { n = { x: hub.x, y: hub.y, dev: d }; nodes.set(d.mac, n); }
      n.dev = d;
      n.index = i;
    });
    for (const k of [...nodes.keys()]) if (!seen.has(k)) nodes.delete(k);
    layout();
  }

  function layout() {
    W = cv.clientWidth; H = cv.clientHeight;
    cloud = { x: W / 2, y: Math.max(30, H * 0.07) };
    router = { x: W / 2, y: Math.max(90, H * 0.2) };
    hub = { x: W / 2, y: H * 0.57 };
    const count = nodes.size;
    // Leave room for labels at the bottom and the sides.
    // Bottom margin: label (2 lines) + the legend strip.
    const ry = Math.max(80, Math.min(H * 0.33, H - hub.y - 78));
    const rx = Math.max(90, Math.min(W / 2 - 70, ry * 2.1));
    // Crowded? Alternate devices between an outer and an inner ring.
    const twoRings = count > Math.max(8, Math.floor((rx + ry) / 45));
    crowded = twoRings;
    scale = Math.max(0.62, Math.min(1, Math.min(W / 900, H / 520)));
    compact = W < 640;
    const a0 = -Math.PI / 2 + 0.55, a1 = (3 * Math.PI) / 2 - 0.55;
    // Keep the spot straight below the gateway free: that's where its label is.
    const gap = count > 12 ? 0.45 : 1.3, bottom = Math.PI / 2;
    for (const n of nodes.values()) {
      let a = a0 + (a1 - a0 - gap) * ((n.index + 0.5) / Math.max(1, count));
      if (a > bottom - gap / 2) a += gap;
      const k = twoRings && n.index % 2 ? 0.66 : 1;
      n.tx = hub.x + rx * k * Math.cos(a);
      n.ty = hub.y + ry * k * Math.sin(a);
    }
  }

  function spawn(from, to, color, inten, key) {
    if (Math.random() < inten * inten * 0.55) {
      particles.push({ from, to, color, t: 0, speed: 0.35 + Math.random() * 0.35 + inten * 0.4, key });
    }
  }

  function edge(a, b, downBps, upBps, dim, dashed) {
    const di = intensity(downBps), ui = intensity(upBps);
    const act = Math.max(di, ui);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.setLineDash(dashed ? [3, 5] : []);
    ctx.strokeStyle = dim ? "rgba(51,65,85,0.35)" : `rgba(56,189,248,${0.12 + act * 0.5})`;
    ctx.lineWidth = 1 + act * 2.5;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  let ctx = null;
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    let w, h;
    [ctx, w, h] = setupCanvas(cv);
    if (w !== W || h !== H) layout();
    drawGrid(now);
    if (!data) { requestAnimationFrame(frame); return; }

    // ease nodes toward targets
    for (const n of nodes.values()) {
      n.x += (n.tx - n.x) * Math.min(1, dt * 4);
      n.y += (n.ty - n.y) * Math.min(1, dt * 4);
    }

    const T = data.totals;
    edge(cloud, router, T.down, T.up, false, false);
    edge(router, hub, T.down, T.up, false, false);
    spawn(cloud, router, COLORS.down, intensity(T.down));
    spawn(router, hub, COLORS.down, intensity(T.down));
    spawn(hub, router, COLORS.up, intensity(T.up));
    spawn(router, cloud, COLORS.up, intensity(T.up));

    for (const n of nodes.values()) {
      const d = n.dev;
      edge(hub, n, d.down, d.up, !d.online, !d.routed);
      if (!d.blocked) {
        spawn(hub, n, COLORS.down, intensity(d.down));
        spawn(n, hub, COLORS.up, intensity(d.up));
      }
    }

    // particles
    particles = particles.filter((p) => (p.t += dt * p.speed) < 1);
    if (particles.length > 900) particles = particles.slice(-900);
    for (const p of particles) {
      const x = p.from.x + (p.to.x - p.from.x) * p.t;
      const y = p.from.y + (p.to.y - p.from.y) * p.t;
      const tx = p.from.x + (p.to.x - p.from.x) * Math.max(0, p.t - 0.06);
      const ty = p.from.y + (p.to.y - p.from.y) * Math.max(0, p.t - 0.06);
      ctx.strokeStyle = p.color;
      ctx.globalAlpha = 0.5;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = p.color;
      ctx.shadowColor = p.color;
      ctx.shadowBlur = 10;
      ctx.beginPath(); ctx.arc(x, y, 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0;
    }

    drawSpecial(cloud, "🌐", "Internet", "", 22, now);
    drawSpecial(router, "📶", data.router.name || "Router", data.router.ip, 24, now);
    const gw = data.devices.find((d) => d.self);
    drawHub(now, gw);
    for (const n of nodes.values()) drawNode(n, now);
    requestAnimationFrame(frame);
  }

  function drawGrid(now) {
    ctx.strokeStyle = "rgba(56,189,248,0.04)";
    ctx.lineWidth = 1;
    const step = 40;
    for (let x = (W / 2) % step; x < W; x += step) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 0; y < H; y += step) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
    // radar pulse from the hub
    const r = ((now / 30) % 400);
    ctx.strokeStyle = `rgba(34,211,238,${0.12 * (1 - r / 400)})`;
    ctx.beginPath(); ctx.arc(hub.x, hub.y, r, 0, Math.PI * 2); ctx.stroke();
  }

  function glowCircle(x, y, r, stroke, fill, dashed, blur = 14) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.setLineDash(dashed ? [4, 4] : []);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.shadowColor = stroke;
    ctx.shadowBlur = blur;
    ctx.stroke();
    ctx.shadowBlur = 0;
    ctx.setLineDash([]);
  }

  function label(x, y, text, sub, color = COLORS.text, max = 20) {
    ctx.textAlign = "center";
    ctx.font = "600 11px Inter, system-ui, sans-serif";
    const t = text.length > max ? text.slice(0, max - 1) + "…" : text;
    // Dark halo so overlapping lines and labels stay readable.
    ctx.lineWidth = 4;
    ctx.strokeStyle = "rgba(5,10,20,0.85)";
    ctx.strokeText(t, x, y);
    ctx.fillStyle = color;
    ctx.fillText(t, x, y);
    if (sub) {
      ctx.font = "10px ui-monospace, monospace";
      ctx.fillStyle = "#6b7f99";
      ctx.fillText(sub, x, y + 13);
    }
  }

  function emoji(x, y, e, size) {
    ctx.font = `${size}px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(e, x, y + 1);
    ctx.textBaseline = "alphabetic";
  }

  function drawSpecial(p, e, name, sub, r) {
    glowCircle(p.x, p.y, r, "rgba(129,140,248,0.8)", "rgba(15,23,42,0.95)", false, 12);
    emoji(p.x, p.y, e, r * 0.85);
    ctx.textAlign = "left";
    ctx.font = "600 11px Inter, system-ui";
    ctx.fillStyle = COLORS.text;
    ctx.fillText(name, p.x + r + 10, p.y + 1);
    if (sub) { ctx.font = "10px ui-monospace, monospace"; ctx.fillStyle = "#6b7f99"; ctx.fillText(sub, p.x + r + 10, p.y + 14); }
  }

  function drawHub(now, gw) {
    const r = 36;
    // rotating outer ring
    ctx.save();
    ctx.translate(hub.x, hub.y);
    ctx.rotate(now / 3000);
    ctx.setLineDash([10, 8]);
    ctx.strokeStyle = "rgba(34,211,238,0.5)";
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(0, 0, r + 10, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
    ctx.setLineDash([]);
    // hexagon core
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 3) * i + Math.PI / 6;
      ctx[i ? "lineTo" : "moveTo"](hub.x + r * Math.cos(a), hub.y + r * Math.sin(a));
    }
    ctx.closePath();
    ctx.fillStyle = "rgba(8,20,38,0.96)";
    ctx.fill();
    ctx.strokeStyle = COLORS.down;
    ctx.lineWidth = 2;
    ctx.shadowColor = COLORS.down;
    ctx.shadowBlur = 22;
    ctx.stroke();
    ctx.shadowBlur = 0;
    emoji(hub.x, hub.y, "🛡️", 26);
    label(hub.x, hub.y + r + 26, gw ? gw.label : "Gateway",
      `${data.self_ip} · ${data.mode}`, COLORS.down);
  }

  function drawNode(n, now) {
    const d = n.dev;
    const act = Math.max(intensity(d.down), intensity(d.up));
    const r = (19 + act * 7) * scale;
    const stroke = d.blocked ? COLORS.bad : !d.approved ? "#fbbf24" : !d.online ? COLORS.muted
      : d.routed ? COLORS.down : "rgba(148,163,184,0.7)";
    const isSel = selected === d.mac, isHover = hover === n;
    if (isSel || isHover) {
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 7 + Math.sin(now / 200) * 1.5, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.globalAlpha = d.online ? 1 : 0.45;
    glowCircle(n.x, n.y, r, stroke, d.blocked ? "rgba(60,10,20,0.95)" : "rgba(8,16,30,0.95)",
      !d.routed && !d.blocked, d.online ? 10 + act * 18 : 0);
    emoji(n.x, n.y, ICON[d.type] || ICON.unknown, 16 * scale);
    if (d.blocked) {
      ctx.strokeStyle = COLORS.bad;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(n.x - r * 0.7, n.y + r * 0.7); ctx.lineTo(n.x + r * 0.7, n.y - r * 0.7); ctx.stroke();
    }
    if (d.bypass) emoji(n.x + r * 0.75, n.y - r * 0.75, "⚡", 12);
    const active = d.up + d.down > 0;
    const focus = isSel || isHover;
    // Crowded maps show the second line only when it matters (traffic, hover, selection).
    const sub = active ? `↓${fmtRate(d.down)} ↑${fmtRate(d.up)}` : (!crowded || focus ? d.ip : "");
    if (!compact || focus || active) {
      label(n.x, n.y + r + 15, d.label, compact && !focus ? "" : sub, COLORS.text, crowded && !focus ? 14 : 22);
    }
    ctx.globalAlpha = 1;
  }

  function hit(e) {
    const rect = cv.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    for (const n of nodes.values()) if (Math.hypot(n.x - x, n.y - y) < 28 * scale + 4) return n;
    if (Math.hypot(hub.x - x, hub.y - y) < 40) return "hub";
    return null;
  }
  cv.addEventListener("mousemove", (e) => {
    const h = hit(e);
    hover = h && h !== "hub" ? h : null;
    cv.style.cursor = h ? "pointer" : "default";
  });
  cv.addEventListener("click", (e) => {
    const h = hit(e);
    if (h === "hub") { const gw = data?.devices.find((d) => d.self); if (gw) select(gw.mac); }
    else if (h) select(h.dev.mac);
  });
  window.addEventListener("resize", () => { layout(); if (snap) drawTotals(); if (selected) drawDeviceChart(); });
  requestAnimationFrame(frame);
  return { sync };
})();

connect();
