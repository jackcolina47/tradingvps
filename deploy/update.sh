#!/usr/bin/env bash
# Actualiza el bot con la última versión y reinicia los contenedores.
# - Instalación con git clone: trae los cambios del repositorio.
# - Instalación desde zip (botón «Instalar en tu VPS»): usa update-zip.sh con el paquete nuevo.
set -euo pipefail
( crontab -l 2>/dev/null | grep -v 'cron-walk-forward.sh' || true ) | crontab - 2>/dev/null || true
cd "$(dirname "$0")"
if [ -d ../.git ]; then
  git pull --ff-only
else
  echo "Esta instalación se hizo desde un zip. Descarga el paquete nuevo en el panel (Instalar en tu VPS → Descargar) y ejecuta:"
  echo "    bash update-zip.sh /ruta/al/ai-trader-XXXX.zip"
  exit 1
fi
docker compose up -d --build
docker image prune -f >/dev/null
docker builder prune -af --filter "until=24h" >/dev/null 2>&1 || true
echo "Actualizado. Logs: docker compose logs -f backend"
