#!/usr/bin/env bash
# Actualiza una instalación hecha desde un zip con un paquete nuevo descargado del panel (botón «Instalar en tu VPS»).
# Conserva deploy/.env, deploy/backend.env y deploy/backups/ (no vienen en el zip) y reconstruye los contenedores.
# Uso: bash update-zip.sh /root/ai-trader-XXXX.zip
set -euo pipefail
( crontab -l 2>/dev/null | grep -v 'cron-walk-forward.sh' || true ) | crontab - 2>/dev/null || true

main() {
  cd "$(dirname "$0")"
  local ZIP="${1:?Indica el .zip descargado del panel (Instalar en tu VPS → Descargar)}"
  [ -f "$ZIP" ] || { echo "No existe $ZIP"; exit 1; }
  command -v unzip >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq unzip; }
  local APP_DIR; APP_DIR="$(cd .. && pwd)"
  local TMP; TMP=$(mktemp -d)
  unzip -q "$ZIP" -d "$TMP"
  local SRC; SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  if [ ! -d "$SRC/backend" ] || [ ! -d "$SRC/frontend" ] || [ ! -d "$SRC/deploy" ]; then
    echo "El zip no parece un paquete del bot (faltan backend/, frontend/ o deploy/)"; rm -rf "$TMP"; exit 1
  fi
  echo "==> Versión actual: $(grep -o '"version": *"[^"]*"' "$APP_DIR/VERSION" 2>/dev/null | head -1 | cut -d'"' -f4 || echo '?')"
  echo "==> Versión nueva:  $(grep -o '"version": *"[^"]*"' "$SRC/VERSION" 2>/dev/null | head -1 | cut -d'"' -f4 || echo '?')"
  for d in backend frontend scripts; do
    [ -d "$SRC/$d" ] || continue
    rm -rf "$APP_DIR/$d"
    cp -a "$SRC/$d" "$APP_DIR/$d"
  done
  cp -a "$SRC/deploy/." "$APP_DIR/deploy/"
  for f in VERSION README.md .dockerignore; do [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$APP_DIR/$f"; done
  rm -rf "$TMP"
  chmod +x "$APP_DIR"/deploy/*.sh
  echo "==> Reconstruyendo contenedores (2-10 min)"
  docker compose up -d --build
  docker image prune -f >/dev/null
  docker builder prune -af --filter "until=24h" >/dev/null 2>&1 || true
  echo "Actualizado. Logs: docker compose logs -f backend"
}

main "$@"
exit 0
