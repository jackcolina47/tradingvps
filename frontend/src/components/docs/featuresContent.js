// "Características" content for the three bots. `bot` = "crypto" | "fx" | "pepper". Every claim here mirrors a real feature of the app.
export function buildFeatures(bot) {
  const fx = bot === "fx" || bot === "pepper";
  const pep = bot === "pepper";
  const name = pep ? "Pepperstone AI trading" : fx ? "HotForex AI Trading" : "Bybit AI Trader";
  const market = fx ? "forex y metales (oro, plata y 10 pares mayores/cruces)" : "futuros perpetuos USDT de Bybit";
  const broker = pep ? "tu cuenta MetaTrader de Pepperstone a través de MetaApi.cloud" : fx ? "tu cuenta HFM (HotForex) a través de MetaApi.cloud" : "tu cuenta de Bybit (API V5 firmada)";
  const cur = fx ? "USD" : "USDT";
  const hours = fx ? "03:00–04:00, 08:00–12:00 y 19:00–00:00" : "las 24 horas (sin ventanas)";
  const hero = {
    title: `${name}: un embudo de IA que opera solo en las horas buenas y se aparta de las noticias`,
    text: `Analiza ${market} cada minuto. Un filtro técnico gratuito descarta lo que no merece atención, un modelo rápido elige como mucho 3 candidatos y un modelo potente los estudia como un trader profesional y responde BUY, SELL o PASS con un porcentaje de confianza. Solo se abre la operación si esa confianza supera el umbral que tú eliges. El tamaño siempre lo fija el riesgo por operación (% del capital). Empieza en paper trading y pasa a modo REAL sobre ${broker} cuando las métricas lo avalen.`,
    stats: [
      ["3", "etapas del embudo IA"],
      ["15 min", "ciclo de análisis"],
      ["5 min", "reanálisis de candidatos"],
      ["±15 min", "bloqueo por evento rojo"],
      ["10–90 %", "umbral de confianza"],
      [pep ? "Pepperstone" : fx ? "HFM" : "Bybit", "modo REAL integrado"],
    ],
  };
  const sections = [
    {
      icon: "Filter", title: "Embudo IA en tres etapas",
      text: "Sustituye a las antiguas estrategias fijas, al backtest y al optimizador. Gasta tokens solo donde hay una oportunidad real.",
      bullets: [
        "Paso 0 · filtro técnico sin IA (gratis): EMA 20/50/200, RSI, MACD, ADX, ATR, actividad y zonas de oferta y demanda. Descarta los pares planos y lejos de cualquier zona.",
        "Paso 1 · modelo rápido multitemporal (Gemini 3.5 Flash): evalúa cada par en 4H (estructura) y 1H (confirmación) y usa la temporalidad del bot como gatillo. Elige de 0 a 3 candidatos en una sola llamada.",
        "Contratendencia controlada: se prefiere operar a favor de 4H, pero se permite el lado contrario como rebote técnico si la temporalidad de entrada muestra un giro claro. En ese caso el stop sigue fijo y el objetivo lo fija la IA antes de la primera EMA o zona de 4H/1H, con R:R mínimo de 1,5; si no hay ese recorrido, no se abre.",
        "Paso 2 · modelo potente: analiza cada candidato con las últimas velas, la temporalidad superior, los eventos próximos y tus reglas aprendidas. Responde BUY, SELL o PASS con un % de confianza.",
        "Sincronizado con el reloj: el embudo se dispara en :00, :15, :30 y :45 de cada hora con 8 segundos de margen, cuando la vela ya está cerrada y el volumen y los indicadores son definitivos.",
        "Reanálisis cada 15 minutos: mientras un par siga siendo candidato del modelo rápido, vuelve al modelo potente en cada disparo hasta que salga una operación ejecutable o el bot llene el máximo de posiciones.",
        "Los pares que el modelo rápido descarta no llegan nunca al modelo potente.",
      ],
    },
    {
      icon: "Gauge", title: "Confianza IA para abrir operación",
      text: "Un desplegable en Configuración con los valores 10, 20, 30, 40, 50, 60, 70, 80 y 90 %.",
      bullets: [
        "Confianza mayor que el umbral → la operación se ejecuta.",
        "Confianza igual o menor → no se ejecuta, pero se abre como operación sombra (BAJA_CONFIANZA) para medir qué habría pasado.",
        "En Rendimiento → Operaciones sombra ves el resultado por tramo de confianza y puedes calibrar el umbral con datos, no con intuición.",
      ],
    },
    {
      icon: "Clock", title: "Horario operativo de Nueva York",
      text: `El embudo solo busca entradas dentro de estas ventanas (hora de Nueva York), todos los días: ${hours}.`,
      bullets: [
        "Una franja bajo la cabecera muestra si estás dentro o fuera de horario, la ventana activa y la cuenta atrás hasta la siguiente.",
        "Fuera de horario no se llama a la IA: coste cero. Las posiciones abiertas siguen gestionándose con stop, objetivo, breakeven y trailing.",
        fx ? "En forex, además, el mercado debe estar abierto: durante el cierre del fin de semana no hay entradas aunque la ventana esté activa." : "El cambio de hora de EE. UU. se aplica solo: las ventanas siempre siguen la hora real de Nueva York.",
      ],
    },
    {
      icon: "CalendarClock", title: "Guardia de eventos rojos",
      text: "El calendario económico de alto impacto se descarga una vez al día y protege tanto las entradas como las posiciones abiertas.",
      bullets: [
        fx ? "Cada evento bloquea solo los pares que contienen su divisa (un dato del euro bloquea EURUSD, EURJPY, EURCHF…; uno del dólar bloquea también oro y plata)." : "Los eventos rojos del dólar (USD) bloquean todos los pares cripto.",
        "Bloqueo de nuevas entradas desde 15 minutos antes hasta 15 minutos después del evento.",
        "15 minutos antes: las posiciones afectadas que van ganando pasan su stop a breakeven; las que van perdiendo se cierran al momento (motivo NOTICIA).",
        fx ? "Las operaciones cerradas por NOTICIA no generan reglas aprendidas: no fue un error de análisis." : "Al empezar el bloqueo se cancelan solas las órdenes límite pendientes de los pares afectados; las del resto siguen activas.",
        "Pestaña «Eventos rojos»: lista de los próximos eventos, los pares que bloquea cada uno y botón para actualizar el calendario.",
      ],
    },
    {
      icon: "ShieldCheck", title: "Gestión de riesgo fija",
      text: "La IA decide la dirección y los niveles; el tamaño lo decide siempre el riesgo.",
      bullets: [
        "Tamaño = (equidad actual × riesgo %) ÷ (distancia al stop + comisiones). Con el 1 % por defecto, cada stop cuesta el 1 % de la cuenta, sea cual sea el par. El redondeo al lote puede dejarlo algo por debajo, nunca por encima.",
        "«SL/TP dinámico por ATR» encendido: stop a 2×ATR y objetivo a 4×ATR (configurables) → relación 1:2. Apagado: se usan los niveles que propone la IA si son sensatos (stop entre 0,5 y 4 ATR, R:R ≥ 1).",
        "Máximo de posiciones simultáneas, una posición por par y límite de pérdida diaria que pausa las entradas hasta el día siguiente.",
        "Stop a breakeven, trailing stop y cierre parcial de beneficios, cada uno con su interruptor y su nivel en R.",
        fx ? "Regla del viernes: sin entradas nuevas desde las 12:00 de Nueva York y cierre de todas las posiciones a las 15:30, ganen o pierdan. Además, tope de exposición por divisa, guardia de rollover (17:00 NY) y tope de riesgo para el lote mínimo (1 % por defecto: si el lote mínimo arriesga más, la operación no se abre)." : "Órdenes de entrada a mercado o límite maker (comisión menor) con espera configurable.",
      ],
    },
    {
      icon: "BarChart3", title: fx ? "Volumen de ticks del bróker" : "Datos de mercado",
      text: fx ? "El forex no tiene un volumen centralizado; el dato útil es el volumen de ticks de tu propio bróker." : "Velas, volumen y precios en tiempo real.",
      bullets: fx ? [
        "Con la cuenta MetaTrader vinculada, el embudo usa las velas y el volumen de ticks de tu bróker (lo mismo que ves en MetaTrader).",
        "Sin cuenta conectada (paper con Yahoo Finance) el volumen no es fiable: se sustituye por la actividad medida con el rango de la vela frente a su media y el ATR.",
        "El modelo potente sabe en cada análisis si está viendo volumen de ticks real o solo el rango de las velas.",
      ] : [
        "Datos de Bybit con volumen real; si Bybit no responde desde la región del servidor, respaldo automático con Bitget.",
        "Monitor cada 5 s: marca a mercado, PnL no realizado, stop/objetivo/trailing y cierres detectados en el exchange.",
        "Gráfico con EMAs, Bollinger, VWAP, RSI, MACD, ADX, ATR, zonas de oferta/demanda y herramientas de dibujo.",
      ],
    },
    {
      icon: "GraduationCap", title: "Reglas aprendidas",
      text: "El bot aprende de sus propias operaciones y el modelo potente aplica esas reglas en cada análisis.",
      bullets: [
        "Con «Aprendizaje automático» encendido, cada operación cerrada se analiza: de una pérdida evitable sale una regla nueva.",
        "Si una regla habría bloqueado 3 operaciones ganadoras, se apaga sola.",
        "Ajustes → Reglas aprendidas: buscar, editar, apagar, borrar y «Limpiar con IA» para fusionar duplicadas o contradictorias.",
      ],
    },
    {
      icon: "MousePointerClick", title: "Compra y venta manual desde el gráfico",
      text: "Botones Buy / Sell sobre el gráfico para operar tu propia idea con el mismo control de riesgo.",
      bullets: [
        "Stop a 2×ATR y objetivo a 4×ATR prellenados, con desplegables para cambiarlos y riesgo % editable.",
        "No pasa por la IA ni por las reglas, pero sí respeta los límites de riesgo. El bot no aprende de las operaciones manuales.",
      ],
    },
    {
      icon: "Ghost", title: "Operaciones sombra y rendimiento",
      text: "Todo lo que no se ejecuta se simula, para saber si los filtros te ayudan o te cuestan dinero.",
      bullets: [
        "Sombras por motivo: BAJA_CONFIANZA, NO_EJECUTADA, bloqueos de riesgo y horario.",
        "Rendimiento por par, lado, motivo de salida y día; recorrido máximo a favor y en contra de cada operación.",
        fx ? "Resultado también en pips netos, con el spread incluido." : "«Pares 30d» con poda automática: avisa por Telegram y apaga al día siguiente los pares que siguen perdiendo.",
      ],
    },
    {
      icon: "TrendingUp", title: "Plan de crecimiento, revisión IA y asesor",
      text: "Versiones simplificadas, basadas solo en operaciones reales y sombras.",
      bullets: [
        "Plan de crecimiento: escenarios Prudente / Realista / Ambicioso, fases con criterios claros, seguimiento de aportes y aviso cuando toca avanzar.",
        "Revisión IA automática (diaria o semanal): revisa riesgo, pares, apalancamiento, máximo de posiciones y umbral de confianza. Puede aplicar los cambios o solo avisar.",
        "Botón «Revisar plan y ajustar el bot con IA» y pestaña Evolución para ver cada cambio y deshacerlo.",
        "Chat «IA expert»: pregunta por cualquier dato del bot; los cambios que proponga siempre requieren tu confirmación.",
      ],
    },
    {
      icon: "Send", title: "Telegram, copias de seguridad y VPS",
      text: "Para dejarlo funcionando sin estar delante.",
      bullets: [
        "Alertas por Telegram por tipo y por bot: órdenes, decisiones de la IA, resumen diario y semanal, plan, revisión y sistema.",
        "Copia de seguridad nocturna de la base de datos, con envío semanal por Telegram y restauración desde el panel.",
        "Kit de instalación en tu propio VPS con Docker y HTTPS automático.",
      ],
    },
    {
      icon: "Wallet", title: "Paper trading realista",
      text: "La cuenta de práctica replica los costes y las reglas de la cuenta real.",
      bullets: [
        pep ? "Spread y comisión según tu tipo de cuenta Pepperstone (Razor o Standard), lotes de 0,01 y apalancamiento 1:N."
          : fx ? "Spread medio de HFM en la entrada y la salida, lotes de 0,01, apalancamiento 1:N y conversión a USD de los pares cruzados."
          : "Comisión 0,055 % por lado (0,02 % en maker), slippage, lotes y nocional mínimo de Bybit.",
        `Capital inicial configurable (por defecto $500 ${cur}), reinicio en un clic y botón Depositar / Retirar para simular tus aportes.`,
        "Todo lo que ves en paper (embudo, sombras, plan, evolución) funciona exactamente igual en REAL.",
      ],
    },
  ];
  return { hero, sections };
}
