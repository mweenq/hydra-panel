#!/usr/bin/env python3
"""hydra-node — агент ноды.

Ставится установщиком, общается с панелью только по mTLS (порт 8443).
Умеет: принять конфиг, проверить его, перезапустить движок, отдать статистику.

Зависимостей нет — только стандартная библиотека Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0"
ROOT = Path(os.environ.get("HYDRA_NODE_HOME", "/opt/hydra-node"))
CERTS = ROOT / "certs"
AGENT = ROOT / "agent"
STATE = ROOT / "state.json"
LOG_DIR = ROOT / "logs"

XRAY_DIR = ROOT / "xray"
HY_DIR = ROOT / "hysteria"
SBOX_DIR = ROOT / "singbox"

XRAY_API = "127.0.0.1:10085"
HY2_STATS = "http://127.0.0.1:9999/traffic"
HY2_SECRET = "hydra-stats"

_apply_lock = threading.Lock()
_started_at = time.time()


# ---------------------------------------------------------------- утилиты
def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default if default is not None else {}


def write_atomic(path: Path, data: str) -> bool:
    """Пишет файл, только если содержимое изменилось. True — если изменилось."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == data:
                return False
        except OSError:
            pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
    return True


def systemctl(action: str, unit: str) -> tuple[bool, str]:
    if not shutil.which("systemctl"):
        return False, "systemctl not found"
    r = subprocess.run(["systemctl", action, unit], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def unit_exists(unit: str) -> bool:
    return Path("/etc/systemd/system", unit).exists() or Path("/lib/systemd/system", unit).exists()


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


# ---------------------------------------------------------------- статистика
def xray_stats() -> dict[str, dict]:
    """Читает и сбрасывает счётчики Xray по каждому email."""
    binary = shutil.which("xray") or str(XRAY_DIR / "xray")
    if not Path(binary).exists():
        return {}
    code, out = run([binary, "api", "statsquery", "--server=" + XRAY_API,
                     "-pattern", "user>>>", "-reset"], timeout=15)
    if code != 0:
        return {}
    try:
        data = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {}
    users: dict[str, dict] = {}
    for item in data.get("stat") or []:
        name = item.get("name", "")
        m = re.match(r"user>>>(.+?)>>>traffic>>>(uplink|downlink)$", name)
        if not m:
            continue
        key, direction = m.group(1), m.group(2)
        try:
            value = int(item.get("value") or 0)
        except (TypeError, ValueError):
            value = 0
        users.setdefault(key, {"up": 0, "down": 0})
        if direction == "uplink":
            users[key]["up"] += value
        else:
            users[key]["down"] += value
    return users


def hysteria_stats() -> dict[str, dict]:
    req = urllib.request.Request(HY2_STATS + "?clear=1",
                                 headers={"Authorization": f"Bearer {HY2_SECRET}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode() or "{}")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for user, v in (data or {}).items():
        out[f"{user}|hy2"] = {"up": int(v.get("tx") or 0), "down": int(v.get("rx") or 0)}
    return out


def collect_stats() -> dict:
    users = {}
    try:
        users.update(xray_stats())
    except Exception as e:  # noqa: BLE001
        log(f"xray stats: {e}")
    try:
        users.update(hysteria_stats())
    except Exception as e:  # noqa: BLE001
        log(f"hy2 stats: {e}")
    return {"users": users}


# ---------------------------------------------------------------- конфиги
def apply_xray(cfg: dict | None) -> dict:
    conf_path = XRAY_DIR / "config.json"
    if not cfg:
        if conf_path.exists():
            systemctl("stop", "hydra-xray.service")
            conf_path.unlink()
        return {"xray": "off"}
    text = json.dumps(cfg, ensure_ascii=False, indent=2)
    changed = write_atomic(conf_path, text)

    binary = shutil.which("xray") or str(XRAY_DIR / "xray")
    code, out = run([binary, "run", "-test", "-c", str(conf_path)], timeout=25)
    if code != 0:
        log(f"xray config test failed: {out[:400]}")
        return {"xray": "invalid", "error": out[-400:]}
    if changed or not is_active("hydra-xray.service"):
        ok, err = systemctl("restart", "hydra-xray.service")
        log(f"xray restart: {ok} {err[:200]}")
        return {"xray": "restarted" if ok else f"error: {err[:200]}"}
    return {"xray": "unchanged"}


def apply_hysteria(cfg: str | None) -> dict:
    conf_path = HY_DIR / "config.yaml"
    if not cfg:
        if conf_path.exists():
            systemctl("stop", "hydra-hysteria.service")
            conf_path.unlink()
        return {"hysteria2": "off"}
    changed = write_atomic(conf_path, cfg)
    if changed or not is_active("hydra-hysteria.service"):
        ok, err = systemctl("restart", "hydra-hysteria.service")
        log(f"hysteria restart: {ok} {err[:200]}")
        return {"hysteria2": "restarted" if ok else f"error: {err[:200]}"}
    return {"hysteria2": "unchanged"}


def apply_singbox(cfg: dict | None) -> dict:
    conf_path = SBOX_DIR / "config.json"
    if not cfg:
        if conf_path.exists():
            systemctl("stop", "hydra-singbox.service")
            conf_path.unlink()
        return {"singbox": "off"}
    changed = write_atomic(conf_path, json.dumps(cfg, ensure_ascii=False, indent=2))
    if changed or not is_active("hydra-singbox.service"):
        ok, err = systemctl("restart", "hydra-singbox.service")
        log(f"singbox restart: {ok} {err[:200]}")
        return {"singbox": "restarted" if ok else f"error: {err[:200]}"}
    return {"singbox": "unchanged"}


def is_active(unit: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    code, out = run(["systemctl", "is-active", unit], timeout=8)
    return out.strip() == "active"


def apply_tls(tls: dict) -> dict:
    if not tls:
        return {}
    cert, key = tls.get("cert_pem"), tls.get("key_pem")
    if not cert or not key:
        return {}
    CERTS.mkdir(parents=True, exist_ok=True)
    (CERTS / "fullchain.pem").write_text(cert, encoding="utf-8")
    (CERTS / "privkey.pem").write_text(key, encoding="utf-8")
    try:
        os.chmod(CERTS / "privkey.pem", 0o600)
    except OSError:
        pass
    return {"tls": "updated"}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"hydra-node/{VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log(f"{self.address_string()} {fmt % args}")

    # -- helpers
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _master_ok(self) -> bool:
        cert = self.connection.getpeercert() or {}
        cn = ""
        for rdn in cert.get("subject", ()):  # ((('commonName','hydra-master'),),)
            for key, value in rdn:
                if key == "commonName":
                    cn = value
        if cn != "hydra-master":
            log(f"rejected client cert cn={cn!r}")
            return False
        return True

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 8 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except (ValueError, json.JSONDecodeError):
            return {}

    # -- routes
    def do_GET(self) -> None:  # noqa: N802
        if not self._master_ok():
            self._json(403, {"error": "forbidden"})
            return
        if self.path.startswith("/v1/status"):
            state = read_json(STATE)
            services = {}
            for unit in ("hydra-xray.service", "hydra-hysteria.service", "hydra-singbox.service"):
                if unit_exists(unit):
                    services[unit] = "active" if is_active(unit) else "inactive"
            self._json(200, {
                "ok": True,
                "version": VERSION,
                "engine": state.get("engine", "xray"),
                "uptime_h": round((time.time() - _started_at) / 3600, 2),
                "services": ", ".join(f"{k.split('-')[1].split('.')[0]}={v}" for k, v in services.items()) or "—",
                "node_id": state.get("node_id"),
            })
            return
        if self.path.startswith("/v1/stats"):
            self._json(200, {"ok": True, **collect_stats()})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._master_ok():
            self._json(403, {"error": "forbidden"})
            return
        if self.path.startswith("/v1/apply"):
            payload = self._body()
            with _apply_lock:
                result: dict = {}
                try:
                    result.update(apply_tls(payload.get("tls") or {}))
                    result.update(apply_xray(payload.get("xray")))
                    result.update(apply_hysteria(payload.get("hysteria2")))
                    result.update(apply_singbox(payload.get("singbox")))
                    if result.get("tls") == "updated":
                        # сертификат сменился — движки надо перечитать, даже если конфиг тот же
                        for unit in ("hydra-xray.service", "hydra-hysteria.service",
                                     "hydra-singbox.service"):
                            if unit_exists(unit):
                                systemctl("restart", unit)
                except Exception as e:  # noqa: BLE001
                    log(f"apply failed: {e}")
                    self._json(500, {"ok": False, "error": str(e)})
                    return
            state = read_json(STATE)
            state["engine"] = payload.get("engine", state.get("engine", "xray"))
            state["applied_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_atomic(STATE, json.dumps(state, indent=2))
            self._json(200, {"ok": True, "version": VERSION, "result": result})
            return
        self._json(404, {"error": "not found"})


# ---------------------------------------------------------------- join
def do_join(master: str, token: str, name: str, engine: str, insecure: bool = False) -> int:
    """Генерирует ключ+CSR через openssl, регистрируется на панели, сохраняет сертификат."""
    import socket

    AGENT.mkdir(parents=True, exist_ok=True)
    host = name or socket.gethostname()
    key_path = AGENT / "node.key"
    csr_path = AGENT / "node.csr"
    code, out = run([
        "openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key_path), "-out", str(csr_path), "-subj", f"/CN={host}",
    ], timeout=60)
    if code != 0 or not csr_path.exists():
        log(f"openssl failed: {out[:300]}")
        return 1
    csr_pem = csr_path.read_text(encoding="utf-8")

    body = json.dumps({
        "token": token,
        "csr": csr_pem,
        "engine": engine,
        "version": VERSION,
        "name": host,
    }).encode()
    url = master.rstrip("/") + "/v1/join"
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        log(f"join rejected: {e.code} {e.read().decode()[:300]}")
        return 1
    except Exception as e:  # noqa: BLE001
        log(f"join failed: {e}")
        return 1

    (AGENT / "node.pem").write_text(data["cert_pem"], encoding="utf-8")
    (AGENT / "ca.pem").write_text(data["ca_pem"], encoding="utf-8")
    try:
        os.chmod(key_path, 0o600)
        csr_path.unlink(missing_ok=True)
    except OSError:
        pass
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        "node_id": data["node_id"],
        "name": data.get("name") or host,
        "master": master,
        "engine": engine,
        "joined_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")
    log(f"joined as node #{data['node_id']} ({data.get('name')})")
    return 0


# ---------------------------------------------------------------- main
def serve(port: int) -> int:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(AGENT / "node.pem"), str(AGENT / "node.key"))
    ctx.load_verify_locations(str(AGENT / "ca.pem"))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    log(f"agent listening on :{port} (mTLS)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="hydra-node")
    ap.add_argument("--join", action="store_true")
    ap.add_argument("--master", default="")
    ap.add_argument("--token", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--engine", default="xray")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--serve", action="store_true")
    args = ap.parse_args()

    for d in (ROOT, CERTS, AGENT, LOG_DIR, XRAY_DIR, HY_DIR, SBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)

    if args.join:
        if not args.master or not args.token:
            print("нужны --master и --token", file=sys.stderr)
            return 2
        return do_join(args.master, args.token, args.name, args.engine, args.insecure)

    if args.serve:
        if not (AGENT / "node.pem").exists():
            print("нода не подключена: сначала запустите --join", file=sys.stderr)
            return 2
        return serve(args.port)

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
