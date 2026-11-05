"""SQLite persistence: devices, per-minute usage, domains, events."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices(
  mac TEXT PRIMARY KEY, ip TEXT, hostname TEXT, name TEXT, vendor TEXT,
  first_seen REAL, last_seen REAL, blocked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS usage(
  mac TEXT, minute INTEGER, up INTEGER, down INTEGER, PRIMARY KEY(mac, minute));
CREATE TABLE IF NOT EXISTS domains(
  mac TEXT, domain TEXT, count INTEGER, last REAL, PRIMARY KEY(mac, domain));
CREATE TABLE IF NOT EXISTS events(ts REAL, kind TEXT, mac TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS service_usage(
  mac TEXT, day TEXT, service TEXT, bytes INTEGER, PRIMARY KEY(mac, day, service));
CREATE TABLE IF NOT EXISTS adblock(
  day TEXT, mac TEXT, domain TEXT, count INTEGER, PRIMARY KEY(day, mac, domain));
CREATE TABLE IF NOT EXISTS health(ts REAL, target TEXT, rtt REAL, loss REAL);
CREATE TABLE IF NOT EXISTS throughput(minute INTEGER PRIMARY KEY, peak REAL, avg REAL);
CREATE INDEX IF NOT EXISTS health_ts ON health(ts);
CREATE INDEX IF NOT EXISTS usage_minute ON usage(minute);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(devices)")}
        # Devices that existed before approvals were introduced count as approved.
        for col, ddl in (("bypass", "INTEGER DEFAULT 0"), ("approved", "INTEGER DEFAULT 1"),
                         ("paused_until", "REAL DEFAULT 0")):
            if col not in cols:
                self.db.execute(f"ALTER TABLE devices ADD COLUMN {col} {ddl}")
        dcols = {r["name"] for r in self.db.execute("PRAGMA table_info(domains)")}
        if "first" not in dcols:
            self.db.execute("ALTER TABLE domains ADD COLUMN first REAL")

    def load_devices(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM devices").fetchall()

    def save_devices(self, rows: list[tuple]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO devices(mac, ip, hostname, name, vendor, first_seen, last_seen, blocked,"
                " bypass, approved, paused_until) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mac) DO UPDATE SET"
                " ip=excluded.ip, hostname=excluded.hostname, name=excluded.name,"
                " vendor=excluded.vendor, last_seen=excluded.last_seen, blocked=excluded.blocked,"
                " bypass=excluded.bypass, approved=excluded.approved, paused_until=excluded.paused_until",
                rows,
            )

    def add_usage(self, minute: int, rows: dict[str, list[int]]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO usage VALUES(?,?,?,?) ON CONFLICT(mac, minute) DO UPDATE"
                " SET up=up+excluded.up, down=down+excluded.down",
                [(mac, minute, u, d) for mac, (u, d) in rows.items() if u or d],
            )

    def usage_since(self, since: float, mac: str | None = None) -> list[sqlite3.Row]:
        if mac:
            return self.db.execute(
                "SELECT minute, up, down FROM usage WHERE mac=? AND minute>=? ORDER BY minute",
                (mac, int(since))).fetchall()
        return self.db.execute(
            "SELECT minute, SUM(up) up, SUM(down) down FROM usage WHERE minute>=?"
            " GROUP BY minute ORDER BY minute", (int(since),)).fetchall()

    def totals_since(self, since: float) -> dict[str, list[int]]:
        rows = self.db.execute(
            "SELECT mac, SUM(up) up, SUM(down) down FROM usage WHERE minute>=? GROUP BY mac",
            (int(since),)).fetchall()
        return {r["mac"]: [r["up"], r["down"]] for r in rows}

    def add_domains(self, rows: dict[tuple[str, str], tuple[int, float]]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO domains(mac, domain, count, last, first) VALUES(?,?,?,?,?)"
                " ON CONFLICT(mac, domain) DO UPDATE SET count=count+excluded.count, last=excluded.last",
                [(mac, dom, n, ts, ts) for (mac, dom), (n, ts) in rows.items()],
            )

    def top_domains(self, mac: str, limit: int = 25) -> list[dict]:
        rows = self.db.execute(
            "SELECT domain, count, last FROM domains WHERE mac=? ORDER BY count DESC LIMIT ?",
            (mac, limit)).fetchall()
        return [dict(r) for r in rows]

    # ---------- insights ----------
    def add_service_usage(self, day: str, rows: dict[tuple[str, str], int]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO service_usage VALUES(?,?,?,?) ON CONFLICT(mac, day, service)"
                " DO UPDATE SET bytes=bytes+excluded.bytes",
                [(mac, day, svc, b) for (mac, svc), b in rows.items() if b])

    def services_since(self, day: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT service, mac, SUM(bytes) bytes FROM service_usage WHERE day>=?"
            " GROUP BY service, mac", (day,)).fetchall()

    def add_adblock(self, day: str, rows: dict[tuple[str, str], int]) -> None:
        with self.db:
            self.db.executemany(
                "INSERT INTO adblock VALUES(?,?,?,?) ON CONFLICT(day, mac, domain)"
                " DO UPDATE SET count=count+excluded.count",
                [(day, mac, dom, n) for (mac, dom), n in rows.items()])

    def adblock_by_mac(self, day_from: str) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT mac, SUM(count) n FROM adblock WHERE day>=? GROUP BY mac", (day_from,)).fetchall()
        return {r["mac"]: r["n"] for r in rows}

    def adblock_top(self, day_from: str, mac: str | None = None, limit: int = 10) -> list[dict]:
        q = "SELECT domain, SUM(count) n FROM adblock WHERE day>=?"
        args: list = [day_from]
        if mac:
            q += " AND mac=?"
            args.append(mac)
        q += " GROUP BY domain ORDER BY n DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(q, args).fetchall()]

    def adblock_daily(self, day_from: str) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT day, SUM(count) n FROM adblock WHERE day>=? GROUP BY day", (day_from,)).fetchall()
        return {r["day"]: r["n"] for r in rows}

    def services_for(self, mac: str, day_from: str, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            "SELECT service, SUM(bytes) bytes FROM service_usage WHERE mac=? AND day>=?"
            " GROUP BY service ORDER BY bytes DESC LIMIT ?", (mac, day_from, limit)).fetchall()
        return [dict(r) for r in rows]

    def add_health(self, ts: float, target: str, rtt: float | None, loss: float) -> None:
        self.db.execute("INSERT INTO health VALUES(?,?,?,?)", (ts, target, rtt, loss))

    def health_since(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT ts, target, rtt, loss FROM health WHERE ts>=? ORDER BY ts", (since,)).fetchall()

    def add_throughput(self, minute: int, peak: float, avg: float) -> None:
        self.db.execute(
            "INSERT INTO throughput VALUES(?,?,?) ON CONFLICT(minute) DO UPDATE SET"
            " peak=max(peak, excluded.peak), avg=excluded.avg", (minute, peak, avg))

    def throughput_since(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT minute, peak, avg FROM throughput WHERE minute>=? ORDER BY minute",
            (int(since),)).fetchall()

    def daily_usage(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT strftime('%Y-%m-%d', minute, 'unixepoch', 'localtime') day, mac,"
            " SUM(up) up, SUM(down) down FROM usage WHERE minute>=? GROUP BY day, mac ORDER BY day",
            (int(since),)).fetchall()

    def hourly_usage(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT CAST(strftime('%w', minute, 'unixepoch', 'localtime') AS INTEGER) dow,"
            " CAST(strftime('%H', minute, 'unixepoch', 'localtime') AS INTEGER) hour,"
            " SUM(up + down) bytes FROM usage WHERE minute>=? GROUP BY dow, hour", (int(since),)).fetchall()

    def hourly_split(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT CAST(strftime('%H', minute, 'unixepoch', 'localtime') AS INTEGER) hour,"
            " SUM(up) up, SUM(down) down FROM usage WHERE minute>=? GROUP BY hour", (int(since),)).fetchall()

    def new_domains(self, since: float) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT mac, domain, first, count FROM domains WHERE first>=? ORDER BY first DESC",
            (since,)).fetchall()

    def add_event(self, ts: float, kind: str, mac: str, message: str) -> None:
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (ts, kind, mac, message))

    def recent_events(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def prune(self, days: int = 30) -> None:
        cutoff = time.time() - days * 86400
        with self.db:
            self.db.execute("DELETE FROM usage WHERE minute<?", (int(cutoff),))
            self.db.execute("DELETE FROM events WHERE ts<?", (cutoff,))
            self.db.execute("DELETE FROM health WHERE ts<?", (cutoff,))
            self.db.execute("DELETE FROM throughput WHERE minute<?", (int(cutoff),))
            day = time.strftime("%Y-%m-%d", time.localtime(cutoff))
            self.db.execute("DELETE FROM service_usage WHERE day<?", (day,))
            self.db.execute("DELETE FROM adblock WHERE day<?", (day,))
