// "Características" content for the three bots. `bot` = "crypto" | "fx" | "pepper". Every claim here mirrors a real feature of the app.
export function buildFeatures(bot) {
  const fx = bot === "fx" || bot === "pepper";
  const pep = bot === "pepper";
  const name = pep ? "Pepperstone AI trading" : fx ? "HotForex AI Trading" : "Bybit AI Trader";
  const market = fx ? "forex y metales (oro, plata y 10 pares mayores/cruces) con precios de Yahoo Finance" : "futuros perpetuos USDT de Bybit (BTC, ETH, SOL, XRP, AVAX, LINK y hasta 12 pares)";
  const broker = pep ? "tu cuenta MetaTrader de Pepperstone a través de tu propio puente MT (VPS Windows)" : fx ? "tu cuenta HFM (HotForex) a través de tu propio puente MT (VPS Windows)" : "tu cuenta de Bybit (API V5 firmada)";
  const cur = fx ? "USD" : "USDT";
  const hero = {
    title: `${name}: un operador algorítmico que trabaja 24/7 y piensa antes de entrar`,
    text: `Analiza ${market} en tiempo real, detecta configuraciones con ${fx ? "siete" : "ocho"} estrategias clásicas, pide una segunda opinión a la IA con todo el contexto del mercado y, si pasa todos los filtros de riesgo, ejecuta la orden con stop, objetivo y trailing. Empieza en paper trading con dinero ficticio y datos reales; cuando las métricas lo avalen, pásalo a modo REAL sobre ${broker} con un clic y una confirmación.`,
    stats: [
      [fx ? "7" : "8", "estrategias combinables"],
      ["12", "pares disponibles"],
      ["IA", "Claude Sonnet 5.5 o Gemini"],
      ["5", "perfiles de riesgo"],
      ["24/7", fx ? "en horario de mercado" : "sin descanso"],
      [pep ? "Pepperstone" : fx ? "HFM" : "Bybit", "modo REAL integrado"],
    ],
  };
  const sections = [
    {
      icon: "Activity", title: "Motor de análisis en tiempo real",
      text: "Cada pocos segundos el bot vuelve a mirar el mercado y cada minuto (configurable) escanea todos los pares en busca de una entrada.",
      bullets: [
        "Indicadores calculados sobre velas reales: EMA 9/10/20/21/50/200, RSI 14, MACD, ATR, Bandas de Bollinger, ADX, Donchian 20 y Supertrend 10/3.",
        fx ? "Siete estrategias que puedes encender o apagar: seis de corto plazo (Tendencia EMA, Reversión a la media, Ruptura Turtle, Supertrend + momentum, Pullback a la EMA21 y Cruce EMA 10/20, esta última apagada por defecto: entra al cruce al cierre con filtro EMA200 + ADX y sale cuando las EMAs se cruzan al revés) y una de largo plazo (LP): Reversión fin de mes del fix de Londres (último día hábil del mes, opera contra el movimiento 15:00→16:00 Londres y cierra al mediodía siguiente). En modo Multi-estrategia compiten entre sí y gana la señal con mejor puntuación."
           : "Ocho estrategias que puedes encender o apagar: seis de corto plazo (Tendencia EMA, Reversión a la media, Ruptura Turtle, Supertrend + momentum, Pullback a la EMA21 y Cruce EMA 10/20, esta última apagada por defecto: entra al cruce al cierre con filtro EMA200 + ADX y sale cuando las EMAs se cruzan al revés) y dos de largo plazo (LP): Tendencia Multi-Horizonte (diaria: cuatro canales Donchian 20/55/100/200 votan y la SMA200 diaria confirma) y Divergencia doble RSI+MACD en 4h. En modo Multi-estrategia compiten entre sí y gana la señal con mejor puntuación.",
        "Las operaciones de largo plazo tienen su propio tope («Máx. posiciones largo plazo») y no consumen el límite de posiciones de corto plazo; se señalan con la etiqueta LP.",
        "Tres niveles de criterios de entrada (Estrictos / Relajados / Mínimos) que endurecen o relajan RSI, ADX, volumen y confirmaciones MACD/EMA200.",
        "Filtro de temporalidad superior: solo se opera a favor de la tendencia del marco mayor (4h si operas en 1h, 1h si operas en 15m).",
        fx ? "Horario forex real: respeta el cierre del mercado, puede cerrar posiciones antes del hueco del fin de semana y filtra entradas por sesión (Sídney, Tokio, Londres, Nueva York)."
           : "Datos de Bybit con respaldo automático de Bitget si Bybit no responde desde la región del servidor: nunca te quedas sin precios.",
        "Monitor cada 5 s: marca a mercado, PnL no realizado, stop/objetivo/trailing y cierres detectados en el bróker.",
      ],
    },
    {
      icon: "Brain", title: "Una IA que decide con contexto, no a ciegas",
      text: "Antes de abrir, la IA recibe la señal, los indicadores, la tendencia superior, el estado de la cuenta, las noticias y los riesgos del momento, y devuelve una decisión razonada con un porcentaje de confianza.",
      bullets: [
        "Usa Claude Sonnet 5.5 con la llave universal de Emergent (sin configurar nada) o tu propia API key de Google Gemini: la fuente y el modelo se cambian en Ajustes → IA y se prueban antes de guardar.",
        "Confianza mínima configurable: por debajo, la señal se descarta como BAJA_CONFIANZA pero se simula en la sombra para medir si la IA acertó.",
        "El razonamiento de cada decisión queda visible en la tarjeta Evaluación IA y en el feed de señales, con etiquetas APROBADA / RECHAZADA / BLOQUEADA / NOTICIAS" + (fx ? " / SESION / NO_EJECUTADA." : " / FUNDING / NO_EJECUTADA."),
        "Ahorro de tokens: la IA no se consulta si ya estás al tope de posiciones, el par está abierto, el límite diario saltó o el bot está detenido; el agente de noticias tampoco repite análisis si los titulares no cambiaron. Un contador diario muestra llamadas hechas y ahorradas.",
        "Monitor de salud: si la API key falla o el saldo se agota aparece un banner rojo y llega un aviso por Telegram; al recuperarse, otro aviso.",
      ],
    },
    {
      icon: "ShieldCheck", title: "Gestión de riesgo de nivel profesional",
      text: "El tamaño de cada orden se calcula con la equidad real del momento (incluidos tus depósitos) y nunca arriesga más del porcentaje que fijes.",
      bullets: [
        `Riesgo fijo por operación (por defecto 1 % de la equidad), stop por ATR (1,5×) y objetivo por ATR (3×) con R:R mínimo 1:2; ${fx ? "lotes cuantizados al paso del bróker (0,01) y la opción de usar el lote mínimo cuando el riesgo real sigue dentro del tope que indiques" : "cantidades ajustadas al lote y al nocional mínimo de Bybit"}.`,
        "Protección dinámica configurable: breakeven a +N R, trailing por ATR desde +N R con distancia ajustable y cierre parcial (p. ej. 50 % a +1,5R) dejando correr el resto. Cada operación registra su recorrido (máximo a favor/en contra en R y % del TP alcanzado); en Rendimiento ves cuántas «rozaron el TP sin tocarlo» y la IA ajusta breakeven, trailing, cierre parcial y TP con esa evidencia (mínimo 20 operaciones).",
        "Límite de pérdida diaria: al tocarlo el bot deja de abrir hasta el siguiente día UTC. Tope de posiciones simultáneas, un par por posición y cierre por tiempo máximo (72 h).",
        `Cinco perfiles de riesgo con un deslizador (Conservador → Máximo) que ajustan pares, temporalidad, criterios, filtro de IA, riesgo, posiciones, ${fx ? "apalancamiento 1:100–1:500" : "apalancamiento 3x–10x"} y límite diario de golpe, con confirmación en los niveles agresivos.`,
        "Los depósitos y retiros se detectan y se excluyen del PnL diario, del límite diario, del retorno total y del drawdown: un aporte nunca parece ganancia ni un retiro una pérdida.",
      ],
    },
    {
      icon: "Newspaper", title: fx ? "Puertas de riesgo: noticias, sesiones y fin de semana" : "Puertas de riesgo: noticias y funding",
      text: "Antes de molestar a la IA, cada señal pasa por filtros objetivos que bloquean o reducen el tamaño en los momentos peligrosos.",
      bullets: [
        `Agente de noticias: lee RSS ${fx ? "de ForexLive, Investing.com, FXEmpire y MarketWatch (más Google News por par) y el calendario económico de ForexFactory" : "de CoinDesk, CoinTelegraph, The Block y Decrypt más el calendario macro de ForexFactory"}, los resume con la IA en un nivel de riesgo (BAJO/MEDIO/ALTO) y detecta alertas CRÍTICAS por par o para todo el mercado.`,
        "Bloqueo automático alrededor de eventos de alto impacto (45 min antes, 30 después) y ante alertas críticas; con riesgo ALTO las órdenes se abren a la mitad de tamaño.",
        fx ? "Trading por noticias: cuando sale un dato macro relevante, la IA puede proponer una operación específica (opcionalmente confirmando el movimiento del precio antes de entrar)."
           : "Filtro de funding: si la tasa de financiación va en tu contra y es ALTA reduce el tamaño; si es EXTREMA bloquea la entrada. Normaliza pares que liquidan cada 4h/1h a su equivalente de 8h.",
        fx ? "Sesiones permitidas: elige en qué sesiones puede abrir el bot; fuera de ellas la señal queda como SESION y se simula en la sombra para ver qué habrías ganado o perdido."
           : null,
        fx ? "Filtros FX con evidencia (pestaña Filtros): tope de exposición neta por divisa (no apostar tres veces al mismo dólar), guardia de rollover 16:50–17:15 Nueva York (spreads ×4, sin entradas y stops no evaluados en paper), régimen de volatilidad por percentil de ATR (sin entradas en mercado muerto, mitad de riesgo en extremos), estacionalidad intradía (Breedon & Ranaldo: una divisa se deprecia en su horario local), régimen USD con índice sintético vs SMA200 y regla FOMC sin continuación 24 h. Cada bloqueo queda en sombra (EXPOSICION, ROLLOVER, VOLATILIDAD, ESTACIONAL, REGIMEN_USD, FOMC) para medir su valor." : null,
        fx ? null : "Entradas con orden límite maker (comisión 0,02 % en vez de 0,055 %): la señal aprobada espera hasta N velas a su precio límite y, si no se llena, entra a mercado. En Rendimiento ves la tasa de llenado y la comisión ahorrada.",
      ],
    },
    {
      icon: "Ghost", title: "Shadow trading: mide lo que el bot NO hizo",
      text: "Toda señal descartada (por la IA, por riesgo, por noticias" + (fx ? ", por sesión" : ", por funding") + " o porque la orden no pudo abrirse) se simula con el mismo tamaño, stop, objetivo, trailing y costes que una operación real.",
      bullets: [
        "El resultado de esas operaciones sombra se convierte en el «valor del filtro»: positivo si bloquear evitó pérdidas, negativo si bloqueó ganancias.",
        "Desglose por motivo, por confianza de la IA, por estrategia, por par" + (fx ? " y por sesión del mercado, con pips netos de spread." : "."),
        "La IA recibe estas estadísticas y los últimos motivos de rechazo en cada revisión: aprende si está siendo demasiado estricta o demasiado laxa.",
      ],
    },
    {
      icon: "FlaskConical", title: "Backtest, optimizador y walk-forward",
      text: "Prueba antes de arriesgar: mil velas reales por par, con los mismos costes que el paper, y una validación fuera de muestra que descubre el sobreajuste.",
      bullets: [
        `Backtest con comisiones y slippage ${fx ? "(spread medio de HFM a la entrada y a la salida, lote mínimo, cierre de fin de semana y sesiones)" : "(0,055 % + 0,02 %)"} que devuelve retorno, drawdown, profit factor, expectativa en R y desglose por estrategia${fx ? ", sesión y pips" : ""}.`,
        "Optimizador de SL/TP por par: 54 combinaciones (stop 1–3×ATR, objetivo 2–6×ATR, ATR 10/14/20) puntuadas con SQN robusto (promedio con las combinaciones vecinas) y mapa de calor. Aplica o quita parámetros con un clic.",
        "Walk-forward sobre 2.000 velas: optimiza en un tramo y opera a ciegas en el siguiente, encadenando balances. Veredictos claros: ROBUSTO, DUDOSO, SOBREAJUSTADO, NO_RENTABLE o INSUFICIENTE, con eficiencia WFE y curvas comparadas.",
        "Revalidación semanal automática (lunes 07:00 UTC) de los parámetros aplicados; si un par sale sobreajustado se quitan solos (opcional) y te avisa por Telegram.",
        "Semáforo de decisión por par: Aplicar / Con cautela / No aplicar / Mantener / Vigilar / Quitar.",
      ],
    },
    {
      icon: "Sparkles", title: "Revisión IA automática y «Optimizar todo con IA»",
      text: "La IA no solo filtra señales: revisa el bot entero como lo haría un gestor y ajusta la configuración dentro de topes de seguridad.",
      bullets: [
        "Revisión programada (diaria o semanal, 08:00 UTC) con el rendimiento real, el chequeo del plan, los backtests, las sombras, las noticias y la última revalidación walk-forward.",
        `Topes inviolables según tu perfil (riesgo ≤ 1,5 %${fx ? ", apalancamiento ≤ 1:100" : ", apalancamiento ≤ 5x"}, posiciones ≤ 5, mínimo 2 pares); cambios estructurales solo con suficientes operaciones reales (20–30).`,
        "Puede aplicar los cambios o solo avisarte; cada cambio queda explicado en lenguaje natural y registrado en Evolución con botón Deshacer.",
        "«Optimizar todo con IA» lanza optimizador + backtest + walk-forward de todos los pares y deja que la IA decida qué parámetros merecen aplicarse.",
      ],
    },
    {
      icon: "MessageSquare", title: "Chatea con IA expert: un gestor que conoce tu bot por dentro",
      text: "El botón negro y dorado de la cabecera abre un panel lateral con un experto en trading y gestión de riesgo que ve en vivo los tres bots y responde con contexto real, no con generalidades.",
      bullets: [
        "Contexto completo: ajustes, estado del mercado, posiciones abiertas, historial y estadísticas, señales y análisis de la IA, logs recientes, plan de crecimiento y evolución de Bybit, HotForex y Pepperstone; además puede leer el código del proyecto (solo lectura) para explicarte exactamente cómo funciona algo.",
        "Propuestas en tarjetas: cuando sugiere cambiar un ajuste o ejecutar una acción (iniciar/detener, escanear, cerrar una posición, aplicar parámetros…) aparece una tarjeta con Aplicar / Descartar. Nada se ejecuta sin tu clic; en modo REAL la tarjeta avisa además del riesgo.",
        "Límites: nunca modifica código, claves ni contraseñas, no cambia el modo paper/real ni reinicia cuentas. Usa el proveedor y modelo de IA configurados en Ajustes → IA (llave universal o tu Gemini).",
        "Memoria: una sola conversación compartida con resumen automático a partir de 40 mensajes (conserva los 10 últimos) y memoria a largo plazo de tus preferencias; «Nueva conversación» la borra con confirmación.",
      ],
    },
    ...(pep ? [{
      icon: "FlaskConical", title: "Modo laboratorio: una estrategia sola para recoger data limpia",
      text: "Exclusivo de Pepperstone: un clic deja activa solo la estrategia Cruce EMA 10/20 con las capas que distorsionan la medición apagadas, y otro clic restaura tus ajustes.",
      bullets: [
        "Preset: solo Cruce EMA 10/20 en 15 m, criterios relajados (EMA200 + ADX>15), los 12 pares y el filtro IA activo. Apaga filtro de temporalidad superior, breakeven/trailing/cierre parcial (las salidas son SL 2×ATR, TP 6×ATR o cruce contrario), régimen USD, volatilidad, estacionalidad, FOMC, rollover, bloqueos y trading por noticias y la revisión IA automática.",
        "Mantiene las protecciones de capital: riesgo por operación, límite de pérdida diaria, máximo de posiciones, lote mínimo, exposición por divisa y cierre antes del fin de semana. Solo en paper: no se puede pasar a REAL con el laboratorio activo.",
        "La tarjeta muestra en vivo operaciones, acierto, PnL, pips, profit factor, cierres por cruce contrario y motivos de cierre desde que lo activaste; Rendimiento → Por estrategia y el Historial completan la data.",
        "Si tocas un ajuste a mano te avisa de que la data deja de ser pura. Al desactivarlo restaura ajustes, noticias y revisión automática; aplicar un perfil de riesgo también lo termina (el perfil manda y el resto vuelve a su valor anterior).",
      ],
    }] : []),
    {
      icon: "TrendingUp", title: "Plan de crecimiento con chequeo real",
      text: "Convierte una meta de ingreso mensual en un camino por fases y comprueba cada mes si vas por delante o por detrás.",
      bullets: [
        `Calculadora: capital inicial, meta mensual y aporte mensual → meses hasta la meta según tres escenarios (Prudente ${fx ? "1,5" : "2,5"} %, Realista ${fx ? "3" : "4"} %, Ambicioso ${fx ? "5" : "6"} % mensual) y uno «Según backtest» con Monte Carlo de 3.000 simulaciones.`,
        "Fases Semilla → Crecimiento → Consolidación → Ingreso mensual, con regla de reinversión, riesgo y criterio para avanzar; en la fase final se retira el 80 % de la ganancia y el 20 % queda de colchón.",
        "Chequeo con tus resultados reales (operaciones, profit factor, drawdown, días operando) y botón «Avanzar» cuando cumples los criterios.",
        "Aportes del plan: tus depósitos reales cuentan como aportes mensuales cumplidos, mes a mes, y se compara tu capital con el que el plan preveía.",
      ],
    },
    {
      icon: "GitBranch", title: "Evolución: cada cambio, con su antes y su después",
      text: "Una línea de tiempo registra todo cambio de configuración (hecho por ti, por la IA, por el optimizador, por la revalidación o por el perfil) y mide el rendimiento en la ventana siguiente.",
      bullets: [
        "Veredicto por cambio: Mejoró / Empeoró / Igual / Pendiente, con operaciones, PnL, acierto y profit factor antes y después.",
        "Deshacer con un clic: restaura los valores anteriores solo en los ajustes que siguen vigentes y lo registra como un cambio más.",
        "La IA ve su propio historial y, en la revisión automática, deshace por sí sola los cambios que empeoraron el resultado.",
        "El resumen semanal de Telegram incluye qué cambios mejoraron, cuáles empeoraron y cuáles deshizo la IA.",
      ],
    },
    {
      icon: "Landmark", title: pep ? "Modo REAL con Pepperstone vía puente MT propio" : fx ? "Modo REAL con HFM vía puente MT propio" : "Modo REAL con Bybit",
      text: pep ? "Conecta tu cuenta MetaTrader 5 de Pepperstone (demo o real) mediante tu puente MT en el VPS Windows (cuenta pepperstone_v1): el bot la verifica y opera igual que en paper. Importante: el puente solo conecta terminales MetaTrader; si abres la cuenta en cTrader el bot no podrá operarla en real (sí puedes seguirla en paper)."
          : fx ? "Conecta tu cuenta MetaTrader 4 de HFM (demo o real) mediante tu puente MT en el VPS Windows (cuenta hfm_v1): el bot la verifica y opera igual que en paper."
                : "Introduce una API key de Bybit (mainnet, testnet o demo) y el bot opera con órdenes a mercado con stop y objetivo adjuntos.",
      bullets: [
        pep ? "Tipo de cuenta Pepperstone seleccionable en Ajustes: Razor (spread crudo + comisión de 7 USD por lote ida y vuelta, por defecto) o Standard (solo spread, sin comisión). El bot usa ese perfil de costes en paper, backtests, optimizador, plan Monte Carlo y para el PnL esperado; los símbolos van sin sufijo (EURUSD, XAUUSD) y el servidor es del tipo Pepperstone-Demo / Pepperstone-MT5-Live01." : null,
        "Lista de comprobación antes de cambiar de modo: credenciales verificadas, bot detenido, sin posiciones abiertas; confirmación escribiendo REAL y reinicio de estadísticas con la equidad real.",
        "Sincronización continua: marca, PnL, trailing enviado al bróker, detección de cierres hechos fuera del bot y de posiciones externas.",
        fx ? "Alerta de desconexión: si el puente MT o el terminal pierden la conexión con HFM más de 180 s (configurable), banner rojo + Telegram, y el bot no abre nada hasta que vuelve."
           : "Alerta de desconexión: si Bybit deja de responder más de 180 s (configurable), banner rojo + Telegram, y el bot no abre nada hasta que vuelve; cierre total de posiciones con confirmación.",
        "Depósitos y retiros detectados automáticamente en la cuenta real (cada minuto o al instante si la equidad salta) y excluidos de las estadísticas.",
      ],
    },
    {
      icon: "Send", title: "Telegram: el bot te cuenta todo",
      text: "Avisos que activas por separado (compartidos por los tres bots, cada uno con su prefijo), con prueba de envío desde Ajustes.",
      bullets: [
        "Entradas, salidas y límite diario · Decisiones de la IA sobre cada señal · Alertas de noticias graves · Avisos del plan (y depósitos).",
        "Resumen diario (00:00 UTC) y resumen semanal (domingos 20:00 UTC) con PnL, mejores y peores pares, valor del filtro IA, evolución y estado del plan.",
        "Revisión IA automática, revalidación walk-forward, conexión con el bróker caída/restablecida, salud de la IA y copias de seguridad.",
      ],
    },
    {
      icon: "DatabaseBackup", title: "Seguridad, datos y copias de seguridad",
      text: "Pensado para vivir en tu propio servidor.",
      bullets: [
        "Acceso con clave única y sesión de 7 días; bloqueo tras 5 intentos fallidos por IP; cambiar la clave cierra las demás sesiones.",
        "API keys y contraseñas de bróker cifradas en la base de datos; nunca se muestran completas.",
        "Copia de seguridad completa cada noche (03:00 UTC) con 14 días de retención y envío por Telegram los domingos; crear, descargar y restaurar desde Ajustes (también desde un archivo de tu disco).",
        "Kit de despliegue para VPS con Docker (instalación en un comando, HTTPS automático, crons y scripts de backup/actualización); el zip se genera al momento desde el código actual, con los tres bots y el chat IA expert.",
      ],
    },
    {
      icon: "Wallet", title: "Paper trading realista",
      text: "La cuenta de práctica no es un juguete: replica los costes y las reglas de la cuenta real.",
      bullets: [
        pep ? "Spread y comisión según tu tipo de cuenta Pepperstone (Razor: spread crudo + 7 USD por lote ida y vuelta · Standard: solo spread) aplicados a la entrada y la salida, lotes de 0,01, apalancamiento 1:N y conversión a USD de los pares cruzados."
           : fx ? "Spread medio de HFM aplicado a la entrada y la salida, lotes de 0,01, apalancamiento 1:N y conversión a USD de los pares cruzados."
           : "Comisión 0,055 % por lado, slippage 0,02 %, lotes y nocional mínimo de Bybit, apalancamiento aplicado al margen.",
        `Capital inicial configurable (por defecto $500 ${cur}), reinicio en un clic y botón Depositar / Retirar para simular los aportes mensuales de tu plan.`,
        "Todo lo que ves en paper (señales, IA, sombras, plan, evolución) funciona exactamente igual en REAL.",
      ],
    },
  ];
  return { hero, sections };
}
