"""Сертификаты: acme.sh, самоподписанные, проверка сроков."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import config, db, security

ACME = Path("/root/.acme.sh/acme.sh")
LIVE = config.CERT_DIR


def acme_available() -> bool:
    return ACME.exists() or shutil.which("acme.sh") is not None


def _acme_bin() -> str:
    if ACME.exists():
        return str(ACME)
    return shutil.which("acme.sh") or "acme.sh"


def issue(domain: str, email: str = "", mode: str = "standalone",
          webroot: str = "/var/www/acme") -> dict:
    """Выпуск сертификата. mode: standalone | webroot | dns."""
    if not acme_available():
        return {"ok": False, "error": "acme.sh не установлен"}
    LIVE.mkdir(parents=True, exist_ok=True)
    cmd = [_acme_bin(), "--issue", "-d", domain, "--keylength", "ec-256"]
    if email:
        cmd += ["--accountemail", email]
    if mode == "standalone":
        cmd.append("--standalone")
    elif mode == "webroot":
        cmd += ["--webroot", webroot]
    elif mode == "dns":
        cmd.append("--dns")
    else:
        return {"ok": False, "error": f"неизвестный режим {mode}"}

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "acme.sh timeout"}
    if r.returncode != 0 and "Domains not changed" not in (r.stdout + r.stderr):
        return {"ok": False, "error": (r.stderr or r.stdout)[-600:]}

    install = [
        _acme_bin(), "--install-cert", "-d", domain,
        "--key-file", str(LIVE / "privkey.pem"),
        "--fullchain-file", str(LIVE / "fullchain.pem"),
        "--reloadcmd",
        "chown hydra:www-data " + str(LIVE / "fullchain.pem") + " " + str(LIVE / "privkey.pem")
        + " 2>/dev/null; chmod 640 " + str(LIVE / "fullchain.pem") + " " + str(LIVE / "privkey.pem")
        + " 2>/dev/null; systemctl reload nginx 2>/dev/null || systemctl restart hydra 2>/dev/null || true",
    ]
    r2 = subprocess.run(install, capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        return {"ok": False, "error": (r2.stderr or r2.stdout)[-600:]}
    db.set_setting("tls_mode", "acme")
    return {"ok": True, "domain": domain, "cert": str(LIVE / "fullchain.pem")}


def ensure_local(host: str) -> dict:
    """Гарантирует наличие пары cert/key для инбаундов на самой панели."""
    crt, key = LIVE / "fullchain.pem", LIVE / "privkey.pem"
    if crt.exists() and key.exists():
        return {"ok": True, "cert": str(crt), "key": str(key), "mode": "existing"}
    kp, cp = security.self_signed_cert(host)
    LIVE.mkdir(parents=True, exist_ok=True)
    key.write_text(kp, encoding="utf-8")
    crt.write_text(cp, encoding="utf-8")
    db.set_setting("tls_mode", "selfsigned")
    return {"ok": True, "cert": str(crt), "key": str(key), "mode": "selfsigned"}


def info() -> dict:
    crt = LIVE / "fullchain.pem"
    if not crt.exists():
        return {"present": False}
    pem = crt.read_text(encoding="utf-8")
    return {
        "present": True,
        "expires": security.cert_expires(pem),
        "path": str(crt),
        "mode": db.get_setting("tls_mode", "acme"),
    }


def renew_all() -> None:
    if acme_available():
        subprocess.run([_acme_bin(), "--cron"], capture_output=True, text=True, timeout=300)


WILD = config.CERT_DIR / "wildcard"


def issue_wildcard(domain: str, email: str = "", dns_hook: str = "") -> dict:
    """Выпуск *.domain через DNS-01 (нужен API-хук провайдера, напр. dns_cf)."""
    if not acme_available():
        return {"ok": False, "error": "acme.sh не установлен"}
    WILD.mkdir(parents=True, exist_ok=True)
    cmd = [_acme_bin(), "--issue", "-d", domain, "-d", f"*.{domain}",
           "--keylength", "ec-256", "--dns", dns_hook or "dns_cf"]
    if email:
        cmd += ["--accountemail", email]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or r.stdout)[-600:]}
    install = [
        _acme_bin(), "--install-cert", "-d", domain,
        "--key-file", str(WILD / "privkey.pem"),
        "--fullchain-file", str(WILD / "fullchain.pem"),
        "--reloadcmd", "true",
    ]
    r2 = subprocess.run(install, capture_output=True, text=True, timeout=120)
    if r2.returncode != 0:
        return {"ok": False, "error": (r2.stderr or r2.stdout)[-600:]}
    db.set_setting("wildcard_domain", domain)
    return {"ok": True, "domain": domain, "path": str(WILD)}
