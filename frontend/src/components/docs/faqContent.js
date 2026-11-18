// "Preguntas y respuestas" for the three bots. `bot` = "crypto" | "fx" | "pepper". Grouped by category; `tags` feed the search box.
export function buildFaq(bot) {
  const fx = bot === "fx" || bot === "pepper";
  const pep = bot === "pepper";
  const cur = fx ? "USD" : "USDT";
  const broker = pep ? "Pepperstone" : fx ? "HFM" : "Bybit";
  const hours = fx ? "03:00–04:00, 08:00–12:00 y 19:00–00:00" : "03:00–05:00, 08:30–12:00 y 19:00–23:00";
  return [
    { cat: "General", q: "¿Qué hace exactamente este bot?", tags: ["resumen", "qué es", "embudo"],
      a: `Vigila ${fx ? "12 pares de forex y metales" : "los pares de futuros perpetuos USDT que actives"} y, dentro del horario operativo de Nueva York, pasa cada par por un embudo de IA: filtro técnico gratuito → modelo rápido que elige hasta 3 candidatos → modelo potente que decide BUY, SELL o PASS con un % de confianza. Si la confianza supera tu umbral, abre la operación con stop y objetivo y un tamaño calculado por riesgo fijo.` },
    { cat: "General", q: "¿Qué pasó con las estrategias, el backtest y el optimizador?", tags: ["estrategias", "backtest", "optimizador", "walk-forward", "laboratorio"],
      a: "Se eliminaron. Las estrategias fijas, el backtest, el optimizador, el walk-forward, los perfiles de riesgo, los modos de prueba y el laboratorio ya no existen: las entradas las decide el embudo de IA. Las operaciones antiguas siguen en el historial con el nombre de su estrategia." },
    { cat: "General", q: "¿Necesito dinero real para empezar?", tags: ["paper", "demo", "gratis"],
      a: `No. Arranca en paper trading con una cuenta ficticia de $500 ${cur} y precios reales. Todo funciona igual que en real. El plan de crecimiento recomienda no pasar a dinero real hasta superar la fase de validación: al menos 60 operaciones, profit factor ≥ 1,3, drawdown ≤ 10 % y 28 días.` },
    { cat: "General", q: "¿El bot garantiza beneficios?", tags: ["garantía", "riesgo", "pérdidas"],
      a: "No. Ningún sistema lo hace. Lo que sí mantiene es disciplina: riesgo fijo por operación, stops siempre puestos, límite de pérdida diaria y métricas honestas (las operaciones sombra existen para no engañarse). Opera solo con capital que puedas permitirte perder." },
    { cat: "General", q: "¿Puedo usar los tres bots a la vez?", tags: ["cripto", "forex", "ambos"],
      a: "Sí. Cada bot tiene su cuenta, ajustes, posiciones e historial independientes. Comparten la clave de acceso, la configuración de IA, Telegram y las copias de seguridad." },

    { cat: "Embudo IA", q: "¿Qué modelos de IA usa?", tags: ["modelo", "haiku", "sonnet", "rápido", "potente"],
      a: "Dos: uno rápido y barato para el Paso 1 y uno potente para el Paso 2. En Ajustes → IA eliges el proveedor y el modelo potente; el rápido se elige solo (Automático) o lo fijas tú." },
    { cat: "Embudo IA", q: "¿Cómo funciona el análisis multitemporal?", tags: ["4h", "1h", "15m", "multitemporal", "gemini"],
      a: "El modelo rápido (Gemini 3.5 Flash) mira primero 4H para la estructura y 1H para confirmar, y usa la temporalidad configurada del bot como gatillo de entrada. El modelo potente recibe ese mismo contexto junto con las velas de la temporalidad de entrada. Las operaciones contra la tendencia de 4H se bloquean sin gastar IA. Para seguir el esquema 4H → 1H → 15m, pon la temporalidad del bot en 15m." },
    { cat: "Embudo IA", q: "¿Cada cuánto se vuelve a analizar un par?", tags: ["5 minutos", "reanálisis", "tokens", "coste"],
      a: "El ciclo corre cada 60 segundos, pero el modelo rápido solo se vuelve a llamar si algún par se ha movido. Un par elegido como candidato vuelve al modelo potente cada 5 minutos mientras siga siendo candidato, hasta que salga una operación ejecutable o el bot llene el máximo de posiciones. Los pares descartados por el modelo rápido nunca llegan al modelo potente." },
    { cat: "Embudo IA", q: "¿Qué significa «Confianza IA para abrir operación»?", tags: ["confianza", "umbral", "desplegable"],
      a: "Es el mínimo que debe SUPERAR la confianza del modelo potente para ejecutar. Valores de 10 a 90 %. Con el umbral en 60 %, una decisión del 60 % no se ejecuta y una del 61 % sí. Lo que no se ejecuta se sigue como operación sombra." },
    { cat: "Embudo IA", q: "¿Qué umbral de confianza debería poner?", tags: ["calibrar", "umbral", "sombra"],
      a: "Empieza en 60 % y deja que se acumulen operaciones sombra. En Rendimiento → Operaciones sombra verás el resultado por tramo de confianza: sube el umbral si los tramos bajos pierden y bájalo si están dejando escapar ganancias." },
    { cat: "Embudo IA", q: "¿Por qué casi siempre responde PASS?", tags: ["pass", "no opera", "exigente"],
      a: "Porque está diseñado así. El modelo potente actúa como un trader exigente: prefiere no operar a entrar tarde o sin recorrido hasta el objetivo. Menos operaciones, pero mejor escogidas." },
    { cat: "Embudo IA", q: "¿Cuánto cuesta en llamadas a la IA?", tags: ["coste", "tokens", "llamadas"],
      a: "El filtro técnico es gratis y fuera de horario no hay ninguna llamada. Dentro de horario hay como mucho una llamada rápida por ciclo con movimiento y una llamada potente por candidato cada 5 minutos. El contador «IA hoy» de la tarjeta de decisiones muestra las llamadas del día." },

    { cat: "Horario y noticias", q: "¿En qué horario opera?", tags: ["horario", "nueva york", "ventanas", "fin de semana"],
      a: `En hora de Nueva York, todos los días incluidos sábado y domingo: ${hours}.${fx ? " En forex además el mercado debe estar abierto, así que durante el cierre del fin de semana no hay entradas." : ""} El cambio de hora de verano se aplica solo.` },
    { cat: "Horario y noticias", q: "¿Por qué el bot no abre operaciones?", tags: ["no opera", "fuera de horario", "bloqueado"],
      a: "Revisa por orden: 1) la franja de horario (¿fuera de horario?), 2) la pestaña «Eventos rojos» (¿par bloqueado?), 3) si ya está lleno el máximo de posiciones o se alcanzó el límite de pérdida diaria, 4) el registro: el embudo escribe por qué está en pausa, y 5) si la confianza de las decisiones queda por debajo de tu umbral (aparecen como sombra)." },
    { cat: "Horario y noticias", q: "¿Qué hace con las noticias de alto impacto?", tags: ["noticias", "eventos rojos", "calendario", "breakeven"],
      a: `Descarga una vez al día el calendario de eventos rojos. ${fx ? "Cada evento afecta a los pares que contienen su divisa." : "Los eventos del dólar afectan a todos los pares cripto."} Desde 15 minutos antes hasta 15 minutos después no abre entradas en esos pares. Quince minutos antes del evento pasa a breakeven las posiciones afectadas que van ganando y cierra las que van perdiendo.` },
    ...(fx ? [] : [{ cat: "Horario y noticias", q: "¿Qué pasa con mis órdenes límite pendientes cuando llega un evento rojo?", tags: ["límite", "maker", "pendientes", "cancelar"],
      a: "Al empezar el bloqueo se cancelan automáticamente las órdenes límite pendientes de los pares afectados por el evento. Las de pares no afectados siguen activas." }]),
    { cat: "Horario y noticias", q: "¿Sigue existiendo el agente de noticias con titulares y sentimiento?", tags: ["titulares", "sentimiento", "agente de noticias"],
      a: "No. Se eliminó junto con las operaciones automáticas por noticia. Solo queda la guardia de eventos rojos del calendario económico, que no consume IA." },

    { cat: "Riesgo", q: "¿Cómo se calcula el tamaño de cada operación?", tags: ["tamaño", "riesgo", "lote", "posición"],
      a: "Tamaño = (capital × riesgo %) ÷ distancia al stop. Si arriesgas el 1 % de $1.000, pierdes $10 cuando salta el stop, sea cual sea el par o lo lejos que esté el stop." },
    { cat: "Riesgo", q: "¿Quién decide el stop y el objetivo?", tags: ["stop", "objetivo", "atr", "sl", "tp"],
      a: "Con «SL/TP dinámico por ATR» encendido, son fijos: stop a 2×ATR y objetivo a 4×ATR (configurables), así cada ganadora vale 2R. Apagado, se usan los niveles que propone la IA si son sensatos (stop entre 0,5 y 4 ATR y relación ≥ 1:1); si no, se vuelve a los múltiplos de ATR." },
    { cat: "Riesgo", q: "¿Qué protecciones tiene una posición abierta?", tags: ["breakeven", "trailing", "parcial"],
      a: "Stop y objetivo desde la entrada, y de forma opcional stop a breakeven, trailing stop y cierre parcial de beneficios, cada uno con su interruptor. Además, la guardia de eventos rojos." },
    ...(fx ? [{ cat: "Riesgo", q: "¿De dónde sale el volumen en forex?", tags: ["volumen", "ticks", "tradingview", "yahoo"],
      a: "Del volumen de ticks de tu bróker, a través de la cuenta MetaTrader vinculada con MetaApi. El forex no tiene volumen centralizado y TradingView no ofrece una API pública de datos para esto. Sin cuenta conectada, el bot usa precios de Yahoo y mide la actividad con el rango de la vela y el ATR." }] : []),

    { cat: "Aprendizaje y revisión", q: "¿Qué son las reglas aprendidas?", tags: ["reglas", "aprendizaje"],
      a: "Frases que la IA extrae de las pérdidas evitables («no comprar pegado a una oferta con ADX bajo»). El modelo potente las recibe en cada análisis y responde PASS si la operación incumple alguna. Las gestionas en Ajustes → Reglas aprendidas." },
    { cat: "Aprendizaje y revisión", q: "¿Qué hace la revisión IA automática?", tags: ["revisión", "automática", "asesor"],
      a: "Con la frecuencia que elijas, la IA revisa tus operaciones reales y sombras y propone ajustes de riesgo, pares, apalancamiento, máximo de posiciones o umbral de confianza. Puede aplicarlos sola o solo avisarte por Telegram. Todo queda en Evolución y se puede deshacer." },
    { cat: "Aprendizaje y revisión", q: "¿Para qué sirve el plan de crecimiento?", tags: ["plan", "fases", "meta"],
      a: "Te dice en qué fase estás, qué criterios faltan para avanzar, cuánto capital necesitas para tu meta mensual y si vas por delante o por detrás de la proyección." },

    { cat: `Cuenta real · ${broker}`, q: "¿Cómo paso a modo REAL?", tags: ["real", "live", "credenciales"],
      a: fx ? `Vincula tu cuenta MetaTrader de ${broker} con MetaApi en Ajustes, prueba la conexión, detén el bot, cierra las posiciones paper y cambia a REAL escribiendo la palabra de confirmación.`
        : "Introduce tu API key de Bybit en Ajustes, prueba la conexión, detén el bot, cierra las posiciones paper y cambia a REAL escribiendo la palabra de confirmación." },
    { cat: `Cuenta real · ${broker}`, q: "¿Qué pasa si el servidor se cae con posiciones abiertas?", tags: ["caída", "servidor", "stop"],
      a: `El stop y el objetivo viajan con cada orden a ${broker}, así que la posición queda protegida. Lo que no se ejecutaría mientras el servidor esté caído es el breakeven, el trailing y la guardia de eventos rojos.` },

    { cat: "Telegram y VPS", q: "¿Qué avisos llegan por Telegram?", tags: ["telegram", "alertas"],
      a: "Aperturas y cierres, decisiones de la IA, resumen diario y semanal, plan, revisión IA y avisos de sistema. Eliges qué tipos recibes y qué bots avisan en cada chat." },
    { cat: "Telegram y VPS", q: "¿Qué tareas programadas necesita el VPS?", tags: ["cron", "vps", "tareas"],
      a: "Tres, que el instalador crea solo: revisión IA (08:00 UTC), resumen semanal (domingo 20:00 UTC) y copia de seguridad (03:00 UTC). El horario operativo y el calendario de eventos rojos los gestiona el propio bot." },

    { cat: "Seguridad", q: "¿Dónde se guardan mis API keys?", tags: ["api key", "cifrado", "seguridad"],
      a: "En la base de datos del servidor, cifradas con una clave que solo está en el archivo de entorno del servidor. El panel nunca las muestra completas." },
    { cat: "Seguridad", q: "¿Quién puede acceder al panel?", tags: ["acceso", "clave", "sesiones"],
      a: "Quien tenga la clave única del panel. Cambiarla en Ajustes → Seguridad cierra todas las sesiones abiertas. Usa HTTPS y una clave larga." },
  ];
}
