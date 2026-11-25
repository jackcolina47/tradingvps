// "Cómo usar" step-by-step guides for the three bots. `bot` = "crypto" | "fx" | "pepper".
export function buildHowTo(bot) {
  const fx = bot === "fx" || bot === "pepper";
  const pep = bot === "pepper";
  const cur = fx ? "USD" : "USDT";
  const brokerName = pep ? "Pepperstone (puente MetaTrader)" : fx ? "HFM (puente MetaTrader)" : "Bybit";
  const hours = fx ? "03:00–04:00, 08:00–12:00 y 19:00–00:00" : "las 24 horas (sin ventanas)";
  return [
    {
      id: "inicio", icon: "Compass", title: "1 · Primeros pasos y recorrido por el panel",
      steps: [
        "Entra con la clave del panel (una sola clave compartida, sin usuario). La sesión dura 7 días.",
        "En la pantalla inicial eliges el bot: Bybit AI Trader (cripto), HotForex AI Trading o Pepperstone AI trading (forex y metales). «Cambiar bot» en la cabecera te devuelve aquí.",
        "Cabecera: modo (Paper / REAL), origen de los datos, cuenta atrás del próximo ciclo, botones Escanear, Reiniciar (cuenta paper), Iniciar/Detener, Ajustes, Documentación, Instalar en tu VPS y Chatea con IA expert.",
        "Franja de horario: indica si el bot está dentro o fuera del horario operativo de Nueva York, la ventana activa y la cuenta atrás hasta la siguiente.",
        "Fila de KPIs: Balance, Equidad, PnL no realizado, PnL diario, tasa de acierto, operaciones y drawdown máximo.",
        "Watchlist: cada par muestra una insignia con la etapa del embudo en la que quedó (descartado por el filtro técnico, descartado por el modelo rápido, PASS, sombra o ejecutada).",
      ],
    },
    {
      id: "ciclo", icon: "Play", title: "2 · Iniciar el bot y entender el embudo IA",
      steps: [
        "Pulsa «Iniciar bot». El embudo corre en :00, :15, :30 y :45 de cada hora (con 8 segundos de margen) y cada 5 segundos se vigilan las posiciones abiertas.",
        `El embudo solo trabaja dentro del horario operativo (hora de Nueva York, todos los días): ${hours}. Fuera de él no se llama a la IA.`,
        "Paso 0: se calculan los indicadores de cada par y se descartan los que tienen ADX menor a 18 o volumen menor a 0,55× (en forex el volumen solo cuenta con cuenta demo o real conectada). No cuesta nada.",
        "Paso 1: Gemini 3.5 Flash evalúa cada par en 4H y 1H y elige de 0 a 3 candidatos, a favor de 4H o como rebote de contratendencia con objetivo corto. La temporalidad que eliges en Configuración es la de entrada (se recomienda 15m).",
        "Paso 2: el modelo potente analiza cada candidato y responde BUY, SELL o PASS con un % de confianza. Verás su razonamiento en «Decisiones del embudo IA» y en el registro.",
        "Si un candidato no acaba en operación, vuelve al modelo potente cada 15 minutos mientras el modelo rápido lo siga eligiendo. Se detiene cuando hay una operación ejecutable o el bot llena el máximo de posiciones.",
        "«Escanear» fuerza un ciclo inmediato. «Detener bot» no cierra posiciones: siguen protegidas por su stop y objetivo.",
      ],
    },
    {
      id: "confianza", icon: "Gauge", title: "3 · Elegir la confianza IA y el riesgo",
      steps: [
        "En el panel Configuración abre el desplegable «Confianza IA para abrir operación» y elige entre 10 y 90 %.",
        "Solo se ejecutan las decisiones con confianza MAYOR que ese valor. Con 60 %, una decisión del 60 % no se ejecuta y una del 61 % sí.",
        "Las decisiones que no superan el umbral se abren como operación sombra (una por vela). Revisa su resultado en Rendimiento → Operaciones sombra antes de mover el umbral.",
        `«Riesgo por operación (%)»: 1 % por defecto. Es el porcentaje de la equidad actual que pierdes si salta el stop, comisiones incluidas; el tamaño se ajusta solo en cada operación. Cuenta en ${cur}.`,
        "«SL/TP dinámico por ATR» viene encendido: stop a 2×ATR y objetivo a 4×ATR (puedes cambiar los múltiplos). Apagado: la IA propone los niveles.",
        "Ajusta también pares activos, temporalidad, apalancamiento, máximo de posiciones y límite de pérdida diaria. Pulsa «Guardar».",
      ],
    },
    {
      id: "eventos", icon: "CalendarClock", title: "4 · Eventos rojos",
      steps: [
        "Abre la pestaña «Eventos rojos»: verás los próximos eventos de alto impacto, su hora y los pares que bloquea cada uno.",
        "El calendario se descarga solo una vez al día. El botón de actualizar lo vuelve a pedir; úsalo con moderación porque la fuente limita las peticiones.",
        fx ? "Cada evento afecta solo a los pares con su divisa. Los del dólar afectan también al oro y la plata." : "En cripto los eventos del dólar (USD) afectan a todos los pares.",
        "Desde 15 minutos antes hasta 15 minutos después no se abren entradas en los pares afectados.",
        "15 minutos antes del evento: las posiciones afectadas en ganancia pasan a breakeven y las que van en pérdida se cierran (motivo NOTICIA).",
        ...(fx ? [] : ["Al empezar el bloqueo se cancelan las órdenes límite pendientes de los pares afectados. Las de otros pares no se tocan."]),
      ],
    },
    {
      id: "posiciones", icon: "ListOrdered", title: "5 · Posiciones, cierres e historial",
      steps: [
        "Pestaña «Posiciones»: par, lado, tamaño, entrada, precio actual, PnL, stop, objetivo y recorrido. Puedes cerrar una o «Cerrar todas».",
        "Los botones Buy / Sell del gráfico abren una operación manual con stop y objetivo por ATR y el riesgo que indiques. No pasa por la IA.",
        "Breakeven, trailing stop y cierre parcial se activan y regulan en Configuración; cada uno funciona por separado.",
        "Pestaña «Historial»: operaciones cerradas con su motivo (TP, SL, TRAILING, BREAKEVEN, NOTICIA, MANUAL…), resultado, pips y análisis de la IA. Puedes exportar a CSV.",
        "Las operaciones anteriores al embudo aparecen con el nombre de su antigua estrategia, solo como historial.",
      ],
    },
    {
      id: "rendimiento", icon: "BarChart3", title: "6 · Rendimiento y operaciones sombra",
      steps: [
        "Pestaña «Rendimiento»: resultado por par, lado, motivo de salida y día. Puedes apagar un par que pierde desde su fila.",
        "«Operaciones sombra»: qué habría pasado con las decisiones no ejecutadas. Valor del filtro positivo = el filtro te ahorró pérdidas.",
        "La tabla por confianza te dice a partir de qué porcentaje las decisiones de la IA son rentables: úsala para fijar el umbral.",
        ...(fx ? [] : ["Pestaña «Pares 30d»: ranking de pares y poda automática (avisa por Telegram y apaga al día siguiente el par que sigue perdiendo)."]),
      ],
    },
    {
      id: "plan", icon: "TrendingUp", title: "7 · Plan de crecimiento",
      steps: [
        "Indica capital inicial, aporte mensual y meta mensual, y elige un escenario: Prudente, Realista o Ambicioso.",
        "El plan muestra la proyección, las fases (validación en paper, semilla, crecimiento, consolidación, ingreso) y los criterios de cada una.",
        "Cuando cumples los criterios de tu fase aparece el aviso para avanzar; al hacerlo se ajusta el riesgo recomendado.",
        "«Revisar plan y ajustar el bot con IA» pide a la IA una revisión con tus operaciones reales y sombras.",
      ],
    },
    {
      id: "evolucion", icon: "GitBranch", title: "8 · Evolución y deshacer cambios",
      steps: [
        "Cada cambio de configuración (tuyo, de la revisión IA o del chat) queda registrado con la equidad de ese momento.",
        "Tras unas cuantas operaciones se compara el rendimiento antes y después: MEJOR, PEOR, IGUAL o PENDIENTE.",
        "Cualquier cambio se puede deshacer con un clic.",
      ],
    },
    {
      id: "ajustes", icon: "Settings2", title: "9 · Ajustes (botón «Ajustes» de la cabecera)",
      steps: [
        "IA: proveedor y modelo potente, y «Modelo rápido del embudo» (Automático elige el modelo barato del mismo proveedor). Botón para probar la conexión.",
        "Reglas aprendidas: lista con estadísticas, edición, apagado y «Limpiar con IA».",
        "Revisión IA automática: frecuencia (diaria o semanal), día y si aplica los cambios o solo avisa.",
        "Telegram: token, hasta dos chats, qué bots avisan en cada uno y qué tipos de alerta recibes.",
        "Copias de seguridad: crear, descargar y restaurar. Seguridad: cambiar la clave del panel.",
        `Cuenta real y modo de operación: credenciales de ${brokerName} y paso a REAL.`,
      ],
    },
    {
      id: "real", icon: "Landmark", title: `10 · Pasar a modo REAL con ${brokerName}`,
      steps: fx ? [
        "En Ajustes introduce la URL de tu puente MetaTrader, el Token del Puente y el ID de Cuenta (el puente ya guarda el login y la contraseña de MetaTrader).",
        "Pulsa «Guardar» y «Probar conexión»: se comprueba la conexión con el puente, el permiso de trading, el sufijo y los símbolos operables (en HFM Zero el oro y la plata se resuelven solos como GOLDb y SILVERb).",
        "Con la cuenta conectada, el embudo usa las velas y el volumen de ticks de tu bróker.",
        "Detén el bot, cierra las posiciones paper y cambia a REAL escribiendo la palabra de confirmación.",
        "Empieza con riesgo bajo. El stop y el objetivo se envían al bróker con cada orden, así que la posición queda protegida aunque el servidor se apague.",
      ] : [
        "Crea una API key en Bybit con permiso de contratos (sin retiros) y, si puedes, limitada a la IP de tu servidor.",
        "En Ajustes introduce la key y el secreto y pulsa «Probar conexión».",
        "Detén el bot, cierra las posiciones paper y cambia a REAL escribiendo la palabra de confirmación.",
        "Empieza con riesgo bajo. El stop y el objetivo se envían a Bybit con cada orden.",
        "Si Bybit bloquea la región del servidor, usa un VPS en una región permitida (por ejemplo Singapur).",
      ],
    },
    {
      id: "vps", icon: "Server", title: "11 · Ejecutarlo en tu propio servidor (VPS)",
      steps: [
        "Botón «Instalar en tu VPS» de la cabecera: descarga el paquete y sigue los pasos (subir el zip, ejecutar install.sh).",
        "El instalador programa tres tareas: revisión IA (08:00 UTC), resumen semanal (domingo 20:00 UTC) y copia de seguridad (03:00 UTC).",
        "El horario operativo y el calendario de eventos rojos no necesitan ninguna tarea externa: los gestiona el propio bot.",
        "Para actualizar, sube el zip nuevo y ejecuta update-zip.sh: conserva tus datos y ajustes.",
      ],
    },
    {
      id: "chat", icon: "MessageSquare", title: "12 · Chatea con IA expert",
      steps: [
        "Abre el chat desde la cabecera y pregunta en lenguaje normal: «¿por qué no ha operado hoy?», «¿qué umbral de confianza me conviene?».",
        "El asistente ve en vivo los tres bots, el horario, los eventos rojos, las señales, el registro y el rendimiento.",
        "Si propone un cambio de ajustes o una acción, aparece una tarjeta con Aplicar / Descartar: nada se aplica sin tu confirmación.",
        "No puede tocar credenciales ni cambiar a modo REAL.",
      ],
    },
  ];
}
