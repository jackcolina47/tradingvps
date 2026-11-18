#!/usr/bin/env bash
# Instalador para VPS Ubuntu/Debian. Uso: sudo bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta como root: sudo bash install.sh"; exit 1; }

echo "==> [1/6] Docker"
command -v curl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq curl; }
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq docker-compose-plugin; }
command -v openssl >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq openssl; }

echo "==> [2/6] Memoria (swap si hay poca RAM)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 3500 ] && ! swapon --show --noheadings | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    swap de 2G creado"
fi

echo "==> [3/6] Configuración"
if [ ! -f .env ]; then
  read -rp "Dominio para el panel (ej: bot.midominio.com). Vacío = acceso por IP solo HTTP: " DOMAIN
  SITE_ADDRESS="${DOMAIN:-:80}"
  MONGO_IMAGE="mongo:7"
  grep -q avx /proc/cpuinfo || MONGO_IMAGE="mongo:4.4"
  printf 'SITE_ADDRESS=%s\nMONGO_IMAGE=%s\n' "$SITE_ADDRESS" "$MONGO_IMAGE" > .env
fi

NEW_PW=""
if [ ! -f backend.env ]; then
  read -rsp "Contraseña inicial del panel (vacío = generar una): " PW; echo
  PW="${PW:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-14)}"
  PW="${PW//\'/}"
  read -rp "Clave IA (EMERGENT_LLM_KEY). Vacío = la pones luego en Ajustes → IA: " LLM
  {
    echo "MONGO_URL=mongodb://mongo:27017"
    echo "DB_NAME=cryptobot"
    echo "CORS_ORIGINS=*"
    echo "JWT_SECRET=$(openssl rand -hex 32)"
    echo "INITIAL_PASSWORD='$PW'"
    echo "WEBHOOK_CRON_SECRET=$(openssl rand -hex 24)"
    echo "CREDENTIAL_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr '+/' '-_')"
    echo "EMERGENT_LLM_KEY=$LLM"
  } > backend.env
  chmod 600 backend.env
  NEW_PW="$PW"
  echo "    Contraseña del panel: $PW   (guardada en backend.env como INITIAL_PASSWORD)"
fi

echo "==> [4/6] Construyendo e iniciando contenedores (la primera vez tarda 5-10 min)"
mkdir -p backups
docker compose up -d --build

echo "==> [5/6] Crons: revisión IA (08:00 UTC), resumen semanal (domingo 20:00 UTC) y copia de seguridad nocturna (03:00 UTC; los domingos también por Telegram)"
chmod +x cron-ai-review.sh cron-weekly-summary.sh backup.sh restore.sh update.sh update-zip.sh reset-password.sh clean-disk.sh
CRON_LINE="0 8 * * * /bin/bash $(pwd)/cron-ai-review.sh >> /var/log/cryptobot-cron.log 2>&1"
WK_LINE="0 20 * * 0 /bin/bash $(pwd)/cron-weekly-summary.sh >> /var/log/cryptobot-cron.log 2>&1"
BK_LINE="0 3 * * * /bin/bash $(pwd)/backup.sh >> /var/log/cryptobot-cron.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'cron-ai-review.sh' | grep -v 'cron-walk-forward.sh' | grep -v 'cron-weekly-summary.sh' | grep -v 'backup.sh' || true; echo "$CRON_LINE"; echo "$WK_LINE"; echo "$BK_LINE" ) | crontab -

echo "==> [6/6] Firewall y límites de logs"
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q 'Status: active'; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
fi
mkdir -p /etc/systemd/journald.conf.d
if [ ! -f /etc/systemd/journald.conf.d/ai-trader.conf ]; then
  printf '[Journal]\nSystemMaxUse=200M\n' > /etc/systemd/journald.conf.d/ai-trader.conf
  systemctl restart systemd-journald 2>/dev/null || true
fi
docker builder prune -af --filter "until=24h" >/dev/null 2>&1 || true

echo
echo "Esperando a que el backend responda..."
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS http://127.0.0.1:8001/api/ >/dev/null 2>&1 && echo "Backend OK" || echo "El backend aún no responde; revisa: docker compose logs -f backend"

SITE=$(grep '^SITE_ADDRESS=' .env | cut -d= -f2-)
IP=$(curl -fsS -4 --max-time 5 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo
echo "================= LISTO ================="
if [ "$SITE" = ":80" ]; then echo "Panel:  http://$IP"; else echo "Panel:  https://$SITE  (apunta el DNS A de $SITE a $IP)"; fi
[ -n "$NEW_PW" ] && echo "Contraseña del panel: $NEW_PW   <- guárdala, no se vuelve a mostrar"
echo "Logs:   cd $(pwd) && docker compose logs -f backend"
echo "Update: cd $(pwd) && ./update.sh"
echo "========================================="
