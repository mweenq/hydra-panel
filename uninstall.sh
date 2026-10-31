#!/usr/bin/env bash
# Hydra Panel — удаление.
#
#   curl -fsSL https://raw.githubusercontent.com/mweenq/hydra-panel/refs/heads/main/uninstall.sh | sudo bash
#   sudo bash uninstall.sh [--purge] [--yes]
#
# Без флагов: останавливает и убирает службы, nginx-конфиг и юниты,
#             но оставляет /opt/hydra (база, сертификаты, бэкапы).
# --purge:    удаляет и /opt/hydra, /opt/hydra-node, пользователя hydra.
# --yes:      без вопросов.
set -euo pipefail

HYDRA_DIR="/opt/hydra"
NODE_DIR="/opt/hydra-node"
PURGE=0
ASSUME=0

for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes|-y) ASSUME=1 ;;
    -h|--help)
      cat <<'USAGE'
Hydra Panel — удаление.

  curl -fsSL https://raw.githubusercontent.com/mweenq/hydra-panel/refs/heads/main/uninstall.sh | sudo bash
  sudo bash uninstall.sh [--purge] [--yes]

  --purge   удалить и данные (/opt/hydra: база, сертификаты, бэкапы)
  --yes     без подтверждений
USAGE
      exit 0 ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 1 ;;
  esac
done

if [ "$(id -u)" != "0" ]; then
  echo "нужен root: sudo bash uninstall.sh" >&2
  exit 1
fi

say()  { printf '  %s\n' "$1"; }
ok()   { printf '  ✓ %s\n' "$1"; }
warn() { printf '  ! %s\n' "$1"; }

echo
if [ "$PURGE" = "1" ]; then
  say "Будут удалены панель, нода И данные: $HYDRA_DIR (база, сертификаты, бэкапы)."
else
  say "Будут удалены панель и нода. Данные в $HYDRA_DIR останутся."
  say "Полное удаление вместе с базой: --purge"
fi
if [ "$ASSUME" != "1" ]; then
  read -r -p "  Продолжить? [y/N]: " answer </dev/tty || true
  case "${answer:-n}" in [YyДд]*) ;; *) echo "  отменено"; exit 0 ;; esac
fi

# домен нужен, чтобы снять сертификат из acme.sh — читаем до удаления конфига
DOMAIN=""
for conf in /etc/nginx/sites-available/hydra.conf /etc/nginx/conf.d/hydra.conf; do
  [ -f "$conf" ] || continue
  DOMAIN="$(awk '/server_name/ && $2 != "_" {print $2; exit}' "$conf" | tr -d ';')"
  [ -n "$DOMAIN" ] && break
done

# ------------------------------------------------------------------ службы
for unit in hydra hydra-backup.timer hydra-node hydra-xray hydra-hysteria hydra-singbox; do
  systemctl stop "$unit" >/dev/null 2>&1 || true
  systemctl disable "$unit" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/$unit.service" "/etc/systemd/system/$unit.timer"
done
systemctl daemon-reload >/dev/null 2>&1 || true
ok "Службы остановлены и юниты удалены"

# ------------------------------------------------------------------ nginx
removed_nginx=0
for f in /etc/nginx/sites-enabled/hydra.conf /etc/nginx/sites-available/hydra.conf \
         /etc/nginx/conf.d/hydra.conf; do
  [ -e "$f" ] && rm -f "$f" && removed_nginx=1
done
rm -rf /var/www/hydra-decoy
if [ "$removed_nginx" = "1" ]; then
  nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  ok "Конфиг nginx убран"
else
  say "Конфига nginx не нашлось — пропускаю"
fi

# ------------------------------------------------------------------ acme
if [ -n "$DOMAIN" ] && [ -x /root/.acme.sh/acme.sh ]; then
  /root/.acme.sh/acme.sh --remove -d "$DOMAIN" --ecc >/dev/null 2>&1 || true
  /root/.acme.sh/acme.sh --remove -d "$DOMAIN" >/dev/null 2>&1 || true
  ok "Сертификат $DOMAIN снят с автопродления (сам acme.sh оставлен)"
fi

# ------------------------------------------------------------------ данные
if [ "$PURGE" = "1" ]; then
  rm -rf "$HYDRA_DIR" "$NODE_DIR"
  id hydra >/dev/null 2>&1 && userdel -r hydra >/dev/null 2>&1 || true
  rm -f /var/log/hydra-install.log
  ok "Данные и пользователь hydra удалены"
else
  rm -rf "$NODE_DIR"
  ok "Нода удалена; данные остались в $HYDRA_DIR"
  say "Удалить их позже: rm -rf $HYDRA_DIR && userdel -r hydra"
fi

echo
say "Готово. Проверить остатки:"
say "  systemctl list-units 'hydra*' --all"
say "  ls /opt/hydra /opt/hydra-node 2>/dev/null"
echo
