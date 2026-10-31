#!/usr/bin/env bash
# Hydra Panel — установщик.
#
# Одна команда (код скачается с GitHub):
#   curl -fsSL https://raw.githubusercontent.com/mweenq/hydra-panel/refs/heads/main/install.sh | sudo bash
#
# Из клона репозитория:
#   git clone https://github.com/mweenq/hydra-panel.git && cd hydra-panel && sudo bash install.sh
#
# Флаги: --domain example.com --email a@b.c --ip --yes --password *** --no-node
set -euo pipefail

# Log all output to file with timestamps
log_file="/var/log/hydra-install.log"
if [ "$(id -u)" = "0" ]; then
  exec > >(tee -a "$log_file") 2>&1
fi

HYDRA_DIR="/opt/hydra"
APP_DIR="$HYDRA_DIR/app"
VENV="$HYDRA_DIR/venv"
# Код берём из каталога установщика, если он рядом; иначе — с GitHub.
REPO="${HYDRA_REPO:-mweenq/hydra-panel}"   # можно переопределить зеркалом
BRANCH="${HYDRA_BRANCH:-main}"
SRC_DIR=""
FETCH_ONLY=0
FETCH_TMP=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  _dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ -d "$_dir/hydra" ]; then SRC_DIR="$_dir"; fi
fi
DOMAIN=""
EMAIL=""
IP_MODE=0
ASSUME_YES=0
WITH_NODE=1
ADMIN_PASS=""
DOCKER=0

# ------------------------------------------------------------------ вывод
if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'
  C=$'\033[36m'; N=$'\033[0m'
else
  B=""; D=""; G=""; R=""; Y=""; C=""; N=""
fi

STEP=0
TOTAL=7

hr() { printf '%s\n' "${D}────────────────────────────────────────────────────────────${N}"; }

banner() {
  printf '\n'
  printf '  %s╭──────────────────────────────────────────────────────╮%s\n' "$D" "$N"
  printf '  %s│%s  %sHYDRA%s  %spanel installer%s                          %s│%s\n' "$D" "$N" "$B" "$N" "$D" "$N" "$D" "$N"
  printf '  %s╰──────────────────────────────────────────────────────╯%s\n' "$D" "$N"
  printf '\n'
}

step() {
  STEP=$((STEP + 1))
  printf '\n%s[%d/%d]%s %s%s%s\n' "$D" "$STEP" "$TOTAL" "$N" "$B" "$1" "$N"
}

ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
info() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '\n  %s✗ %s%s\n\n' "$R" "$1" "$N"; exit 1; }

ask() {
  local prompt="$1" default="${2:-}" answer
  if [ "$ASSUME_YES" = "1" ] && [ -n "$default" ]; then echo "$default"; return; fi
  if [ -n "$default" ]; then
    read -r -p "  ${prompt} ${D}[${default}]${N}: " answer </dev/tty || true
    echo "${answer:-$default}"
  else
    read -r -p "  ${prompt}: " answer </dev/tty || true
    echo "$answer"
  fi
}

as_hydra() {
  if command -v runuser >/dev/null 2>&1; then
    runuser -u hydra -- "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -u hydra "$@"
  else
    su -s /bin/sh hydra -c "$*"
  fi
}

ask_secret() {
  local prompt="$1" answer
  read -r -s -p "  ${prompt}: " answer </dev/tty || true
  printf '\n' >&2
  echo "$answer"
}

confirm() {
  local prompt="$1" default="${2:-y}" answer
  if [ "$ASSUME_YES" = "1" ]; then return 0; fi
  read -r -p "  ${prompt} ${D}[${default}]${N}: " answer </dev/tty || true
  answer="${answer:-$default}"
  case "$answer" in [YyДд]*) return 0 ;; *) return 1 ;; esac
}

# ------------------------------------------------------------------ внешнее IP с fallback
curl_opts="-4 -s --max-time 5"
get_external_ip() {
  local apis=("ifconfig.me" "api.ipify.org" "icanhazip.com")
  local ip
  for api in "${apis[@]}"; do
    ip=$(curl $curl_opts "https://$api" 2>/dev/null || echo "")
    if [[ $ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      echo "$ip"
      return 0
    fi
  done
  echo ""
}

usage() {
  cat <<'USAGE'
Hydra Panel — установщик.

  curl -fsSL https://raw.githubusercontent.com/mweenq/hydra-panel/refs/heads/main/install.sh | sudo bash
  sudo bash install.sh

Флаги:
  --domain example.com   домен панели (Let's Encrypt)
  --email a@b.c          e-mail для ACME
  --ip                   режим IP с самоподписанным сертификатом
  --yes, -y              без вопросов, значения по умолчанию
  --password ***         пароль админа
  --no-node              не ставить ноду на этот же сервер
  --docker               поставить ноду в docker
USAGE
}

# Скачивает код с GitHub, если установщик запущен не из каталога с исходниками.
ensure_src() {
  if [ -n "$SRC_DIR" ] && [ -d "$SRC_DIR/hydra" ]; then
    info "Исходники рядом: $SRC_DIR"
    return 0
  fi
  TOTAL=$((TOTAL + 1))   # загрузка — тоже шаг
  step "Загрузка кода с GitHub"
  FETCH_TMP="$(mktemp -d)"
  trap 'rm -rf "${FETCH_TMP:-}"' EXIT
  local url="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 --connect-timeout 15 "$url" -o "$FETCH_TMP/src.tar.gz" \
      || die "не удалось скачать $url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$FETCH_TMP/src.tar.gz" "$url" || die "не удалось скачать $url"
  else
    die "нет ни curl, ни wget — поставьте один из них и повторите"
  fi
  # распаковываем изнутри каталога: так tar не спотыкается о пути с «C:» в msys
  ( cd "$FETCH_TMP" && tar -xzf src.tar.gz ) || die "архив не распаковался"
  local _root
  _root="$( cd "$FETCH_TMP" && ls -d */ | head -n1 )" || die "архив пустой"
  SRC_DIR="$FETCH_TMP/${_root%/}"
  [ -d "$SRC_DIR/hydra" ] || die "в архиве нет каталога hydra"
  ok "Код скачан: $SRC_DIR"
}

# ------------------------------------------------------------------ аргументы
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --ip) IP_MODE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --no-node) WITH_NODE=0; shift ;;
    --password) ADMIN_PASS="$2"; shift 2 ;;
    --docker) DOCKER=1; shift ;;
    --fetch-only) FETCH_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "неизвестный аргумент: $1" ;;
  esac
done

banner

# только скачать код и показать путь — проверка связи с GitHub
if [ "$FETCH_ONLY" = "1" ]; then
  ensure_src
  echo "SRC_DIR=$SRC_DIR"
  exit 0
fi

# ------------------------------------------------------------------ 1. система
step "Проверка системы"
[ "$(id -u)" = "0" ] || die "нужен root: sudo bash install.sh"

if [ -r /etc/os-release ]; then . /etc/os-release; else die "не удалось определить ОС"; fi
info "ОС: ${PRETTY_NAME:-unknown}"

PKG=""
if command -v apt-get >/dev/null 2>&1; then PKG=apt
elif command -v dnf >/dev/null 2>&1; then PKG=dnf
elif command -v yum >/dev/null 2>&1; then PKG=yum
else die "поддерживаются apt / dnf / yum"; fi

if [ "$PKG" = "apt" ]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq python3 python3-venv python3-pip nginx curl tar openssl ca-certificates \
    cron socat jq iproute2 >/dev/null 2>&1 || die "не удалось поставить пакеты"
else
  $PKG install -y -q python3 python3-pip nginx curl tar openssl ca-certificates cron socat jq \
    >/dev/null 2>&1 || die "не удалось поставить пакеты"
fi
ok "Зависимости на месте"

PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' \
  || die "нужен Python 3.9+, найден $PYV"
info "Python $PYV"

# ------------------------------------------------------------------ 0. порты
# Проверка 80, 443, 8000 — предупреждаем, если уже используются
if [ "$ASSUME_YES" = "1" ] || ( [ -t 1 ] && [ "$ASSUME_YES" != "1" ] ); then
  step "Проверка портов (80, 443, 8000)"
  for p in 80 443 8000; do
    if ss -ltn 2>/dev/null | grep -q ":$p[[:space:]]"; then
      warn "Порт $p уже используется (возможно, Apache, Nginx, другая панель). Рекомендуем освободить его или использовать другой порт."
    fi
  done
  ok "Проверка портов завершена"
fi

# ------------------------------------------------------------------ 2. адрес
step "Куда ставим панель"
if [ "$ASSUME_YES" = "1" ] && [ -z "$DOMAIN" ] && [ "$IP_MODE" = "0" ]; then
  die "неинтерактивная установка: укажите --domain example.com или --ip"
fi
if [ -z "$DOMAIN" ] && [ "$IP_MODE" = "0" ]; then
  printf '  %s1%s домен (рекомендуется, Let'"'"'s Encrypt)\n' "$B" "$N"
  printf '  %s2%s IP-адрес (самоподписанный сертификат)\n' "$B" "$N"
  MODE=$(ask "Выбор" "1")
  case "$MODE" in
    2) IP_MODE=1 ;;
    *) IP_MODE=0 ;;
  esac
fi

if [ "$IP_MODE" = "1" ]; then
  # получить внешний IP через несколько fallback-источников
  DEFAULT_IP=""
  if command -v curl >/dev/null 2>&1; then
    DEFAULT_IP=$(get_external_ip)
  fi
  if [ -z "$DEFAULT_IP" ]; then
    DEFAULT_IP=$(hostname -I | awk '{print $1}')
  fi
  [ -n "$DOMAIN" ] || DOMAIN=$(ask "IP-адрес сервера" "$DEFAULT_IP")
  PANEL_URL="https://$DOMAIN"
  info "Режим IP: браузер будет ругаться на сертификат — это ожидаемо"
else
  [ -n "$DOMAIN" ] || DOMAIN=$(ask "Домен панели (A-запись уже должна указывать на этот сервер)")
  [ -n "$DOMAIN" ] || die "домен обязателен"
  PANEL_URL="https://$DOMAIN"
fi
ok "Панель: $PANEL_URL"

if [ -z "$EMAIL" ] && [ "$IP_MODE" = "0" ]; then
  EMAIL=$(ask "E-mail для Let's Encrypt" "admin@$DOMAIN")
fi

# ------------------------------------------------------------------ 3. пароль
step "Администратор"
if [ -z "$ADMIN_PASS" ]; then
  if confirm "Сгенерировать пароль автоматически?" "y"; then
    ADMIN_PASS=$(openssl rand -base64 12 | tr -d '/+=' | cut -c1-14)
    info "Пароль: $ADMIN_PASS"
  else
    ADMIN_PASS=$(ask_secret "Придумайте пароль (мин. 8 символов)")
    [ ${#ADMIN_PASS} -ge 8 ] || die "слишком короткий пароль"
  fi
fi
ok "Логин: admin"

# ------------------------------------------------------------------ 4. файлы
ensure_src
step "Установка панели"
FRESH_DB=1
[ -f "$HYDRA_DIR/data/hydra.db" ] && FRESH_DB=0
id hydra >/dev/null 2>&1 || useradd -r -m -d "$HYDRA_DIR" -s /usr/sbin/nologin hydra
mkdir -p "$APP_DIR" "$HYDRA_DIR/data" "$HYDRA_DIR/certs" "$HYDRA_DIR/run"
# служба работает под пользователем hydra — каталоги должны быть ему доступны на запись,
# иначе при первом старте не создастся CA и панель упадёт
chown -R hydra:hydra "$HYDRA_DIR/data" "$HYDRA_DIR/certs" "$HYDRA_DIR/run"
chmod 750 "$HYDRA_DIR/data" "$HYDRA_DIR/certs" "$HYDRA_DIR/run"

if [ "$SRC_DIR" != "$APP_DIR" ]; then
  command -v rsync >/dev/null 2>&1 || $PKG install -y -q rsync >/dev/null 2>&1 || true
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude data --exclude .git --exclude __pycache__ "$SRC_DIR/" "$APP_DIR/"
  else
    cp -a "$SRC_DIR/." "$APP_DIR/"
    rm -rf "$APP_DIR/data"
  fi
fi
ok "Код в $APP_DIR"

python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt" || die "pip install не удался"
ok "Зависимости Python установлены"

cat > /usr/local/bin/hydra <<'WRAP'
#!/bin/sh
exec env HYDRA_HOME=__HYDRA_DIR__ __VENV__/bin/python -m hydra "$@"
WRAP
sed -i "s|__HYDRA_DIR__|$HYDRA_DIR|; s|__VENV__|$VENV|" /usr/local/bin/hydra
chmod +x /usr/local/bin/hydra
ok "Команда hydra доступна"

cat > /etc/systemd/system/hydra.service <<EOF
[Unit]
Description=Hydra Panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=hydra
Group=hydra
WorkingDirectory=$APP_DIR
Environment=HYDRA_HOME=$HYDRA_DIR
Environment=HYDRA_HOST=127.0.0.1
Environment=HYDRA_PORT=8000
Environment=HYDRA_ADMIN_PASSWORD=$ADMIN_PASS
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/python -m hydra serve
Restart=always
RestartSec=3
LimitNOFILE=65535
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hydra >/dev/null 2>&1
systemctl restart hydra
sleep 2
systemctl is-active --quiet hydra || die "панель не запустилась: journalctl -u hydra -n 40"
ok "Служба hydra запущена"

if [ "$FRESH_DB" = "0" ]; then
  as_hydra env HYDRA_HOME="$HYDRA_DIR" "$VENV/bin/python" -m hydra passwd -p "$ADMIN_PASS" >/dev/null 2>&1     && info "База уже была — пароль приведён к указанному"
fi

# пароль больше не нужен в юните
sed -i '/HYDRA_ADMIN_PASSWORD/d' /etc/systemd/system/hydra.service
# пароль сохраняем сразу: если установка упадёт дальше, он не потеряется
mkdir -p "$HYDRA_DIR/data"
printf '%s' "$ADMIN_PASS" > "$HYDRA_DIR/data/initial_password.txt"
chown hydra:hydra "$HYDRA_DIR/data/initial_password.txt" 2>/dev/null || true
chmod 600 "$HYDRA_DIR/data/initial_password.txt" 2>/dev/null || true
systemctl daemon-reload

# ------------------------------------------------------------------ 5. TLS
step "Сертификат"
mkdir -p /var/www/acme
chmod 755 /var/www/acme

CERT_FIX="chown hydra:www-data $HYDRA_DIR/certs/fullchain.pem $HYDRA_DIR/certs/privkey.pem; chmod 640 $HYDRA_DIR/certs/fullchain.pem $HYDRA_DIR/certs/privkey.pem; systemctl reload nginx"

if [ "$IP_MODE" = "1" ]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$HYDRA_DIR/certs/privkey.pem" \
    -out "$HYDRA_DIR/certs/fullchain.pem" \
    -subj "/CN=$DOMAIN" -addext "subjectAltName=IP:$DOMAIN" >/dev/null 2>&1
  ok "Самоподписанный сертификат выпущен"
  TLS_MODE="selfsigned"
else
  if [ ! -f /root/.acme.sh/acme.sh ]; then
    info "Ставлю acme.sh…"
    curl -fsSL https://get.acme.sh | sh -s email="$EMAIL" >/dev/null 2>&1 || die "acme.sh не установился"
  fi
  systemctl stop nginx >/dev/null 2>&1 || true
  if ! /root/.acme.sh/acme.sh --issue -d "$DOMAIN" --standalone --keylength ec-256 >/tmp/acme.log 2>&1; then
    # acme.sh возвращает ошибку и тогда, когда сертификат уже выпущен («Skipping»)
    if /root/.acme.sh/acme.sh --list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$DOMAIN"; then
      info "Сертификат уже есть — обновляю файлы, повторный выпуск не нужен"
    else
      warn "Выпуск не удался, смотрите /tmp/acme.log"
      warn "Ставлю самоподписанный — панель поднимется, сертификат замените позже"
      openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "$HYDRA_DIR/certs/privkey.pem" -out "$HYDRA_DIR/certs/fullchain.pem" \
        -subj "/CN=$DOMAIN" >/dev/null 2>&1
      TLS_MODE="selfsigned"
    fi
  fi
  if [ "${TLS_MODE:-}" != "selfsigned" ]; then
    /root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" --ecc \
      --key-file "$HYDRA_DIR/certs/privkey.pem" \
      --fullchain-file "$HYDRA_DIR/certs/fullchain.pem" \
      --reloadcmd "$CERT_FIX" >/dev/null 2>&1
    ok "Let's Encrypt выпущен, автопродление включено (acme.sh cron)"
    TLS_MODE="acme"
  fi
fi

chown hydra:www-data "$HYDRA_DIR/certs"
chmod 750 "$HYDRA_DIR/certs"
for f in "$HYDRA_DIR/certs"/*.pem "$HYDRA_DIR/certs"/*.key; do
  [ -e "$f" ] || continue
  chown hydra:hydra "$f"
  chmod 600 "$f"
done
# nginx нужна только серверная пара; ca.key и master.key остаются закрытыми
for f in fullchain.pem privkey.pem; do
  if [ -e "$HYDRA_DIR/certs/$f" ]; then
    chown hydra:www-data "$HYDRA_DIR/certs/$f"
    chmod 640 "$HYDRA_DIR/certs/$f"
  fi
done

# ------------------------------------------------------------------ 6. nginx
step "Nginx и заглушка"
mkdir -p /var/www/hydra-decoy
cat > /var/www/hydra-decoy/index.html <<'HTML'
<!doctype html><meta charset="utf-8"><title>Northline</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1f2937;
max-width:760px;margin:80px auto;padding:0 24px;line-height:1.65}
h1{font-size:28px;letter-spacing:-.02em}p{color:#4b5563}</style>
<h1>Infrastructure that quietly does its job.</h1>
<p>We build and operate backend systems for teams that need reliability more than buzzwords.</p>
<p>Managed hosting, observability and support — handled.</p>
HTML

NGINX_CONF="/etc/nginx/conf.d/hydra.conf"
if [ -d /etc/nginx/sites-available ]; then
  NGINX_CONF="/etc/nginx/sites-available/hydra.conf"
fi

cat > "$NGINX_CONF" <<EOF
# Hydra Panel — сгенерировано установщиком
map \$http_upgrade \$connection_upgrade { default upgrade; '' close; }

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    location /.well-known/acme-challenge/ { root /var/www/acme; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;

    ssl_certificate     $HYDRA_DIR/certs/fullchain.pem;
    ssl_certificate_key $HYDRA_DIR/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy no-referrer always;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 120s;
    }
}

# Заглушка для fallback: сюда уходит «чужой» TLS с инбаундов
server {
    listen 127.0.0.1:8080;
    server_name _;
    root /var/www/hydra-decoy;
    index index.html;
    location / { try_files \$uri /index.html; }
}
EOF

if [ -d /etc/nginx/sites-enabled ]; then
  rm -f /etc/nginx/sites-enabled/default
  ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/hydra.conf
fi
nginx -t >/dev/null 2>&1 || die "конфиг nginx невалиден: nginx -t"
systemctl enable nginx >/dev/null 2>&1
systemctl restart nginx
ok "Nginx поднят, заглушка на 127.0.0.1:8080"

# настройки в базе
as_hydra env HYDRA_HOME="$HYDRA_DIR" PYTHONPATH="$APP_DIR" "$VENV/bin/python" - \
  "$PANEL_URL" "$EMAIL" "$TLS_MODE" <<'PY'
import sys
from hydra import db
from hydra.services import certs
db.init_db()
db.set_setting("panel_url", sys.argv[1])
db.set_setting("acme_email", sys.argv[2])
db.set_setting("tls_mode", sys.argv[3])
certs.ensure_local(sys.argv[1].split("//")[-1])
print("settings ok")
PY

# ------------------------------------------------------------------ 7. нода
step "Нода на этом сервере"
if [ "$WITH_NODE" = "1" ] && confirm "Поставить ноду здесь же? (VLESS/REALITY/Hysteria2 на этом IP)" "y"; then
  TOKENS=$(as_hydra env HYDRA_HOME="$HYDRA_DIR" PYTHONPATH="$APP_DIR" "$VENV/bin/python" - "$DOMAIN" <<'PY'
import sys
from hydra import db
from hydra.services import nodes
db.init_db()
if not nodes.list_nodes():
    nid, token = nodes.create_node("local", "127.0.0.1", "xray", 8443, True, "панель",
                                   public_host=sys.argv[1].split("//")[-1])
    print(f"{nid}|{token}")
PY
)
  if [ -n "$TOKENS" ]; then
    TOKEN="${TOKENS##*|}"
    NODE_ARGS=(--master "$PANEL_URL" --token "$TOKEN" --name "$(hostname -s)"
               --domain "$DOMAIN" --local)
    [ -n "${EMAIL:-}" ] && NODE_ARGS+=(--email "$EMAIL")
    # в IP-режиме сертификат самоподписанный — ноде нужно разрешить его принять
    [ "$IP_MODE" = "1" ] && NODE_ARGS+=(--insecure)
    bash "$APP_DIR/hydra/static/install-node.sh" "${NODE_ARGS[@]}" \
      || warn "нода не установилась — повторите позже"
  else
    info "Нода уже зарегистрирована"
  fi
else
  info "Пропущено — добавите через панель"
fi

# ежедневный бэкап
if [ -f "$APP_DIR/deploy/hydra-backup.service" ]; then
  cp "$APP_DIR/deploy/hydra-backup.service" /etc/systemd/system/
  cp "$APP_DIR/deploy/hydra-backup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now hydra-backup.timer >/dev/null 2>&1 || true
  ok "Автобэкап включён (04:00)"
fi

# firewall
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw allow 443/udp >/dev/null 2>&1 || true
  ufw allow 8443/tcp >/dev/null 2>&1 || true
  ok "Порты открыты в ufw"
fi

# Docker deployment files (optional)
if [ "$DOCKER" = "1" ]; then
  mkdir -p deploy
  # Create a simple Dockerfile placeholder
  cat > deploy/Dockerfile <<'EOF'
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN python -m venv venv
RUN . venv/bin/activate && pip install -r requirements.txt
EXPOSE 8000
CMD ["venv/bin/python", "-m", "hydra", "serve"]
EOF
  # Create a docker-compose.yml placeholder
  cat > deploy/docker-compose.yml <<'EOF'
version: '3.8'
services:
  hydra:
    build: .
    ports:
      - "8000:8000"
    environment:
      - HYDRA_HOME=/app
    volumes:
      - ./data:/app/data
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - hydra
EOF
  info "Docker deployment files generated in ./deploy/ (requires manual configuration)"
fi

# ------------------------------------------------------------------ итог
printf '\n'
hr
printf '  %sГотово%s\n\n' "$B" "$N"
printf '  Панель     %s%s%s\n' "$C" "$PANEL_URL" "$N"
printf '  Логин      %sadmin%s\n' "$B" "$N"
printf '  Пароль     %s%s%s\n' "$B" "$ADMIN_PASS" "$N"
printf '  Данные     %s\n' "$HYDRA_DIR"
printf '  Службы     %shydra%s, %snginx%s%s\n' "$D" "$N" "$D" "$N" \
  "$([ "$WITH_NODE" = "1" ] && echo ", hydra-node")"
printf '\n'
printf '  %sДальше:%s\n' "$D" "$N"
printf '  · создайте инбаунд в разделе «Инбаунды»\n'
printf '  · клиентов — в «Клиенты», ссылка появится сразу\n'
printf '  · ботов и бэкапы — в «Настройки»\n'
printf '\n'
printf '  %sЛоги:%s journalctl -u hydra -f\n' "$D" "$N"
printf '  %sПароль:%s hydra passwd\n' "$D" "$N"
hr
printf '\n'

echo "$ADMIN_PASS" > "$HYDRA_DIR/data/initial_password.txt"
chown hydra:hydra "$HYDRA_DIR/data/initial_password.txt"
chmod 600 "$HYDRA_DIR/data/initial_password.txt"
