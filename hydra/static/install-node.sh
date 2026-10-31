#!/usr/bin/env bash
# hydra-node installer — ставит движок и агент, подключает к панели по mTLS.
# Запуск:  bash install-node.sh --master https://panel.example.com --token XXX [--domain node.example.com]
set -euo pipefail

MASTER=""
TOKEN=""
NAME="$(hostname -s 2>/dev/null || hostname)"
DOMAIN=""
EMAIL=""
ENGINE="xray"
PORT=8443
LOCAL=0
WITH_HY2=1
OPENFLUX_URL=""
INSECURE=0

NODE_DIR="/opt/hydra-node"

if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else
  B=""; D=""; G=""; R=""; Y=""; N=""
fi
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$1"; }
info() { printf '  %s·%s %s\n' "$D" "$N" "$1"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$1"; }
die()  { printf '  %s✗ %s%s\n' "$R" "$1" "$N"; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --master) MASTER="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --email) EMAIL="$2"; shift 2 ;;
    --engine) ENGINE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --local) LOCAL=1; shift ;;
    --no-hy2) WITH_HY2=0; shift ;;
    --openflux-url) OPENFLUX_URL="$2"; shift 2 ;;
    --insecure) INSECURE=1; shift ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) die "неизвестный аргумент: $1" ;;
  esac
done

[ "$(id -u)" = "0" ] || die "нужен root"
[ -n "$MASTER" ] || die "нужен --master"
[ -n "$TOKEN" ] || die "нужен --token"

printf '\n  %sHydra Node%s  установка\n' "$B" "$N"
printf '  %s────────────────────────────────────────────%s\n\n' "$D" "$N"

# ------------------------------------------------------------------ пакеты
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq python3 curl tar unzip openssl ca-certificates jq >/dev/null 2>&1
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y -q python3 curl tar unzip openssl ca-certificates jq >/dev/null 2>&1
elif command -v yum >/dev/null 2>&1; then
  yum install -y -q python3 curl tar unzip openssl ca-certificates jq >/dev/null 2>&1
else
  die "неподдерживаемый пакетный менеджер"
fi
ok "Зависимости"

mkdir -p "$NODE_DIR"/{certs,agent,xray,hysteria,singbox,logs}

# ------------------------------------------------------------------ бинарники
arch() {
  case "$(uname -m)" in
    x86_64|amd64) echo "64" ;;
    aarch64|arm64) echo "arm64-v8a" ;;
    *) echo "64" ;;
  esac
}
HYARCH() {
  case "$(uname -m)" in
    x86_64|amd64) echo "amd64" ;;
    aarch64|arm64) echo "arm64" ;;
    *) echo "amd64" ;;
  esac
}

install_xray() {
  if [ -x "$NODE_DIR/xray/xray" ]; then info "xray уже установлен"; return; fi
  info "Скачиваю xray-core…"
  local url
  url="https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-$(arch).zip"
  curl -fsSL --retry 3 -o /tmp/xray.zip "$url" || die "не удалось скачать xray"
  unzip -oq /tmp/xray.zip -d "$NODE_DIR/xray" || die "не удалось распаковать xray"
  chmod +x "$NODE_DIR/xray/xray"
  rm -f /tmp/xray.zip
  ok "xray $("$NODE_DIR/xray/xray" version 2>/dev/null | head -1 || echo '')"
}

install_openflux() {
  if [ -z "$OPENFLUX_URL" ]; then
    warn "openflux не задан (--openflux-url). Останется xray."
    return
  fi
  info "Скачиваю openflux…"
  curl -fsSL --retry 3 -o "$NODE_DIR/xray/openflux" "$OPENFLUX_URL" || die "не удалось скачать openflux"
  chmod +x "$NODE_DIR/xray/openflux"
  ok "openflux установлен"
}

install_hy2() {
  [ "$WITH_HY2" = "1" ] || return 0
  if [ -x "$NODE_DIR/hysteria/hysteria" ]; then info "hysteria2 уже установлен"; return; fi
  info "Скачиваю hysteria2…"
  curl -fsSL --retry 3 -o "$NODE_DIR/hysteria/hysteria" \
    "https://github.com/apernet/hysteria/releases/latest/download/hysteria-linux-$(HYARCH)" \
    || { warn "hysteria2 не скачался — пропускаю"; return 0; }
  chmod +x "$NODE_DIR/hysteria/hysteria"
  ok "hysteria2 установлен"
}

install_singbox() {
  [ "$ENGINE" = "singbox" ] || return 0
  if [ -x "$NODE_DIR/singbox/sing-box" ]; then info "sing-box уже установлен"; return; fi
  info "Скачиваю sing-box…"
  local tag url
  tag=$(curl -fsSL https://api.github.com/repos/SagerNet/sing-box/releases/latest | jq -r .tag_name)
  url="https://github.com/SagerNet/sing-box/releases/download/${tag}/sing-box-${tag#v}-linux-$(HYARCH).tar.gz"
  curl -fsSL --retry 3 -o /tmp/sb.tar.gz "$url" || { warn "sing-box не скачался"; return 0; }
  tar -xzf /tmp/sb.tar.gz -C /tmp
  find /tmp -maxdepth 2 -name 'sing-box' -type f -exec mv {} "$NODE_DIR/singbox/sing-box" \;
  chmod +x "$NODE_DIR/singbox/sing-box"
  rm -f /tmp/sb.tar.gz
  ok "sing-box установлен"
}

install_xray
[ "$ENGINE" = "openflux" ] && install_openflux
install_hy2
install_singbox

# ------------------------------------------------------------------ TLS
if [ -f "$NODE_DIR/certs/fullchain.pem" ]; then
  info "Сертификат уже есть"
elif [ "$LOCAL" = "1" ] && [ -f /opt/hydra/certs/fullchain.pem ]; then
  cp /opt/hydra/certs/fullchain.pem /opt/hydra/certs/privkey.pem "$NODE_DIR/certs/"
  ok "Взял сертификат панели"
elif [ -n "$DOMAIN" ]; then
  if [ ! -f /root/.acme.sh/acme.sh ]; then
    info "Ставлю acme.sh…"
    curl -fsSL https://get.acme.sh | sh -s email="${EMAIL:-admin@$DOMAIN}" >/dev/null 2>&1 || true
  fi
  systemctl stop nginx >/dev/null 2>&1 || true
  if /root/.acme.sh/acme.sh --issue -d "$DOMAIN" --standalone --keylength ec-256 >/tmp/acme-node.log 2>&1; then
    /root/.acme.sh/acme.sh --install-cert -d "$DOMAIN" \
      --key-file "$NODE_DIR/certs/privkey.pem" \
      --fullchain-file "$NODE_DIR/certs/fullchain.pem" \
      --reloadcmd "systemctl restart hydra-xray hydra-hysteria hydra-singbox 2>/dev/null || true" >/dev/null 2>&1
    ok "Let's Encrypt для $DOMAIN"
  else
    warn "ACME не удался (см. /tmp/acme-node.log) — ставлю самоподписанный"
  fi
fi

if [ ! -f "$NODE_DIR/certs/fullchain.pem" ]; then
  IP=$(curl -4 -s --max-time 5 ifconfig.me || echo 127.0.0.1)
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$NODE_DIR/certs/privkey.pem" -out "$NODE_DIR/certs/fullchain.pem" \
    -subj "/CN=$IP" -addext "subjectAltName=IP:$IP" >/dev/null 2>&1
  ok "Самоподписанный сертификат ($IP)"
fi
chmod 600 "$NODE_DIR/certs/privkey.pem"

# ------------------------------------------------------------------ агент
if [ -f "$NODE_DIR/hydra-node.py" ] && [ "$LOCAL" = "0" ]; then
  info "Агент уже на месте"
else
  # при запуске через `curl | bash` BASH_SOURCE пуст — не падаем на set -u
  SELF_DIR=""
  if [ -n "${BASH_SOURCE[0]:-}" ]; then
    SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  fi
  if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/hydra-node.py" ]; then
    cp "$SELF_DIR/hydra-node.py" "$NODE_DIR/hydra-node.py"
  elif [ -f /opt/hydra/app/node/hydra-node.py ]; then
    cp /opt/hydra/app/node/hydra-node.py "$NODE_DIR/hydra-node.py"
  else
    curl -fsSL "${MASTER%/}/node-agent.py" -o "$NODE_DIR/hydra-node.py" \
      || die "не удалось получить агент"
  fi
fi
chmod +x "$NODE_DIR/hydra-node.py"

# ------------------------------------------------------------------ systemd
XRAY_BIN="$NODE_DIR/xray/xray"
[ "$ENGINE" = "openflux" ] && [ -x "$NODE_DIR/xray/openflux" ] && XRAY_BIN="$NODE_DIR/xray/openflux"

cat > /etc/systemd/system/hydra-xray.service <<EOF
[Unit]
Description=Hydra Xray
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$XRAY_BIN run -c $NODE_DIR/xray/config.json
Restart=always
RestartSec=2
LimitNOFILE=65535
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hydra-hysteria.service <<EOF
[Unit]
Description=Hydra Hysteria2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$NODE_DIR/hysteria/hysteria server -c $NODE_DIR/hysteria/config.yaml
Restart=always
RestartSec=2
LimitNOFILE=65535
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hydra-singbox.service <<EOF
[Unit]
Description=Hydra sing-box
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$NODE_DIR/singbox/sing-box run -c $NODE_DIR/singbox/config.json
Restart=always
RestartSec=2
LimitNOFILE=65535
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hydra-node.service <<EOF
[Unit]
Description=Hydra Node Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $NODE_DIR/hydra-node.py --serve --port $PORT
Restart=always
RestartSec=3
Environment=HYDRA_NODE_HOME=$NODE_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
ok "Службы записаны"

# ------------------------------------------------------------------ join
info "Подключаюсь к панели…"
if [ -f "$NODE_DIR/agent/node.pem" ]; then
  info "Уже подключено"
else
  JOIN_ARGS=(--join --master "$MASTER" --token "$TOKEN" --name "$NAME"
             --engine "$ENGINE" --port "$PORT")
  [ "$INSECURE" = "1" ] && JOIN_ARGS+=(--insecure)
  python3 "$NODE_DIR/hydra-node.py" "${JOIN_ARGS[@]}" || die "не удалось подключиться к панели"
fi

systemctl enable hydra-node >/dev/null 2>&1
systemctl restart hydra-node
sleep 1
systemctl is-active --quiet hydra-node || die "агент не запустился: journalctl -u hydra-node -n 30"
ok "Агент слушает :$PORT (mTLS)"

systemctl enable hydra-xray >/dev/null 2>&1 || true
systemctl enable hydra-hysteria >/dev/null 2>&1 || true

# firewall
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q active; then
  ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw allow 443/udp >/dev/null 2>&1 || true
  ok "Порты открыты"
fi

printf '\n  %sНода готова%s\n\n' "$B" "$N"
printf '  Панель   %s\n' "$MASTER"
printf '  Движок   %s\n' "$ENGINE"
printf '  Логи     journalctl -u hydra-node -f\n\n'
