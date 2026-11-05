"""HTTP + WebSocket server for the Netwatch dashboard."""
import asyncio
import ipaddress
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as C
from .auth import MIN_LENGTH, Auth
from .engine import Engine
from . import insights
from .setup import SetupChecks, apply_saved_mode, ipv6_router_advertising, load_settings, save_settings
from .telegram import TelegramBot
from .version import __version__
from .adblock import ACTIVE as ADBLOCK_FILE, AdBlocker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("netwatch")
WEB = Path(__file__).resolve().parent.parent / "web"
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
COOKIE = "nw_session"

apply_saved_mode()
engine = Engine()
auth = Auth()
checks = SetupChecks()
bot = TelegramBot(engine)
engine.bot = bot
adblock = AdBlocker(engine)
engine.adblock = adblock
bot.adblock = adblock


def authed(cookies: dict) -> bool:
    return auth.valid_session(cookies.get(COOKIE, ""))


def _login_response() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, auth.session_token(), max_age=30 * 86400, httponly=True, samesite="strict")
    return resp


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ADBLOCK_FILE.touch(exist_ok=True)  # dnsmasq loads it at start
    await engine.start()
    engine.tasks.append(asyncio.create_task(adblock.run()))
    engine.tasks.append(asyncio.create_task(bot.run()))
    engine.tasks.append(asyncio.create_task(bot.weekly_report_loop()))
    try:
        yield
    finally:
        await engine.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_stale_assets(request: Request, call_next):
    # Always revalidate pages and assets (cheap on a LAN, via ETag), so a deploy
    # never leaves a browser running old JS/CSS against new HTML.
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# While the password is the temporary one, or setup isn't finished, only these work.
WELCOME_PATHS = ("/welcome", "/api/password", "/api/logout", "/api/welcome")


def setup_complete() -> bool:
    return bool(load_settings().get("setup_complete"))


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path in ("/login", "/api/login") or path.startswith("/static/"):
        return await call_next(request)
    if not authed(request.cookies):
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")
    if (auth.must_change or not setup_complete()) and not path.startswith(WELCOME_PATHS):
        if path.startswith("/api/"):
            return JSONResponse({"error": "setup_required"}, status_code=403)
        return RedirectResponse("/welcome")
    return await call_next(request)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/login")
async def login_page():
    return FileResponse(WEB / "login.html")


@app.get("/welcome")
async def welcome_page():
    return FileResponse(WEB / "welcome.html")


@app.get("/guide")
async def guide_page():
    # The setup guide now lives inside Settings.
    return RedirectResponse("/settings#setup")


@app.get("/insights")
async def insights_page():
    return FileResponse(WEB / "insights.html")


@app.get("/settings")
async def settings_page():
    return FileResponse(WEB / "settings.html")


class Login(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: Login):
    if auth.check(body.password):
        return _login_response()
    await asyncio.sleep(1.5)  # slow down guessing
    return JSONResponse({"ok": False}, status_code=401)


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


class PasswordChange(BaseModel):
    current: str = ""
    new: str


@app.post("/api/password")
async def change_password(body: PasswordChange):
    # Right after signing in with the temporary password, the session is proof enough.
    if not auth.must_change and not auth.check(body.current):
        await asyncio.sleep(1.5)
        raise HTTPException(403, "Current password is wrong")
    if auth.check(body.new):
        raise HTTPException(400, "Choose a password different from the current one")
    if len(body.new) < MIN_LENGTH:
        raise HTTPException(400, f"New password must be at least {MIN_LENGTH} characters")
    auth.set_password(body.new)
    log.info("dashboard password changed")
    return _login_response()  # keep this browser signed in; others are signed out


@app.get("/api/welcome/state")
async def welcome_state():
    """Everything the first-run pages need: what's left to do and what we detected."""
    found = C.detect()
    saved = load_settings()
    return {
        "must_change_password": auth.must_change,
        "setup_complete": setup_complete(),
        "min_password_length": MIN_LENGTH,
        "network": {
            "iface": C.IFACE, "self_ip": C.SELF_IP, "router_ip": C.ROUTER_IP, "lan": str(C.LAN),
            "router_name": engine.router_name(),
            "router_vendor": engine.router_vendor(),
            "dynamic_ip": found.get("dynamic"),
            "interfaces": C.interfaces(),
        },
        "ipv6_ra": await ipv6_router_advertising(),
        "features": {
            "adblock": bool(saved.get("adblock", False)),
            "require_approval": bool(saved.get("require_approval", False)),
            "telegram": bool(C.TELEGRAM_TOKEN),
        },
    }


class SetupNetwork(BaseModel):
    iface: str
    self_ip: str
    router_ip: str
    lan: str
    router_name: str = ""


class SetupFinish(BaseModel):
    network: SetupNetwork
    adblock: bool = False
    require_approval: bool = False
    telegram_token: str = ""


@app.post("/api/welcome/finish")
async def welcome_finish(body: SetupFinish):
    if auth.must_change:
        raise HTTPException(403, "Choose a new password first")
    net = body.network
    try:
        lan = ipaddress.ip_network(net.lan.strip(), strict=False)
        for ip in (net.self_ip, net.router_ip):
            if ipaddress.ip_address(ip.strip()) not in lan:
                raise ValueError(f"{ip} isn't inside {lan}")
    except ValueError as e:
        raise HTTPException(400, f"Network settings look wrong: {e}")
    if not re.fullmatch(r"[\w.@:-]{1,15}", net.iface.strip()):
        raise HTTPException(400, "That doesn't look like a network interface name")
    network = {"iface": net.iface.strip(), "self_ip": net.self_ip.strip(), "router_ip": net.router_ip.strip(),
               "lan": str(lan), "router_name": net.router_name.strip()[:40]}
    token = body.telegram_token.strip()
    if token and not re.fullmatch(r"\d{5,}:[\w-]{20,}", token):
        raise HTTPException(400, "That doesn't look like a Telegram bot token (it looks like 123456:ABC-...)")
    changes = {"network": network, "adblock": body.adblock, "require_approval": body.require_approval,
               "setup_complete": True}
    if token:
        changes["telegram_token"] = token
    restart = (network["iface"], network["self_ip"], network["router_ip"], network["lan"]) != \
        (C.IFACE, C.SELF_IP, C.ROUTER_IP, str(C.LAN)) or bool(token and token != C.TELEGRAM_TOKEN)
    save_settings(**changes)
    C.ROUTER_NAME = network["router_name"]
    log.info("setup finished (restart=%s)", restart)
    if restart:
        # Network settings are read at start; Docker brings us straight back up.
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
    else:
        engine.settings_changed()
        await adblock.apply()
    return {"ok": True, "restarting": restart}


@app.get("/api/version")
async def version():
    try:
        channel = (Path(C.DATA) / "channel").read_text().strip() or "stable"
    except OSError:
        channel = "stable"
    return {"version": __version__, "channel": channel}


@app.get("/api/setup/status")
async def setup_status(force: bool = False):
    return await checks.status(engine, force)


class GatewayMode(BaseModel):
    enabled: bool


@app.post("/api/setup/gateway")
async def set_gateway(body: GatewayMode):
    if body.enabled:
        st = await checks.status(engine, force=True)
        if not st["static_ip"]:
            raise HTTPException(409, "Give this machine a fixed IP address first (step 1)")
        if st["router_dhcp_on"]:
            raise HTTPException(409, "Your router is still handing out addresses; turn its DHCP server off first (step 2)")
    engine.set_gateway_mode(body.enabled)
    return await checks.status(engine)


@app.get("/api/insights")
async def get_insights(range: str = "7d"):
    if range not in insights.RANGES:
        raise HTTPException(400, "range must be today, 7d or 30d")
    return await asyncio.to_thread(insights.compute, engine, range)


@app.get("/api/telegram")
async def telegram_status():
    return bot.status()


@app.post("/api/telegram/pair-code")
async def telegram_pair_code():
    if not bot.configured:
        raise HTTPException(400, "No bot token yet: add one in Settings")
    return bot.new_pair_code()


class TelegramToken(BaseModel):
    token: str


@app.post("/api/telegram/token")
async def telegram_token(body: TelegramToken):
    token = body.token.strip()
    if not re.fullmatch(r"\d{5,}:[\w-]{20,}", token):
        raise HTTPException(400, "That doesn't look like a Telegram bot token (it looks like 123456:ABC-...)")
    save_settings(telegram_token=token)
    log.info("telegram token saved; restarting to connect the bot")
    asyncio.get_running_loop().call_later(1.0, os._exit, 0)  # Docker restarts us with the new token
    return {"ok": True, "restarting": True}


@app.post("/api/telegram/unpair")
async def telegram_unpair():
    bot.unpair()
    return bot.status()


@app.post("/api/telegram/test")
async def telegram_test():
    if not await bot.send("👋 Test message from Netwatch. Alerts are working."):
        raise HTTPException(400, "Couldn't send. Is the bot paired?")
    return {"ok": True}


class ApprovalSetting(BaseModel):
    require_approval: bool


@app.post("/api/settings/approval")
async def set_approval(body: ApprovalSetting):
    save_settings(require_approval=body.require_approval)
    engine.settings_changed()
    return bot.status()


@app.get("/api/snapshot")
async def snapshot():
    return Response(engine.snapshot_json, media_type="application/json")


def _check_mac(mac: str) -> str:
    mac = mac.lower()
    if not MAC_RE.match(mac) or mac not in engine.devices:
        raise HTTPException(404, "unknown device")
    return mac


class DevicePatch(BaseModel):
    name: str | None = None
    blocked: bool | None = None
    bypass: bool | None = None
    approved: bool | None = None


@app.patch("/api/devices/{mac}")
async def patch_device(mac: str, body: DevicePatch):
    mac = _check_mac(mac)
    try:
        d = engine.update_device(mac, body.name, body.blocked, body.bypass, body.approved)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return engine.device_json(d, time.time())


class Decision(BaseModel):
    verdict: str


@app.post("/api/devices/{mac}/decide")
async def decide_device(mac: str, body: Decision):
    mac = _check_mac(mac)
    if body.verdict not in ("allow", "allow_bypass", "block"):
        raise HTTPException(400, "verdict must be allow, allow_bypass or block")
    try:
        d = engine.decide(mac, body.verdict)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return engine.device_json(d, time.time())


def next_morning(hour: int = 7) -> float:
    t = time.localtime()
    ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 0, 0, 0, 0, -1))
    return ts if ts > time.time() + 60 else ts + 86400


class Pause(BaseModel):
    minutes: int | None = None
    until_morning: bool = False


@app.post("/api/devices/{mac}/pause")
async def pause_device(mac: str, body: Pause):
    mac = _check_mac(mac)
    if body.until_morning:
        until = next_morning()
    elif body.minutes and 1 <= body.minutes <= 24 * 60:
        until = time.time() + body.minutes * 60
    else:
        raise HTTPException(400, "give minutes (1-1440) or until_morning")
    try:
        d = engine.pause(mac, until)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return engine.device_json(d, time.time())


@app.post("/api/devices/{mac}/resume")
async def resume_device(mac: str):
    d = engine.resume(_check_mac(mac))
    return engine.device_json(d, time.time())


@app.get("/api/adblock")
async def adblock_status():
    day = time.strftime("%Y-%m-%d")
    return adblock.status() | {"top_today": engine.store.adblock_top(day, limit=10)}


class AdblockPatch(BaseModel):
    enabled: bool | None = None
    force_dns: bool | None = None
    allowlist: str | list[str] | None = None


@app.post("/api/adblock")
async def adblock_update(body: AdblockPatch):
    if body.enabled is not None and body.enabled != adblock.enabled:
        await adblock.set_enabled(body.enabled)
    if body.force_dns is not None and body.force_dns != adblock.force_dns:
        await adblock.set_force_dns(body.force_dns)
    if body.allowlist is not None:
        await adblock.set_allowlist(body.allowlist)
    engine._build_snapshot()
    return await adblock_status()


@app.post("/api/adblock/refresh")
async def adblock_refresh():
    await adblock.refresh(force=True)
    return await adblock_status()


@app.get("/api/devices/{mac}/history")
async def device_history(mac: str, minutes: int = 60):
    mac = _check_mac(mac)
    minutes = max(5, min(minutes, 60 * 24 * 7))
    rows = engine.store.usage_since(time.time() - minutes * 60, mac)
    return [dict(r) for r in rows]


@app.get("/api/devices/{mac}/domains")
async def device_domains(mac: str):
    return engine.store.top_domains(_check_mac(mac))


@app.get("/api/history")
async def history(minutes: int = 60):
    minutes = max(5, min(minutes, 60 * 24 * 7))
    return [dict(r) for r in engine.store.usage_since(time.time() - minutes * 60)]


@app.websocket("/ws")
async def ws(socket: WebSocket):
    if not authed(socket.cookies):
        await socket.close(code=4401)
        return
    if auth.must_change or not setup_complete():
        await socket.close(code=4403)  # finish /welcome first
        return
    await socket.accept()
    await socket.send_text(engine.snapshot_json)
    engine.clients.add(socket)
    try:
        while True:
            await socket.receive_text()  # keepalive; we only push
    except WebSocketDisconnect:
        pass
    finally:
        engine.clients.discard(socket)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=C.PORT, log_level="warning", ws_ping_interval=20)
