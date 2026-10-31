"""Командная строка: запуск, пароль, бэкапы, сертификаты."""
from __future__ import annotations

import argparse
import getpass
import sys

from . import config, db


def cmd_serve(args) -> int:
    import uvicorn

    from .web.app import create_app

    app = create_app()
    uvicorn.run(app, host=config.WEB_HOST, port=args.port or config.WEB_PORT,
                log_level="info", proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    return 0


def cmd_passwd(args) -> int:
    db.init_db()
    username = args.user or "admin"
    row = db.q1("SELECT id FROM admins WHERE username=?", (username,))
    if not row:
        print(f"нет пользователя {username}")
        return 1
    from .security import hash_password

    pw = args.password or getpass.getpass("Новый пароль: ")
    if len(pw) < 8:
        print("минимум 8 символов")
        return 1
    db.ex("UPDATE admins SET password_hash=? WHERE id=?", (hash_password(pw), row["id"]))
    print("пароль обновлён")
    return 0


def cmd_backup(args) -> int:
    from .services import backup

    path = backup.create()
    print(path)
    if args.tg:
        ok = backup.send_to_telegram(
            path, str(db.get_setting("bot_token", "")), str(db.get_setting("backup_tg_chat", ""))
        )
        print("отправлено в Telegram" if ok else "Telegram не настроен или отправка не удалась")
    return 0


def cmd_restore(args) -> int:
    import shutil
    import subprocess

    from .services import backup

    has_systemd = shutil.which("systemctl") is not None
    if has_systemd:
        subprocess.run(["systemctl", "stop", "hydra"], capture_output=True)
    try:
        backup.restore(args.file)
    except Exception as e:  # noqa: BLE001
        print(f"не удалось восстановить: {e}")
        return 1
    if has_systemd:
        subprocess.run(["systemctl", "start", "hydra"], capture_output=True)
        print("восстановлено, панель перезапущена")
    else:
        print("восстановлено. запустите панель заново")
    return 0


def cmd_cert(args) -> int:
    from .services import certs

    if getattr(args, "wildcard", False):
        res = certs.issue_wildcard(args.domain, args.email or str(db.get_setting("acme_email", "")),
                                   getattr(args, "dns_hook", "") or "")
    else:
        res = certs.issue(args.domain, args.email or str(db.get_setting("acme_email", "")),
                          args.mode or "standalone")
    print(res)
    return 0 if res.get("ok") else 1


def cmd_init(args) -> int:
    db.init_db()
    print(f"база: {config.DB_PATH}")
    print(f"данные: {config.HOME}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hydra", description="Hydra Panel")
    p.add_argument("--version", action="version", version=config.VERSION)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="запустить панель")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("init", help="создать базу")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("passwd", help="сменить пароль администратора")
    s.add_argument("user", nargs="?", default="admin")
    s.add_argument("-p", "--password")
    s.set_defaults(func=cmd_passwd)

    s = sub.add_parser("backup", help="сделать резервную копию")
    s.add_argument("--tg", action="store_true", help="отправить в Telegram")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("restore", help="восстановить из архива")
    s.add_argument("file")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("cert", help="выпустить сертификат через acme.sh")
    s.add_argument("domain")
    s.add_argument("--email")
    s.add_argument("--mode", choices=["standalone", "webroot", "dns"], default="standalone")
    s.add_argument("--wildcard", action="store_true", help="выпустить *.domain (DNS-01)")
    s.add_argument("--dns-hook", default="", help="хук acme.sh, например dns_cf")
    s.set_defaults(func=cmd_cert)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        args.func = cmd_serve
        args.port = None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
