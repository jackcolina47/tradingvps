# Despliegue en VPS (Docker)

Guía completa paso a paso (también en el panel, botón «Instalar en tu VPS»): [`INSTALACION.md`](INSTALACION.md).

Opción A · desde el zip descargado en el panel:

```bash
apt-get update && apt-get install -y unzip
cd /root && unzip -q ai-trader-*.zip && cd ai-trader/deploy
sudo bash install.sh
```

Opción B · desde git:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone <URL_DEL_REPO> cryptobot && cd cryptobot/deploy
sudo bash install.sh
```

El instalador: instala Docker, crea `backend.env` con secretos aleatorios, construye los
contenedores (Mongo + backend FastAPI + Caddy sirviendo el frontend y proxy `/api`),
programa los crons de revisión IA (08:00 UTC), resumen semanal por Telegram (domingo 20:00 UTC)
y copia de seguridad nocturna de MongoDB (03:00 UTC, los domingos también por Telegram) y abre los puertos 80/443.

| Comando | Para qué |
|---|---|
| `docker compose ps` | estado de los servicios |
| `docker compose logs -f backend` | logs del bot |
| `docker compose restart backend` | reiniciar el bot |
| `./update.sh` | traer la última versión y reconstruir (instalación con git) |
| `bash update-zip.sh /root/ai-trader-NUEVO.zip` | actualizar con un zip nuevo descargado del panel (conserva .env, backend.env, backups/ y la base de datos) |
| `bash reset-password.sh` | poner una contraseña nueva al panel si la olvidaste |
| `bash clean-disk.sh` | liberar espacio: caché de compilación de Docker, imágenes huérfanas, journald (`--all` = toda la caché) |
| `bash cron-ai-review.sh` | disparar la revisión IA manualmente (Bybit, HotForex y Pepperstone) |
| `bash cron-weekly-summary.sh` | enviar ahora el resumen semanal por Telegram (Bybit, HotForex y Pepperstone) |
| `bash backup.sh` | copia de seguridad ahora en `backups/` (añade `--telegram` para enviarla también por Telegram) |
| `bash restore.sh backups/<archivo>` | restaurar una copia (.json.gz) · reemplaza la base de datos y deja los tres bots detenidos |

Archivos sensibles (no se suben a git): `deploy/.env`, `deploy/backend.env`, `deploy/backups/`.
**Haz copia de `backend.env`**: `CREDENTIAL_ENCRYPTION_KEY` cifra las claves de Bybit y el token del puente MetaTrader guardados;
si se pierde habrá que volver a introducirlas.

Copias de seguridad:
- Cada noche a las 03:00 UTC `backup.sh` pide al backend una copia completa de la base de datos (los tres bots) en `backups/cryptobot-AAAA-MM-DD_HHMMSS.json.gz`
  (formato propio del bot, sin mongodump) y borra las de más de 14 días (`BACKUP_KEEP_DAYS` en backend.env; siempre conserva las 3 últimas).
- Los domingos el backend envía la copia del día por Telegram al chat configurado (aviso "Conexión/sistema"); el bot de Telegram admite hasta 50 MB.
- Desde el panel (Ajustes → Copias de seguridad) puedes crear una copia ahora, descargar la última (o cualquiera guardada) y restaurar una copia
  guardada o un archivo de tu disco. Para restaurar hay que detener los tres bots; la clave de acceso actual se conserva y los bots quedan detenidos.
- La copia contiene las claves API cifradas con `CREDENTIAL_ENCRYPTION_KEY` (sin esa clave no se pueden leer) y la clave del panel como hash.
  Restaura siempre en una instalación con la misma `CREDENTIAL_ENCRYPTION_KEY` o vuelve a introducir las claves API después.
