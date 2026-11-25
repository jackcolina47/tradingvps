# Instalar AI Trader en tu propio VPS · guía paso a paso

Esta guía instala los tres bots (Bybit AI Trader, HotForex AI Trading y Pepperstone AI trading) con su panel web en un servidor
Linux propio usando Docker. Todo queda funcionando 24/7, con HTTPS automático si usas un dominio,
copias de seguridad nocturnas y las tareas programadas (revisión IA y resumen semanal).

La misma guía, con botones para copiar cada comando, está en el panel: botón **«Instalar en tu VPS»**.

---

## 0. Qué necesitas

| Qué | Recomendación |
|---|---|
| VPS | Ubuntu 22.04 / 24.04 (o Debian 12) · 2 vCPU · 2–4 GB RAM (si hay menos de 3,5 GB el instalador crea 2 GB de swap) · 20 GB de disco |
| Región | Si vas a operar en REAL con Bybit, el servidor NO puede estar en EE. UU. (Bybit bloquea su API). Funcionan bien Europa (Ámsterdam, Fráncfort) o Asia (Tokio, Singapur). HotForex y Pepperstone (puente MetaTrader propio) no tienen esa restricción |
| Acceso | Usuario `root` (o con `sudo`) por SSH; puertos 22, 80 y 443 abiertos |
| Dominio (opcional) | Un subdominio tipo `bot.midominio.com` apuntando a la IP del VPS → HTTPS automático (Let's Encrypt). Sin dominio el panel va por `http://IP` |
| En tu ordenador | Un terminal con `ssh` y `scp` (Windows 10/11: PowerShell ya los trae; también sirven WinSCP o FileZilla para subir el zip) |
| Claves (opcional) | Clave universal de Emergent (`sk-emergent-…`, en tu perfil de Emergent → Universal Key) **o** una API key de Google Gemini (gratis en aistudio.google.com) que pondrás luego en Ajustes → IA |

Proveedores habituales: Hetzner, Vultr, DigitalOcean, Contabo, OVH. Cualquier VPS con Ubuntu sirve.

---

## 1. Descarga el paquete

En el panel pulsa **Instalar en tu VPS → Descargar ZIP**. Obtendrás `ai-trader-<versión>-<fecha>.zip` con todo el código
(backend, frontend, scripts de despliegue). **No** incluye tus claves, la base de datos ni las copias de seguridad.

## 2. Conéctate al VPS

```bash
ssh root@IP_DEL_VPS
```

La primera vez pregunta si confías en el servidor: escribe `yes`. Si tu proveedor te dio un usuario distinto de root, entra con él y antepón `sudo` a los comandos.

## 3. Prepara el servidor

```bash
apt-get update && apt-get install -y unzip curl
```

## 4. Sube el zip al servidor

Desde **tu ordenador** (no desde el VPS), en la carpeta donde está el zip:

```bash
scp ai-trader-*.zip root@IP_DEL_VPS:/root/
```

Alternativa gráfica: WinSCP / FileZilla (protocolo SFTP, puerto 22) → arrastra el zip a `/root/`.

## 5. Descomprime

En el VPS:

```bash
cd /root && unzip -q ai-trader-*.zip && cd ai-trader/deploy && ls
```

Verás `install.sh`, `docker-compose.yml`, `backup.sh`, etc.

## 6. Ejecuta el instalador

```bash
sudo bash install.sh
```

Te hará tres preguntas:

1. **Dominio para el panel** → `bot.midominio.com` (si ya apunta a la IP) o vacío para usar la IP solo con HTTP.
2. **Contraseña inicial del panel** → la que quieras (mínimo 8 caracteres) o vacío para que genere una aleatoria. **Se muestra una sola vez: apúntala.**
3. **Clave IA (EMERGENT_LLM_KEY)** → pega tu clave universal de Emergent o déjalo vacío y configura después Google Gemini en Ajustes → IA.

Qué hace el instalador (6 pasos, 5–10 minutos la primera vez):

- `[1/6]` instala Docker y Docker Compose.
- `[2/6]` crea 2 GB de swap si el servidor tiene menos de 3,5 GB de RAM.
- `[3/6]` genera `deploy/.env` (dominio, imagen de Mongo según la CPU) y `deploy/backend.env` con secretos aleatorios (`JWT_SECRET`, `WEBHOOK_CRON_SECRET`, `CREDENTIAL_ENCRYPTION_KEY`) y tu contraseña.
- `[4/6]` construye y arranca 3 contenedores: `mongo` (base de datos), `backend` (motores de trading + API) y `web` (panel + proxy HTTPS con Caddy).
- `[5/6]` programa en el cron del sistema: revisión IA 08:00 UTC, resumen semanal domingos 20:00 UTC y copia de seguridad 03:00 UTC.
- `[6/6]` abre 80/443 en el firewall `ufw` si está activo.

Al final imprime la dirección del panel y la contraseña.

## 7. Dominio y HTTPS (opcional)

En tu proveedor de DNS crea un registro **A** `bot` → `IP_DEL_VPS`. Caddy pide el certificado de Let's Encrypt automáticamente la primera vez
que alguien visita `https://bot.midominio.com` (tarda unos segundos; si el DNS aún no ha propagado, reintenta en unos minutos).
Para cambiar de IP a dominio después: edita `SITE_ADDRESS=` en `deploy/.env` y ejecuta `docker compose up -d`.

## 8. Primer acceso

1. Abre `http://IP_DEL_VPS` (o `https://tu-dominio`) e introduce la contraseña.
2. Elige un bot (Bybit, HotForex o Pepperstone) y ve a **Ajustes**:
   - **Seguridad**: cambia la contraseña.
   - **Inteligencia artificial**: elige Emergent (si pusiste la clave) o Google Gemini con tu API key; pulsa «Probar conexión».
   - **Telegram**: token del bot + «Detectar chat ID» + «Enviar prueba».
   - **Bybit · cuenta real** / **HotForex y Pepperstone (puente MetaTrader propio: URL, token e ID de cuenta; también con `MT_BRIDGE_URL` / `MT_BRIDGE_TOKEN` en `backend.env`; la IP del VPS debe estar en `allowed_ips` del puente)**: solo cuando quieras pasar a REAL.
3. Pulsa **Iniciar bot**. Si el servidor se reinicia, los contenedores arrancan solos y el bot continúa donde estaba.

## 9. Traer tus datos desde el panel de desarrollo (opcional)

1. En el panel de origen: Ajustes → Copias de seguridad → **Crear copia ahora** → **Descargar**.
2. En el VPS: Ajustes → Copias de seguridad → **Restaurar desde archivo…** → elige el `.json.gz`, escribe `RESTAURAR`. Los tres bots deben estar detenidos.
3. La contraseña del VPS se conserva. Las API keys (Bybit, token del puente MetaTrader, Gemini, Telegram) van cifradas con la `CREDENTIAL_ENCRYPTION_KEY` de la instalación de origen,
   así que en el VPS tendrás que volver a introducirlas en Ajustes.

## 10. Comandos útiles (dentro de `ai-trader/deploy`)

| Comando | Para qué |
|---|---|
| `docker compose ps` | estado de los 3 servicios |
| `docker compose logs -f backend` | ver el log del bot en vivo (Ctrl+C para salir) |
| `docker compose restart backend` | reiniciar el bot |
| `docker compose down` / `docker compose up -d` | parar todo / arrancar todo (los datos se conservan) |
| `bash backup.sh` | copia de seguridad ahora en `backups/` (`--telegram` para enviarla también) |
| `bash restore.sh backups/<archivo>.json.gz` | restaurar una copia desde la terminal |
| `bash reset-password.sh` | poner una contraseña nueva si la olvidaste |
| `bash clean-disk.sh` | liberar espacio en disco (caché de compilación de Docker, imágenes huérfanas, journald); `--all` borra toda la caché |
| `bash cron-ai-review.sh` | lanzar ahora la revisión IA de los tres bots |
| `bash update-zip.sh /root/ai-trader-NUEVO.zip` | actualizar a una versión nueva (ver 11) |

## 11. Actualizar a una versión nueva

1. Descarga el zip nuevo en el panel (Instalar en tu VPS → Descargar) y súbelo al VPS: `scp ai-trader-*.zip root@IP_DEL_VPS:/root/`.
2. En el VPS: `cd /root/ai-trader/deploy && bash update-zip.sh /root/ai-trader-NUEVO.zip`.
   Sustituye el código, conserva `.env`, `backend.env`, `backups/` y la base de datos, y reconstruye los contenedores (2–10 min). Las posiciones abiertas no se tocan.

## 12. Seguridad y mantenimiento

- **Guarda `deploy/backend.env` en un lugar seguro**: `CREDENTIAL_ENCRYPTION_KEY` cifra las API keys guardadas; sin ella una copia de seguridad no permite recuperarlas.
- El backend solo escucha en `127.0.0.1:8001` dentro del VPS; el único acceso público es el panel por 80/443, protegido por contraseña, JWT y bloqueo tras 5 intentos fallidos.
- Mantén el sistema al día: `apt-get update && apt-get upgrade -y` de vez en cuando; `docker compose up -d` tras un reinicio no es necesario (arranque automático).
- Las copias nocturnas se guardan 14 días en `deploy/backups/` (los domingos también por Telegram). Descárgalas de vez en cuando a tu ordenador.
- **Disco**: la app apenas escribe (base de datos de pocos MB, copias de KB). Lo que crece es Docker: cada actualización deja caché de compilación (0,5–1 GB).
  `update.sh`/`update-zip.sh` la limpian solos; los logs de contenedores están limitados a 3 × 10 MB y journald a 200 MB. Si el disco sube igual: `bash clean-disk.sh`.
- Si usas firewall propio, deja abiertos 22 (SSH), 80 y 443.

## 13. Problemas frecuentes

| Síntoma | Qué hacer |
|---|---|
| `install.sh` termina con «El backend aún no responde» | `docker compose logs -f backend`; suele ser la primera compilación todavía en curso. Espera 1–2 min y abre el panel |
| El panel no carga por el dominio | Comprueba el DNS (`ping bot.midominio.com` debe dar la IP del VPS). Caddy necesita que los puertos 80 y 443 estén abiertos para emitir el certificado |
| «port is already allocated» al arrancar | Otro servicio usa el puerto 80/443 (Apache/Nginx). Desinstálalo o páralo: `systemctl disable --now apache2 nginx` |
| Mongo se reinicia en bucle | La CPU no soporta AVX; el instalador elige `mongo:4.4` automáticamente. Si lo cambiaste a mano, vuelve a `MONGO_IMAGE=mongo:4.4` en `deploy/.env` |
| Bybit «403» o «región no permitida» | El VPS está en una región bloqueada por Bybit (p. ej. EE. UU.). En paper el bot usa Bitget como espejo de precios; para REAL necesitas otra región |
| Yahoo Finance «429 Too Many Requests» (HotForex / Pepperstone) | Es un límite temporal de Yahoo; el bot reintenta solo. No requiere acción |
| Olvidé la contraseña | `bash reset-password.sh` en `deploy/` |
| Quiero empezar de cero | `docker compose down -v` borra también la base de datos (¡y las claves guardadas!). Luego `docker compose up -d` |
| Poco espacio en disco | `bash clean-disk.sh` (caché de compilación de Docker, imágenes antiguas, journald). Para ver qué ocupa: `docker system df` y `df -h /` |

---

**Aviso**: el trading con apalancamiento implica alto riesgo. Prueba siempre en paper antes de pasar a REAL y nunca operes con dinero que no puedas perder.


## Novedades de esta versión (Embudo IA)

- Las entradas las decide un embudo de IA (filtro técnico → modelo rápido → modelo potente). Ya no hay estrategias fijas, backtest, optimizador ni walk-forward.
- El bot solo busca entradas en el horario operativo de Nueva York y bloquea ±15 min los eventos rojos del calendario económico. No necesita ninguna tarea programada para ello.
- El cron `cron-walk-forward.sh` ya no existe. `install.sh` y `update-zip.sh` lo quitan del crontab al ejecutarse; si actualizas a mano, bórralo con `crontab -e`.
- El servidor necesita salida a Internet hacia el proveedor de IA, el calendario económico (ForexFactory) y, según el bot, Bybit/Bitget, Yahoo Finance y tu puente MetaTrader (puerto 8000 del VPS Windows).
- Mantén la hora del servidor sincronizada (NTP): el horario operativo se calcula con la hora real de Nueva York.
