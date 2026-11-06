#!/usr/bin/env bash
# Bot Nazorat — Ubuntu serverga bir buyruq bilan o'rnatish (Oracle Cloud Always Free uchun moslangan).
# Ishlatish:  cd botnazorat-papkasi && sudo bash install.sh
set -euo pipefail
cd "$(dirname "$0")"
APP_DIR="$(pwd)"
[ "$(id -u)" = 0 ] || { echo "sudo bilan ishga tushiring: sudo bash install.sh"; exit 1; }

echo "=== 📊 Bot Nazorat o'rnatilmoqda ==="
read -rp "1) Nazorat botining tokeni (@BotFather'dan): " CONTROL_TOKEN
read -rp "2) Sizning Telegram ID'ingiz (@userinfobot'dan): " ALLOWED_USERS

IP="$(curl -4 -s https://ifconfig.me || curl -4 -s https://api.ipify.org)"
DOMAIN="${IP//./-}.sslip.io"     # bepul https manzil: IP asosidagi domen
SECRET_KEY="$(openssl rand -hex 32)"

echo "→ Paketlar o'rnatilmoqda…"
apt-get update -y -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 python3-venv python3-pip caddy iptables-persistent >/dev/null

echo "→ Python muhiti…"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
cat > "$APP_DIR/.env" <<EOF
CONTROL_TOKEN=$CONTROL_TOKEN
PUBLIC_URL=https://$DOMAIN
SECRET_KEY=$SECRET_KEY
ALLOWED_USERS=$ALLOWED_USERS
TZ_OFFSET=5
REPORT_HOUR=21
DB_PATH=$APP_DIR/data/nazorat.db
PORT=8080
EOF
chmod 600 "$APP_DIR/.env"
else
  echo "→ .env allaqachon bor — o'zgartirilmadi (SECRET_KEY saqlanib qoldi)."
fi
mkdir -p "$APP_DIR/data"

echo "→ Avtomatik ishga tushish (systemd)…"
cat > /etc/systemd/system/botnazorat.service <<EOF
[Unit]
Description=Bot Nazorat
After=network-online.target
[Service]
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
Environment=MPLCONFIGDIR=/tmp
ExecStart=$APP_DIR/.venv/bin/python -m botnazorat
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF

echo "→ HTTPS (Caddy, Let's Encrypt)…"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    reverse_proxy 127.0.0.1:8080
}
EOF

echo "→ Firewall: 80 va 443 portlarini ochish…"
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT
netfilter-persistent save >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable --now botnazorat >/dev/null
systemctl restart caddy

sleep 8
echo
if systemctl is-active --quiet botnazorat; then
  echo "✅ Tayyor! Bot ishlayapti."
  echo "   Manzil: https://$DOMAIN"
  echo "   Endi Telegram'da nazorat botingizga /start yozing."
  echo
  echo "   Agar https ishlamasa: Oracle Cloud → Networking → VCN → Security List → 80 va 443 portlarini oching."
  echo "   Loglar: sudo journalctl -u botnazorat -f"
else
  echo "⚠️ Bot ishga tushmadi. Xatoni ko'rish: sudo journalctl -u botnazorat -n 50"
fi
