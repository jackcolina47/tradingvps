// Content of the "Instalar en tu VPS" guide. Mirrors deploy/INSTALACION.md (shipped inside the zip).
export const REQUIREMENTS = [
  { icon: "Server", title: "Servidor (VPS)", text: "Ubuntu 22.04 / 24.04 o Debian 12 · 2 vCPU · 2–4 GB de RAM (con menos de 3,5 GB el instalador crea 2 GB de swap) · 20 GB de disco. Hetzner, Vultr, DigitalOcean, Contabo u OVH sirven." },
  { icon: "Globe", title: "Región del servidor", text: "Para operar en REAL con Bybit el VPS NO puede estar en EE. UU. (Bybit bloquea su API). Funcionan bien Europa (Ámsterdam, Fráncfort) o Asia (Tokio, Singapur). HotForex y Pepperstone (puente MetaTrader propio) no tienen esta restricción, pero la IP del VPS debe estar autorizada en el puente (allowed_ips)." },
  { icon: "KeyRound", title: "Acceso", text: "Usuario root (o con sudo) por SSH. Puertos 22 (SSH), 80 y 443 abiertos en el proveedor." },
  { icon: "Lock", title: "Dominio (opcional)", text: "Un subdominio tipo bot.midominio.com apuntando a la IP del VPS activa HTTPS automático con Let's Encrypt. Sin dominio, el panel va por http://IP." },
  { icon: "Monitor", title: "En tu ordenador", text: "Un terminal con ssh y scp (Windows 10/11: PowerShell ya los trae). También valen WinSCP o FileZilla (SFTP) para subir el zip." },
  { icon: "Cpu", title: "Clave de IA (opcional)", text: "Clave universal de Emergent (sk-emergent-…, en tu perfil de Emergent → Universal Key) o una API key de Google Gemini (gratis en aistudio.google.com) que pondrás después en Ajustes → IA." },
];

export const STEPS = [
  {
    id: "download", icon: "HardDriveDownload", title: "Descarga el paquete",
    text: "Pulsa «Descargar ZIP» arriba. Obtendrás ai-trader-<versión>-<fecha>.zip con todo el código actual (los tres bots: Bybit, HotForex y Pepperstone; el chat IA expert; backend, frontend y scripts de despliegue). El paquete se genera en el momento desde el código que estás usando, así que siempre incluye los últimos cambios. No incluye tus claves, la base de datos ni las copias de seguridad.",
  },
  {
    id: "ssh", icon: "SquareTerminal", title: "Conéctate al VPS por SSH",
    text: "Sustituye IP_DEL_VPS por la IP que te dio tu proveedor. La primera vez pregunta si confías en el servidor: escribe yes. Si tu usuario no es root, antepón sudo a los comandos.",
    code: [{ where: "Tu ordenador", cmd: "ssh root@IP_DEL_VPS" }],
  },
  {
    id: "prepare", icon: "Wrench", title: "Prepara el servidor",
    text: "Instala las dos herramientas que necesitamos para descomprimir y para que el instalador descargue Docker.",
    code: [{ where: "VPS", cmd: "apt-get update && apt-get install -y unzip curl" }],
  },
  {
    id: "upload", icon: "Upload", title: "Sube el zip al servidor",
    text: "Desde tu ordenador (no desde el VPS), en la carpeta donde se descargó el zip. Alternativa gráfica: WinSCP / FileZilla con SFTP (puerto 22) → arrastra el zip a /root/.",
    code: [{ where: "Tu ordenador", cmd: "scp ai-trader-*.zip root@IP_DEL_VPS:/root/" }],
  },
  {
    id: "unzip", icon: "Package", title: "Descomprime",
    text: "Se crea la carpeta /root/ai-trader. Dentro de deploy/ verás install.sh, docker-compose.yml, backup.sh, etc.",
    code: [{ where: "VPS", cmd: "cd /root && unzip -q ai-trader-*.zip && cd ai-trader/deploy && ls" }],
  },
  {
    id: "install", icon: "Rocket", title: "Ejecuta el instalador",
    text: "Tarda 5–10 minutos la primera vez. Te hará tres preguntas:",
    bullets: [
      "Dominio para el panel → bot.midominio.com (si ya apunta a la IP) o vacío para usar la IP solo con HTTP.",
      "Contraseña inicial del panel → la que quieras (mínimo 8 caracteres) o vacío para generar una aleatoria. Se muestra una sola vez: apúntala.",
      "Clave IA (EMERGENT_LLM_KEY) → pega tu clave universal de Emergent o déjalo vacío y configura Google Gemini después en Ajustes → IA.",
    ],
    code: [{ where: "VPS", cmd: "sudo bash install.sh" }],
    after: "Qué hace: [1/6] instala Docker · [2/6] crea swap si hay poca RAM · [3/6] genera deploy/.env y deploy/backend.env con secretos aleatorios (JWT_SECRET, WEBHOOK_CRON_SECRET, CREDENTIAL_ENCRYPTION_KEY) · [4/6] construye y arranca 3 contenedores: mongo (base de datos), backend (motores + API) y web (panel + proxy HTTPS con Caddy) · [5/6] programa los crons: revisión IA 08:00 UTC, resumen semanal domingos 20:00, copia de seguridad 03:00 · [6/6] abre 80/443 en ufw si está activo. Al final imprime la dirección del panel y la contraseña.",
  },
  {
    id: "domain", icon: "Globe", title: "Dominio y HTTPS (opcional)",
    text: "En tu proveedor de DNS crea un registro A «bot» → IP_DEL_VPS. Caddy pide el certificado de Let's Encrypt automáticamente la primera vez que alguien visita https://bot.midominio.com (si el DNS aún no ha propagado, reintenta en unos minutos). Para pasar de IP a dominio más tarde:",
    code: [{ where: "VPS · ai-trader/deploy", cmd: "sed -i 's/^SITE_ADDRESS=.*/SITE_ADDRESS=bot.midominio.com/' .env && docker compose up -d" }],
  },
  {
    id: "first", icon: "ShieldCheck", title: "Primer acceso y configuración",
    text: "Abre http://IP_DEL_VPS (o https://tu-dominio), introduce la contraseña y elige un bot (Bybit, HotForex o Pepperstone). En Ajustes:",
    bullets: [
      "Seguridad → cambia la contraseña.",
      "Inteligencia artificial → Emergent (si pusiste la clave) o Google Gemini con tu API key · «Probar conexión».",
      "Telegram → token del bot + «Detectar chat ID» + «Enviar prueba».",
      "Bybit · cuenta real / HotForex y Pepperstone (puente MetaTrader propio: URL, token e ID de cuenta; cTrader no es compatible) → solo cuando quieras pasar a REAL.",
      "Pulsa «Iniciar bot». Si el servidor se reinicia, los contenedores arrancan solos y el bot continúa donde estaba.",
    ],
  },
  {
    id: "migrate", icon: "Database", title: "Traer tus datos desde este panel (opcional)",
    text: "Aquí: Ajustes → Copias de seguridad → «Crear copia ahora» → «Descargar». En el VPS: Ajustes → Copias de seguridad → «Restaurar desde archivo…», elige el .json.gz y escribe RESTAURAR (todos los bots detenidos). La contraseña del VPS se conserva.",
    after: "Las API keys (Bybit, token del puente MetaTrader, Gemini, Telegram) van cifradas con la CREDENTIAL_ENCRYPTION_KEY de la instalación de origen: en el VPS tendrás que volver a introducirlas en Ajustes.",
  },
  {
    id: "update", icon: "RefreshCw", title: "Actualizar a una versión nueva",
    text: "Descarga el zip nuevo aquí mismo, súbelo al VPS y ejecuta update-zip.sh: sustituye el código, conserva .env, backend.env, backups/ y la base de datos, y reconstruye los contenedores (2–10 min). Al arrancar, el backend aplica solo las migraciones necesarias. Las posiciones abiertas no se tocan.",
    code: [
      { where: "Tu ordenador", cmd: "scp ai-trader-*.zip root@IP_DEL_VPS:/root/" },
      { where: "VPS", cmd: "cd /root/ai-trader/deploy && bash update-zip.sh /root/ai-trader-NUEVO.zip" },
    ],
  },
  {
    id: "security", icon: "Lock", title: "Seguridad y mantenimiento",
    bullets: [
      "Guarda deploy/backend.env en un lugar seguro: CREDENTIAL_ENCRYPTION_KEY cifra las API keys guardadas; sin ella una copia de seguridad no permite recuperarlas.",
      "El backend solo escucha en 127.0.0.1:8001 dentro del VPS; el único acceso público es el panel por 80/443, protegido por contraseña, JWT y bloqueo tras 5 intentos fallidos.",
      "Mantén el sistema al día (apt-get update && apt-get upgrade -y). Tras un reinicio no hay que hacer nada: arranque automático.",
      "Las copias nocturnas se guardan 14 días en deploy/backups/ (los domingos también por Telegram). Descárgalas de vez en cuando a tu ordenador.",
      "Disco: la app apenas escribe; lo que crece es la caché de compilación de Docker en cada actualización. update.sh/update-zip.sh la limpian solos, los logs de contenedores están limitados (3×10 MB) y journald a 200 MB. Si aun así sube: bash clean-disk.sh.",
      "Si usas firewall propio, deja abiertos 22 (SSH), 80 y 443.",
    ],
  },
];

export const COMMANDS = [
  ["docker compose ps", "estado de los 3 servicios"],
  ["docker compose logs -f backend", "log del bot en vivo (Ctrl+C para salir)"],
  ["docker compose restart backend", "reiniciar el bot"],
  ["docker compose down && docker compose up -d", "parar y arrancar todo (los datos se conservan)"],
  ["bash backup.sh", "copia de seguridad ahora en backups/ (--telegram para enviarla también)"],
  ["bash restore.sh backups/<archivo>.json.gz", "restaurar una copia desde la terminal"],
  ["bash reset-password.sh", "poner una contraseña nueva si la olvidaste"],
  ["bash clean-disk.sh", "liberar espacio en disco (caché de Docker, imágenes huérfanas, journald)"],
  ["bash cron-ai-review.sh", "lanzar ahora la revisión IA de los tres bots"],
  ["bash update-zip.sh /root/ai-trader-NUEVO.zip", "actualizar a una versión nueva"],
];

export const TROUBLE = [
  ["install.sh termina con «El backend aún no responde»", "Mira docker compose logs -f backend; suele ser la primera compilación todavía en curso. Espera 1–2 minutos y abre el panel."],
  ["El panel no carga por el dominio", "Comprueba el DNS (ping bot.midominio.com debe devolver la IP del VPS). Caddy necesita los puertos 80 y 443 abiertos para emitir el certificado."],
  ["«port is already allocated» al arrancar", "Otro servicio usa el puerto 80/443 (Apache/Nginx). Páralo: systemctl disable --now apache2 nginx y vuelve a ejecutar docker compose up -d."],
  ["Mongo se reinicia en bucle", "La CPU no soporta AVX; el instalador elige mongo:4.4 automáticamente. Si lo cambiaste a mano, vuelve a MONGO_IMAGE=mongo:4.4 en deploy/.env."],
  ["Bybit «403» o región no permitida", "El VPS está en una región bloqueada por Bybit (p. ej. EE. UU.). En paper el bot usa Bitget como espejo de precios; para REAL necesitas otra región."],
  ["Yahoo Finance «429 Too Many Requests» (HotForex / Pepperstone)", "Límite temporal de Yahoo; el bot reintenta solo. No requiere acción."],
  ["Olvidé la contraseña del panel", "En el VPS: cd /root/ai-trader/deploy && bash reset-password.sh."],
  ["Quiero empezar de cero", "docker compose down -v borra también la base de datos (y las claves guardadas). Luego docker compose up -d."],
  ["Poco espacio en disco", "La app apenas escribe (base de datos de pocos MB). Lo que crece es la caché de compilación de Docker tras cada actualización: ejecuta bash clean-disk.sh en deploy/ (o docker builder prune -af). Los logs de contenedores ya están limitados a 3×10 MB y journald a 200 MB."],
];
