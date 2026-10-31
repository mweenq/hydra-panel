"""Резервные копии: база + сертификаты + настройки одним архивом."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

import httpx

from .. import config, db

BACKUP_DIR = config.DATA_DIR / "backups"


def create() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"hydra-{stamp}.tar.gz"
    tmp = Path(tempfile.mkdtemp(prefix="hydra-bk-"))
    try:
        snap = tmp / "hydra.db"
        src = sqlite3.connect(config.DB_PATH)
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        (tmp / "settings.json").write_text(
            json.dumps(db.all_settings(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        meta = {
            "version": config.VERSION,
            "created_at": db.now(),
            "db_bytes": snap.stat().st_size,
        }
        (tmp / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        with tarfile.open(target, "w:gz") as tar:
            tar.add(snap, arcname="hydra.db")
            tar.add(tmp / "settings.json", arcname="settings.json")
            tar.add(tmp / "meta.json", arcname="meta.json")
            if config.CERT_DIR.exists():
                tar.add(config.CERT_DIR, arcname="certs")
        cleanup()
        return target
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cleanup() -> None:
    keep = int(db.get_setting("backup_keep", 14) or 14)
    files = sorted(BACKUP_DIR.glob("hydra-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def restore(path: str | Path) -> None:
    """Распаковывает архив поверх текущих данных. Панель надо перезапустить."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    tmp = Path(tempfile.mkdtemp(prefix="hydra-restore-"))
    try:
        with tarfile.open(path, "r:gz") as tar:
            try:
                tar.extractall(tmp, filter="data")  # python 3.12+
            except TypeError:  # старые версии не знают filter
                tar.extractall(tmp)
        db_file = tmp / "hydra.db"
        if not db_file.exists():
            raise ValueError("в архиве нет hydra.db")
        # закрываем соединение и убираем WAL, иначе старые страницы переиграют восстановленные
        db.close()
        shutil.copy2(db_file, config.DB_PATH)
        # старый WAL переиграл бы восстановленные страницы — обнуляем его
        for suffix in ("-wal", "-shm"):
            wal = Path(str(config.DB_PATH) + suffix)
            if wal.exists():
                try:
                    os.truncate(wal, 0)
                except OSError:
                    pass
        certs = tmp / "certs"
        if certs.exists():
            config.CERT_DIR.mkdir(parents=True, exist_ok=True)
            for f in certs.iterdir():
                shutil.copy2(f, config.CERT_DIR / f.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def send_to_telegram(path: Path, token: str, chat_id: str, caption: str = "") -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(path, "rb") as fh:
            r = httpx.post(
                url,
                data={"chat_id": chat_id, "caption": caption or f"Hydra backup {path.name}"},
                files={"document": (path.name, fh, "application/gzip")},
                timeout=120,
            )
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:  # noqa: BLE001
        return False


_scheduler_started = False


def start_scheduler() -> None:
    """Раз в сутки в 04:00 (по локальному времени) делает копию и шлёт в Telegram."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    import threading
    import time as _time

    def loop() -> None:
        last = ""
        while True:
            try:
                today = _time.strftime("%Y-%m-%d")
                if _time.localtime().tm_hour >= 4 and last != today:
                    path = create()
                    token = str(db.get_setting("bot_token", "") or "")
                    chat = str(db.get_setting("backup_tg_chat", "") or "")
                    if token and chat:
                        send_to_telegram(path, token, chat, f"Автобэкап {path.name}")
                    db.audit("backup.auto", target=path.name)
                    last = today
            except Exception:  # noqa: BLE001
                pass
            _time.sleep(600)

    threading.Thread(target=loop, name="hydra-backup", daemon=True).start()
