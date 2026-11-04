#!/usr/bin/env python3
"""Backend dashboard listrik: CRUD perangkat Modbus TCP + polling realtime 3 fasa.

Browser tidak bisa bicara Modbus TCP, jadi server ini yang polling meter lalu
menyajikan hasilnya lewat /api/devices. Konfigurasi perangkat disimpan di SQLite.
"""
import argparse
import inspect
import json
import logging
import math
import mimetypes
import re
import sqlite3
import struct
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import mysql.connector
from pymodbus.client import ModbusTcpClient

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

BASE = Path(__file__).parent
STATIC = BASE / "static"
DB_PATH = BASE / "meters.db"
TIMEOUT_S = 3.0

# Peta register per model meter (float32, alamat 0-based). Dibaca per blok supaya satu siklus
# hanya beberapa request per meter. "scale" mengubah satuan meter ke W / VA yang dipakai dashboard.
PROFILES = {
    # Eastron SDM630 (input register, satuan W / VA). Hasil scan meter 192.168.1.110.
    "sdm630": {
        "label": "Eastron SDM630",
        "blocks": [(0, 24), (52, 6), (70, 2)],
        "phase": {"v": (0, 2, 4), "i": (6, 8, 10), "p": (12, 14, 16), "s": (18, 20, 22)},
        "total": {"p_total": 52, "s_total": 56, "freq": 70},
        "scale": 1,
    },
    # Schneider PM5330 (holding register, alamat = nomor manual - 1, satuan kW / kVA).
    # Arus 3000-3005, tegangan L-N 3028-3033, kW 3054-3060, kVA 3070-3076, frekuensi 3110.
    "pm5330": {
        "label": "Schneider PM5330",
        "blocks": [(2999, 112)],
        "phase": {"v": (3027, 3029, 3031), "i": (2999, 3001, 3003), "p": (3053, 3055, 3057), "s": (3069, 3071, 3073)},
        "total": {"p_total": 3059, "s_total": 3075, "freq": 3109},
        "scale": 1000,
    },
}
DEFAULT_MODEL = "sdm630"
SCALED_KEYS = ("p", "s", "p_total", "s_total")  # besaran daya; v, i, freq tidak diskalakan

FUNCTIONS = ("input", "holding")
BYTE_ORDERS = ("big", "swap", "little", "badc")

FIELDS = ("name", "area", "model", "host", "port", "unit_id", "function", "byte_order", "interval_s", "enabled")

DEFAULT_DB_NAME = "ois_sosro_pw"
CONN_FIELDS = ("name", "host", "port", "db_user", "db_password", "db_name", "store_interval_s")
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")  # dipakai sebagai identifier SQL, jadi dibatasi ketat
MAX_RETRY_S = 30

PHASE_COLUMNS = [f"{k}{n}" for k in ("v", "i", "p", "s") for n in (1, 2, 3)]
READINGS_DDL = f"""CREATE TABLE IF NOT EXISTS power_readings (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    recorded_at DATETIME(3) NOT NULL,
    device_id INT NOT NULL,
    device_name VARCHAR(100) NOT NULL,
    host VARCHAR(255) NOT NULL,
    unit_id SMALLINT NOT NULL,
    {', '.join(f'{c} FLOAT NULL' for c in PHASE_COLUMNS)},
    p_total FLOAT NULL, s_total FLOAT NULL, freq FLOAT NULL,
    INDEX idx_device_time (device_id, recorded_at),
    INDEX idx_time (recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
READINGS_INSERT = (
    "INSERT INTO power_readings (recorded_at, device_id, device_name, host, unit_id, "
    f"{', '.join(PHASE_COLUMNS)}, p_total, s_total, freq) VALUES ({', '.join(['%s'] * (5 + len(PHASE_COLUMNS) + 3))})"
)


# ---------- database ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, area TEXT NOT NULL DEFAULT '',
                host TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 502,
                unit_id INTEGER NOT NULL DEFAULT 1,
                function TEXT NOT NULL DEFAULT 'input',
                byte_order TEXT NOT NULL DEFAULT 'big',
                interval_s REAL NOT NULL DEFAULT 2,
                enabled INTEGER NOT NULL DEFAULT 1)"""
        )
        if "model" not in {r["name"] for r in con.execute("PRAGMA table_info(devices)")}:
            con.execute(f"ALTER TABLE devices ADD COLUMN model TEXT NOT NULL DEFAULT '{DEFAULT_MODEL}'")
        con.execute(
            """CREATE TABLE IF NOT EXISTS db_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, host TEXT NOT NULL,
                port INTEGER NOT NULL DEFAULT 3306,
                db_user TEXT NOT NULL, db_password TEXT NOT NULL DEFAULT '',
                db_name TEXT NOT NULL DEFAULT 'ois_sosro_pw',
                store_interval_s REAL NOT NULL DEFAULT 60,
                active INTEGER NOT NULL DEFAULT 0)"""
        )


def validate(body):
    """Return (clean_dict, error_message)."""
    try:
        clean = {
            "name": str(body.get("name", "")).strip(),
            "area": str(body.get("area", "")).strip(),
            "model": str(body.get("model", DEFAULT_MODEL)),
            "host": str(body.get("host", "")).strip(),
            "port": int(body.get("port", 502)),
            "unit_id": int(body.get("unit_id", 1)),
            "function": str(body.get("function", "input")),
            "byte_order": str(body.get("byte_order", "big")),
            "interval_s": float(body.get("interval_s", 2)),
            "enabled": 1 if body.get("enabled", True) else 0,
        }
    except (TypeError, ValueError):
        return None, "Port, Unit ID, dan interval harus berupa angka."
    if not clean["name"]:
        return None, "Nama titik ukur wajib diisi."
    if clean["model"] not in PROFILES:
        return None, "Model meter tidak dikenal."
    if not clean["host"]:
        return None, "IP / host wajib diisi."
    if not 1 <= clean["port"] <= 65535:
        return None, "Port harus 1–65535."
    if not 0 <= clean["unit_id"] <= 247:
        return None, "Unit ID harus 0–247."
    if clean["function"] not in FUNCTIONS:
        return None, "Function harus input atau holding."
    if clean["byte_order"] not in BYTE_ORDERS:
        return None, "Urutan byte tidak dikenal."
    if not 0.5 <= clean["interval_s"] <= 3600:
        return None, "Interval harus 0,5–3600 detik."
    return clean, None


# ---------- modbus ----------
def decode_float32(w0, w1, order):
    if order == "swap":
        raw = w1.to_bytes(2, "big") + w0.to_bytes(2, "big")
    elif order == "little":
        raw = w1.to_bytes(2, "little") + w0.to_bytes(2, "little")
    elif order == "badc":
        raw = w0.to_bytes(2, "little") + w1.to_bytes(2, "little")
    else:
        raw = w0.to_bytes(2, "big") + w1.to_bytes(2, "big")
    value = struct.unpack(">f", raw)[0]
    return value if math.isfinite(value) else None  # NaN/inf tidak valid di JSON


def make_reader(client, function):
    method = getattr(client, "read_input_registers" if function == "input" else "read_holding_registers")
    params = inspect.signature(method).parameters
    unit_key = next((k for k in ("device_id", "slave", "unit") if k in params), None)

    def read(address, count, unit):
        kwargs = {unit_key: unit} if unit_key else {}
        result = method(address, count=count, **kwargs)
        if result.isError():
            raise RuntimeError(f"Modbus error @{address}: {result}")
        return result.registers

    return read


def read_meter(client, dev):
    if not client.connected and not client.connect():
        raise ConnectionError(f"gagal konek ke {dev['host']}:{dev['port']}")
    profile = PROFILES[dev.get("model") or DEFAULT_MODEL]
    read = make_reader(client, dev["function"])
    words = {}
    for start, count in profile["blocks"]:
        for offset, word in enumerate(read(start, count, dev["unit_id"])):
            words[start + offset] = word

    def f32(addr):
        return decode_float32(words[addr], words[addr + 1], dev["byte_order"])

    data = {key: [f32(a) for a in regs] for key, regs in profile["phase"].items()}
    data.update({key: f32(addr) for key, addr in profile["total"].items()})
    scale = profile["scale"]
    if scale != 1:
        for key in SCALED_KEYS:
            v = data[key]
            data[key] = [x * scale if x is not None else None for x in v] if isinstance(v, list) else (
                v * scale if v is not None else None)
    return data


# ---------- poller ----------
class Poller:
    """Satu thread per perangkat aktif; hasil terakhir disimpan di memori."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}    # id -> {"data", "ts", "error"}
        self.threads = {}  # id -> (thread, stop_event)

    def start(self, dev):
        stop = threading.Event()
        thread = threading.Thread(target=self._loop, args=(dev, stop), daemon=True)
        self.threads[dev["id"]] = (thread, stop)
        thread.start()

    def stop(self, dev_id):
        entry = self.threads.pop(dev_id, None)
        if entry:
            entry[1].set()
        with self.lock:
            self.state.pop(dev_id, None)

    def restart(self, dev):
        self.stop(dev["id"])
        if dev["enabled"]:
            self.start(dev)

    def snapshot(self, dev_id):
        with self.lock:
            return dict(self.state.get(dev_id, {}))

    def _set(self, dev_id, stop, **kv):
        with self.lock:
            if not stop.is_set():  # jangan menulis ulang state perangkat yang sudah dihapus
                self.state.setdefault(dev_id, {}).update(kv)

    def _loop(self, dev, stop):
        client = ModbusTcpClient(dev["host"], port=dev["port"], timeout=TIMEOUT_S)
        while not stop.is_set():
            began = time.monotonic()
            try:
                data = read_meter(client, dev)
                self._set(dev["id"], stop, data=data, ts=time.time(), error=None)
            except Exception as exc:
                client.close()
                self._set(dev["id"], stop, error=str(exc) or exc.__class__.__name__)
            stop.wait(max(0.2, dev["interval_s"] - (time.monotonic() - began)))
        client.close()


POLLER = Poller()


def list_devices():
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM devices ORDER BY id")]
    for row in rows:
        row["enabled"] = bool(row["enabled"])
        snap = POLLER.snapshot(row["id"])
        fresh = snap.get("ts") and time.time() - snap["ts"] < max(10, row["interval_s"] * 3)
        row["live"] = snap.get("data") if fresh else None
        row["ts"] = snap.get("ts")
        row["error"] = snap.get("error") if row["enabled"] else None
    return rows


def get_device(dev_id):
    with db() as con:
        row = con.execute("SELECT * FROM devices WHERE id=?", (dev_id,)).fetchone()
    return dict(row) if row else None


# ---------- koneksi database (MySQL) ----------
def validate_conn(body, existing=None):
    """Return (clean_dict, error_message). Password kosong saat edit = pertahankan yang lama."""
    try:
        clean = {
            "name": str(body.get("name", "")).strip(),
            "host": str(body.get("host", "")).strip(),
            "port": int(body.get("port", 3306)),
            "db_user": str(body.get("db_user", "")).strip(),
            "db_password": str(body.get("db_password", "")),
            "db_name": str(body.get("db_name") or DEFAULT_DB_NAME).strip(),
            "store_interval_s": float(body.get("store_interval_s", 60)),
        }
    except (TypeError, ValueError):
        return None, "Port dan interval simpan harus berupa angka."
    if existing and not clean["db_password"]:
        clean["db_password"] = existing["db_password"]
    if not clean["name"]:
        return None, "Nama koneksi wajib diisi."
    if not clean["host"]:
        return None, "Host server wajib diisi."
    if not 1 <= clean["port"] <= 65535:
        return None, "Port harus 1–65535."
    if not clean["db_user"]:
        return None, "User database wajib diisi."
    if not DB_NAME_RE.match(clean["db_name"]):
        return None, "Nama database hanya boleh huruf, angka, dan underscore (maks. 64 karakter)."
    if not 1 <= clean["store_interval_s"] <= 86400:
        return None, "Interval simpan harus 1–86400 detik."
    return clean, None


def public_conn(row):
    """Password tidak pernah dikirim ke browser."""
    out = {k: v for k, v in row.items() if k != "db_password"}
    out["has_password"] = bool(row["db_password"])
    out["active"] = bool(row["active"])
    return out


def get_connection(conn_id):
    with db() as con:
        row = con.execute("SELECT * FROM db_connections WHERE id=?", (conn_id,)).fetchone()
    return dict(row) if row else None


def get_active_connection():
    with db() as con:
        row = con.execute("SELECT * FROM db_connections WHERE active=1").fetchone()
    return dict(row) if row else None


def mysql_connect(cfg, with_db=True):
    kwargs = dict(host=cfg["host"], port=cfg["port"], user=cfg["db_user"], password=cfg["db_password"],
                  connection_timeout=5)
    if with_db:
        kwargs["database"] = cfg["db_name"]
    return mysql.connector.connect(**kwargs)


def test_connection(cfg):
    """Cek login ke server dan keberadaan database tanpa mengubah apa pun."""
    try:
        con = mysql_connect(cfg, with_db=False)
    except mysql.connector.Error as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cur = con.cursor()
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (cfg["db_name"],))
        return {"ok": True, "version": version, "database_exists": cur.fetchone()[0] > 0}
    except mysql.connector.Error as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        con.close()


def ensure_schema(cfg):
    """Buat database (kalau belum ada) dan tabel penyimpanan. Dipanggil saat koneksi diaktifkan."""
    con = mysql_connect(cfg, with_db=False)
    try:
        cur = con.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{cfg['db_name']}` CHARACTER SET utf8mb4")
        cur.execute(f"USE `{cfg['db_name']}`")
        cur.execute(READINGS_DDL)
        con.commit()
    finally:
        con.close()


class Storer:
    """Menyimpan pembacaan terakhir semua meter ke koneksi aktif tiap store_interval_s."""

    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()  # di-set saat koneksi aktif berubah
        self.info = {"connection_id": None, "state": "idle", "error": None, "last_write": None, "rows_total": 0}

    def reload(self):
        self.wake.set()

    def status(self):
        with self.lock:
            return dict(self.info)

    def _set(self, **kv):
        with self.lock:
            self.info.update(kv)

    def _rows(self):
        rows = []
        for d in list_devices():
            live = d["live"]
            if not (d["enabled"] and live):
                continue
            values = [live[k][n] for k in ("v", "i", "p", "s") for n in range(3)]
            rows.append((datetime.fromtimestamp(d["ts"]), d["id"], d["name"], d["host"], d["unit_id"],
                         *values, live["p_total"], live["s_total"], live["freq"]))
        return rows

    def run(self):
        conn, cfg, next_at = None, None, 0.0
        while True:
            if self.wake.is_set():
                self.wake.clear()
                if conn:
                    conn.close()
                conn, cfg, next_at = None, get_active_connection(), 0.0
                self._set(connection_id=cfg["id"] if cfg else None, state="connecting" if cfg else "idle",
                          error=None, last_write=None, rows_total=0)
            if cfg and time.monotonic() >= next_at:
                retry = cfg["store_interval_s"]
                try:
                    conn = conn or mysql_connect(cfg)
                    rows = self._rows()
                    if rows:
                        conn.cursor().executemany(READINGS_INSERT, rows)
                        conn.commit()
                    with self.lock:
                        self.info.update(state="ok", error=None, rows_total=self.info["rows_total"] + len(rows))
                        if rows:
                            self.info["last_write"] = time.time()
                except Exception as exc:
                    if conn:
                        conn.close()
                    conn = None
                    self._set(state="error", error=str(exc) or exc.__class__.__name__)
                    retry = min(retry, MAX_RETRY_S)  # server pabrik putus: coba lagi lebih cepat
                next_at = time.monotonic() + retry
            self.wake.wait(1)


STORER = Storer()


# ---------- http ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # request biasa tidak perlu dicatat

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def _dev_id(self, path):
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "devices"] and parts[2].isdigit():
            return int(parts[2])
        return None

    def _conn_route(self, method, path):
        """Rute /api/db-connections/...; return True kalau path ini milik koneksi database."""
        parts = path.strip("/").split("/")
        if parts[:2] != ["api", "db-connections"]:
            return False
        rest = parts[2:]
        conn_id = int(rest[0]) if rest and rest[0].isdigit() else None
        existing = get_connection(conn_id) if conn_id else None

        if method == "GET" and not rest:
            with db() as con:
                rows = [public_conn(dict(r)) for r in con.execute("SELECT * FROM db_connections ORDER BY id")]
            self._json(200, {"connections": rows, "status": STORER.status()})
        elif method == "POST" and rest == ["test"]:
            body = self._body() or {}
            old = get_connection(int(body["id"])) if str(body.get("id", "")).isdigit() else None
            clean, err = validate_conn(body, old)
            self._json(400 if err else 200, {"error": err} if err else test_connection(clean))
        elif method == "POST" and not rest:
            clean, err = validate_conn(self._body() or {})
            if err:
                return self._json(400, {"error": err}) or True
            with db() as con:
                cur = con.execute(
                    f"INSERT INTO db_connections ({','.join(CONN_FIELDS)}) VALUES ({','.join('?' * len(CONN_FIELDS))})",
                    [clean[f] for f in CONN_FIELDS],
                )
            self._json(201, public_conn(get_connection(cur.lastrowid)))
        elif conn_id is None or not existing:
            self._json(404, {"error": "koneksi tidak ditemukan"})
        elif method == "PUT" and len(rest) == 1:
            clean, err = validate_conn(self._body() or {}, existing)
            if err:
                return self._json(400, {"error": err}) or True
            with db() as con:
                con.execute(f"UPDATE db_connections SET {','.join(f + '=?' for f in CONN_FIELDS)} WHERE id=?",
                            [clean[f] for f in CONN_FIELDS] + [conn_id])
            if existing["active"]:
                STORER.reload()
            self._json(200, public_conn(get_connection(conn_id)))
        elif method == "POST" and rest[1:] == ["activate"]:
            try:
                ensure_schema(existing)
            except mysql.connector.Error as exc:
                return self._json(400, {"error": f"Gagal menyiapkan database: {exc}"}) or True
            with db() as con:
                con.execute("UPDATE db_connections SET active = (id=?)", (conn_id,))
            STORER.reload()
            self._json(200, public_conn(get_connection(conn_id)))
        elif method == "POST" and rest[1:] == ["deactivate"]:
            with db() as con:
                con.execute("UPDATE db_connections SET active=0 WHERE id=?", (conn_id,))
            STORER.reload()
            self._json(200, public_conn(get_connection(conn_id)))
        elif method == "DELETE" and len(rest) == 1:
            with db() as con:
                con.execute("DELETE FROM db_connections WHERE id=?", (conn_id,))
            if existing["active"]:
                STORER.reload()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "tidak ditemukan"})
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if self._conn_route("GET", path):
            return
        if path == "/api/devices":
            return self._json(200, list_devices())
        if path.startswith("/api/"):
            return self._json(404, {"error": "tidak ditemukan"})
        target = (STATIC / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self._json(404, {"error": "tidak ditemukan"})
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self._conn_route("POST", urlparse(self.path).path):
            return
        if urlparse(self.path).path != "/api/devices":
            return self._json(404, {"error": "tidak ditemukan"})
        clean, err = validate(self._body() or {})
        if err:
            return self._json(400, {"error": err})
        with db() as con:
            cur = con.execute(
                f"INSERT INTO devices ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                [clean[f] for f in FIELDS],
            )
        dev = get_device(cur.lastrowid)
        if dev["enabled"]:
            POLLER.start(dev)
        self._json(201, dev)

    def do_PUT(self):
        if self._conn_route("PUT", urlparse(self.path).path):
            return
        dev_id = self._dev_id(urlparse(self.path).path)
        if dev_id is None or not get_device(dev_id):
            return self._json(404, {"error": "perangkat tidak ditemukan"})
        clean, err = validate(self._body() or {})
        if err:
            return self._json(400, {"error": err})
        with db() as con:
            con.execute(
                f"UPDATE devices SET {','.join(f + '=?' for f in FIELDS)} WHERE id=?",
                [clean[f] for f in FIELDS] + [dev_id],
            )
        dev = get_device(dev_id)
        POLLER.restart(dev)
        self._json(200, dev)

    def do_DELETE(self):
        if self._conn_route("DELETE", urlparse(self.path).path):
            return
        dev_id = self._dev_id(urlparse(self.path).path)
        if dev_id is None or not get_device(dev_id):
            return self._json(404, {"error": "perangkat tidak ditemukan"})
        POLLER.stop(dev_id)
        with db() as con:
            con.execute("DELETE FROM devices WHERE id=?", (dev_id,))
        self._json(200, {"ok": True})


def main():
    parser = argparse.ArgumentParser(description="Dashboard listrik Modbus TCP")
    parser.add_argument("--host", default="127.0.0.1", help="alamat bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    init_db()
    for dev in list_devices():
        if dev["enabled"]:
            POLLER.start(dev)
    threading.Thread(target=STORER.run, daemon=True).start()
    STORER.reload()  # muat koneksi aktif (kalau ada) dari konfigurasi tersimpan

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDihentikan.")


if __name__ == "__main__":
    main()
