// "Cómo usar" step-by-step guides for the three bots. `bot` = "crypto" | "fx" | "pepper".
export function buildHowTo(bot) {
  const fx = bot === "fx" || bot === "pepper";
  const pep = bot === "pepper";
  const cur = fx ? "USD" : "USDT";
  const brokerName = pep ? "Pepperstone (puente MT)" : fx ? "HFM (puente MT)" : "Bybit";
  return [
    {
      id: "inicio", icon: "Compass", title: "1 · Primeros pasos y recorrido por el panel",
      steps: [
        "Entra con la clave del panel (una sola clave compartida, sin usuario). La sesión dura 7 días; con «Cerrar sesión» (icono de salida en la cabecera) la cierras antes.",
        "En la pantalla inicial eliges el bot: Bybit AI Trader (cripto), HotForex AI Trading (forex) o Pepperstone AI trading (forex, misma estrategia con los costes de Pepperstone). Cada tarjeta muestra en vivo si el bot está activo, su modo (PAPER/REAL), la equidad y el retorno. «Cambiar bot» en la cabecera te devuelve aquí.",
        "Cabecera: badge de modo (Paper trading / Modo REAL), origen de los datos, cuenta atrás del próximo análisis, estado del bot, botones Escanear, Reiniciar (cuenta paper), Iniciar/Detener, Ajustes, Documentación, Instalar en tu VPS, Chatea con IA expert (negro y dorado) y Cambiar bot.",
        "Fila de KPIs: Balance, Equidad (con retorno total y aportes), PnL no realizado, PnL diario, tasa de acierto, operaciones y drawdown máximo. Pasa el ratón por encima para ver cómo se calcula cada uno.",
        "Columna izquierda: Mercado en vivo (precio, cambio 24 h, RSI, sesgo" + (fx ? " y sesiones abiertas" : " y chip de funding") + "). Centro: gráfico de velas con EMAs, RSI, volumen y marcadores de operaciones (haz clic en un par para cambiarlo). Arrastra hacia la izquierda para cargar velas antiguas sin límite (como en MetaTrader), elige 1m–1D, apaga/enciende cada indicador con sus chips, amplía a pantalla completa (Esc para salir) y mira el eje de tiempo en tu hora local. Derecha: Plan de trading (perfil, estrategias activas, riesgo, curva de equidad) y Evaluación IA.",
        "Pestañas inferiores: Posiciones, Historial, Backtest, Rendimiento, Optimizador, Noticias" + (fx ? " y agenda" : "") + ", Plan de crecimiento y Evolución. Debajo, el Terminal con el registro completo del bot, coloreado por nivel (ORDER, AI, RISK, NEWS, PLAN, ERROR…) y con botón para limpiarlo.",
      ],
    },
    {
      id: "ciclo", icon: "Play", title: "2 · Iniciar el bot y entender su ciclo",
      steps: [
        "Pulsa «Iniciar bot». Desde ese momento escanea todos los pares cada N segundos (Intervalo de escaneo) y vigila las posiciones cada 5 s. Si reinicias el servidor, el bot recuerda que estaba activo y continúa solo.",
        "«Escanear» fuerza un análisis inmediato sin esperar al temporizador. Útil tras cambiar ajustes.",
        "Ciclo de una señal: estrategia detecta configuración → filtro de temporalidad superior → puertas de riesgo (tope de posiciones, par ya abierto, límite diario, noticias" + (fx ? ", sesión, mercado abierto, conexión con HFM" : ", funding") + ") → IA (confianza mínima) → cálculo de tamaño → orden con SL/TP → trailing.",
        "En «Evaluación IA» verás la última decisión con su razonamiento; en el feed de señales, cada señal con su etiqueta (APROBADA, RECHAZADA, BAJA_CONFIANZA, BLOQUEADA, NOTICIAS" + (fx ? ", SESION, MERCADO, NO_EJECUTADA" : ", FUNDING, NO_EJECUTADA") + ") y la nota del motivo.",
        "Las señales no operadas se simulan en la sombra automáticamente; su resultado aparece en Rendimiento → Operaciones sombra.",
        "«Detener bot» no cierra posiciones: siguen protegidas por su stop y objetivo y el monitor las sigue gestionando. Para salir de todo usa «Cerrar todas» en Posiciones.",
      ],
    },
    {
      id: "estrategia", icon: "SlidersHorizontal", title: "3 · Configurar estrategia, riesgo y perfil",
      steps: [
        "Pestaña «Estrategia y riesgo» (dentro del panel): pares a vigilar, temporalidad (" + (fx ? "5m/15m/1h/4h" : "1m/5m/15m/1h/4h") + "), estrategias encendidas (chips), criterios de entrada (Estrictos/Relajados/Mínimos), filtro de temporalidad superior, filtro de IA y su confianza mínima.",
        "La estrategia «Cruce EMA 10/20» viene apagada en los tres bots y en todos los perfiles: enciéndela con su chip (o en Rendimiento → Por estrategia) cuando quieras probarla. Entra al cruce de la EMA10 sobre/bajo la EMA20 al cierre de vela con el precio a favor de la EMA200 y ADX>20 (relajados: >15; mínimos: EMA50 sin ADX), stop 2×ATR, objetivo lejano 6×ATR y cierra sola cuando las EMAs se cruzan al revés (motivo CRUCE). Si el trailing está activo puede cerrarla antes.",
        `Riesgo: % por operación, posiciones máximas, apalancamiento (${fx ? "1:N" : "Nx"}), límite de pérdida diaria (%) e intervalo de escaneo. Si escribes un valor fuera de rango el panel lo recorta y te avisa.`,
        fx ? "Solo forex: «Riesgo máx. lote mínimo (%)» permite abrir con 0,01 lotes aunque el riesgo real supere tu % si no pasa de ese tope (imprescindible con poco capital en oro/plata); «Cerrar antes del fin de semana»; y «Sesiones para abrir posiciones» (vacío = todas)."
           : "El filtro de funding (Ajustes → Filtro de funding) y el agente de noticias complementan estos ajustes sin tocar la estrategia.",
        "Deslizador «Perfil de riesgo» (0 Conservador → 4 Máximo): aplica un preset completo de pares, temporalidad, criterios, IA, riesgo, posiciones, apalancamiento y límite diario. Si cambias algo a mano el perfil pasa a «Personalizado». Los niveles 3-4 piden confirmación.",
        "Cada cambio guardado queda registrado en Evolución con su antes/después para que puedas medirlo y deshacerlo.",
      ],
    },
    {
      id: "posiciones", icon: "ListOrdered", title: "4 · Posiciones, cierres e historial",
      steps: [
        "Posiciones: par, lado, tamaño, entrada, precio actual, SL/TP vigentes (el stop se mueve solo con breakeven y trailing), PnL y botón «Cerrar» por posición. «Cerrar todas» pide confirmación.",
        "Motivos de cierre: TP (objetivo), SL (stop), TRAILING (stop dinámico), MANUAL, TIEMPO (72 h), CRUCE (la estrategia Cruce EMA 10/20 cierra cuando las EMAs se cruzan al revés)" + (fx ? " y FIN_SEMANA (cierre antes del hueco del fin de semana)." : "."),
        "Historial: todas las operaciones cerradas con PnL neto de comisiones, estrategia, motivo, duración y confianza de la IA. Rendimiento las agrega por estrategia, par, lado y motivo.",
        "En modo REAL, si cierras una posición desde la app del bróker el bot lo detecta en segundos, registra el resultado real y lo refleja aquí.",
      ],
    },
    {
      id: "backtest", icon: "FlaskConical", title: "5 · Backtest",
      steps: [
        "Elige par, temporalidad y capital; el backtest usa 1.000 velas reales, tus estrategias y criterios actuales, los parámetros SL/TP aplicados y los mismos costes que el paper.",
        "Lee: retorno, drawdown máximo, acierto, profit factor, expectativa en R, operaciones por estrategia" + (fx ? ", pips netos, cierres de fin de semana, señales descartadas por sesión y por lote mínimo." : " y señales rechazadas por lote mínimo."),
        "Un backtest positivo no garantiza nada: compáralo con el walk-forward del Optimizador antes de aplicar parámetros.",
      ],
    },
    {
      id: "optimizador", icon: "Target", title: "6 · Optimizador, walk-forward y revalidación semanal",
      steps: [
        "«Optimizar» un par (o «Optimizar todos») prueba 54 combinaciones de SL/TP/ATR y propone la más robusta (SQN promediado con sus vecinas). El mapa de calor muestra el paisaje completo; «Aplicar» guarda los parámetros solo para ese par; «Quitar» vuelve a los valores por defecto.",
        "«Validar walk-forward» (o «Validar todos») optimiza por tramos y opera a ciegas fuera de muestra. Veredictos: ROBUSTO (aplicar), DUDOSO (con cautela), SOBREAJUSTADO / NO_RENTABLE (no aplicar o quitar), INSUFICIENTE (pocas operaciones). Si el par ya tiene parámetros aplicados, también valida ESOS parámetros.",
        "La columna «Decisión» del resumen multi-par resume todo en un semáforo: Aplicar · Con cautela · No aplicar · Mantener · Vigilar · Quitar parámetros.",
        "Tarjeta «Revalidación semanal automática»: cada lunes 07:00 UTC revalida los pares con parámetros aplicados; con «Quitar sobreajustados» activo los elimina solos y te avisa. «Revalidar ahora» lo lanza al momento.",
        "«Optimizar todo con IA» encadena optimizador + backtest + walk-forward de todos los pares y deja que la IA aplique solo lo que supera la validación, explicando cada decisión (tarda 1–3 minutos; puedes seguir el progreso).",
      ],
    },
    {
      id: "rendimiento", icon: "BarChart3", title: "7 · Rendimiento y operaciones sombra",
      steps: [
        "Tablas por estrategia, par, lado y motivo de cierre con operaciones, acierto, PnL y profit factor. Desde aquí puedes apagar una estrategia o un par con un botón.",
        "Decisiones de la IA: cuántas señales aprobó, rechazó o bloqueó cada filtro.",
        "«Operaciones sombra · ¿acierta el filtro?»: PnL simulado de lo que no se operó. Valor del filtro positivo = los bloqueos te ahorraron dinero; negativo = te costaron ganancias. Desglose por motivo, confianza de la IA, estrategia, par" + (fx ? " y sesión." : "."),
        "Si el valor de la IA es muy negativo durante semanas, baja la confianza mínima o cambia a criterios más relajados; si es muy positivo, la IA está haciendo bien su trabajo.",
      ],
    },
    {
      id: "noticias", icon: "Newspaper", title: "8 · Noticias" + (fx ? " y agenda económica" : ""),
      steps: [
        "La pestaña muestra el sentimiento y riesgo del mercado, el riesgo por par, los titulares analizados" + (fx ? ", la agenda de eventos de alto/medio impacto con hora local" : ", los eventos macro de la semana") + " y las alertas activas. El punto rojo en la pestaña indica riesgo ALTO, bloqueo o alerta crítica.",
        "«Actualizar ahora» fuerza un nuevo análisis con la IA (los ciclos automáticos solo la llaman si cambiaron los titulares; con el bot detenido el ciclo se relaja a 60 min).",
        "En Ajustes → Agente de noticias configuras el intervalo, las ventanas de bloqueo alrededor de eventos, el factor de tamaño con riesgo ALTO" + (fx ? ", y el trading por noticias (confianza mínima, confirmación de precio, ventana y duración máxima)." : " y si se activa el filtro."),
        "Cada bloqueo por noticias queda en el feed como NOTICIAS con el motivo y se simula en la sombra.",
      ],
    },
    {
      id: "plan", icon: "TrendingUp", title: "9 · Plan de crecimiento",
      steps: [
        "Calculadora: capital inicial (se toma de tu cuenta), meta de ingreso mensual, aporte mensual y escenario de rentabilidad (Prudente / Realista / Ambicioso / Según backtest). Pulsa «Calcular plan»: obtienes meses hasta la meta, capital objetivo, tabla de fases y proyección mes a mes.",
        "La tabla de fases explica para cada etapa la regla de reinversión, el riesgo por operación y el criterio para avanzar. Fase 0 (Validación en paper) exige ≥ 60 operaciones, PF ≥ 1,3, drawdown ≤ 10 % y ≥ 28 días antes de pasar a dinero real.",
        "«Chequeo del plan · resultados reales» compara tus métricas reales con los criterios de la fase actual; cuando se cumplen aparece «Avanzar a …», que fija la fase y el riesgo recomendado.",
        "«Aportes del plan»: tus depósitos (paper o detectados en el bróker) se agrupan por mes y se comparan con el aporte comprometido; también verás tu capital frente al que el plan preveía a estas alturas.",
        "«Revisar plan y ajustar el bot con IA» pide a la IA una lectura del plan con tus datos reales y propone/aplica cambios dentro de los topes (con botón Deshacer).",
      ],
    },
    {
      id: "evolucion", icon: "GitBranch", title: "10 · Evolución y deshacer cambios",
      steps: [
        "Cinco contadores (cambios, aplicados por la IA, mejoraron, empeoraron, pendientes) y una línea de tiempo con icono por origen: IA (revisión automática, optimizar todo, plan, optimizador), revalidación semanal, perfil, manual o sistema.",
        "Cada evento muestra los ajustes modificados (antes → después), el resumen o razonamiento, y el rendimiento en la ventana posterior frente a la anterior (operaciones, PnL, acierto, PF, equidad) con veredicto Mejoró / Empeoró / Igual / Pendiente (hacen falta ≥ 5 operaciones).",
        "«Deshacer» restaura los valores anteriores de los ajustes que siguen vigentes (si cambiaste algo después, se conserva y se indica). El deshacer se registra como un nuevo evento.",
        "La IA revisa esta misma línea de tiempo en cada revisión automática y deshace sola los cambios con veredicto Empeoró.",
      ],
    },
    {
      id: "ajustes", icon: "Settings2", title: "11 · Ajustes (botón «Ajustes» de la cabecera)",
      steps: [
        "Seguridad: cambia la clave del panel (cierra las demás sesiones).",
        "Inteligencia artificial: fuente (llave universal de Emergent o tu API key de Google Gemini), proveedor y modelo (lista legible o escribir otro), «Probar conexión» antes de guardar, estado de salud en vivo. La configuración de IA es común a los tres bots.",
        "Telegram: crea un bot con @BotFather y pega el token; escribe un mensaje a tu bot y pulsa «Detectar chat ID»; activa los avisos que quieras y envía una prueba. Tarjeta «Resumen semanal»: vista previa y «Enviar ahora».",
        "Revisión IA automática: activar, frecuencia (diaria/semanal + día), aplicar cambios o solo avisar, «Ejecutar ahora» y última revisión con resumen y cambios.",
        "Agente de noticias" + (fx ? "" : " y Filtro de funding") + ": intervalos, ventanas de bloqueo, factores de tamaño" + (fx ? " y trading por noticias." : " y umbrales ALTO/EXTREMO."),
        pep ? "Pepperstone · cuenta real: URL del puente MT, token, ID de cuenta (pepperstone_v1), sufijo de símbolos (en Pepperstone vacío) y «Tipo de cuenta Pepperstone · costes» (Razor: spread crudo + 7 USD/lote · Standard: solo spread) que el bot usa en paper, backtests y plan; Probar conexión, Comprobar símbolos, Reconectar y Quitar; aviso de desconexión; «Cambiar de cuenta · re-basar capital»."
           : fx ? "HotForex · cuenta real: URL del puente MT, token, ID de cuenta (hfm_v1) y tipo de cuenta HFM · sufijo de símbolos (autodetectado al probar la conexión); Probar conexión, Comprobar símbolos, Reconectar y Quitar; aviso de desconexión (segundos); «Cambiar de cuenta · re-basar capital» para pasar de la demo a dinero real sin perder historial."
           : "Bybit · cuenta real: entorno (mainnet/testnet/demo), API key y secret, endpoint avanzado y aviso de desconexión (segundos), Probar conexión (equidad, disponible, posiciones, desfase horario) y Quitar.",
        "Modo de operación: lista de comprobación y botón «Pasar a modo REAL» / «Volver a paper».",
        `Cuenta paper: capital inicial, aportes netos, equidad y operaciones; «Depositar / Retirar» para simular aportes; «Reiniciar con este capital» (borra operaciones, señales, sombras y movimientos; también disponible desde la cabecera con $500 ${cur}).`,
        "Copias de seguridad (comunes a los tres bots): última copia, crear ahora, descargar, lista de copias del servidor, restaurar una copia guardada o un archivo .json.gz de tu disco (requiere todos los bots detenidos y escribir RESTAURAR; tu clave actual se conserva).",
      ],
    },
    {
      id: "real", icon: "Landmark", title: `12 · Pasar a modo REAL con ${brokerName}`,
      steps: pep ? [
        "Abre tu cuenta en Pepperstone eligiendo la plataforma MetaTrader 5 (o MT4), no cTrader: el puente que usa el bot solo conecta terminales MetaTrader. Elige el tipo de cuenta (Razor con comisión o Standard solo spread) y selecciónalo igual en Ajustes → Pepperstone para que los costes del bot coincidan.",
        "Instala el puente MT (carpeta windows_bridge) en tu VPS Windows, abre el terminal MetaTrader de la cuenta con Algo Trading activo y arranca mt_bridge.py. En Pepperstone necesitas el login, la contraseña MASTER (no la de inversor) y el nombre exacto del servidor (p. ej. Pepperstone-Demo o Pepperstone-MT5-Live01; el campo te sugiere los habituales).",
        "Ajustes → Pepperstone · cuenta real: pega token, login, contraseña, servidor, plataforma y región; «Guardar» y luego «Probar conexión» (el bot lee la cuenta del terminal a través del puente; tarda 1–3 min la primera vez).",
        "«Probar conexión» debe mostrar equidad, moneda, apalancamiento y «trading permitido». En Pepperstone los símbolos no llevan sufijo; «Comprobar símbolos» verifica que los 12 pares cotizan y son operables en tu cuenta.",
        "Modo de operación: cumple la lista (cuenta vinculada y verificada, trading permitido, bot detenido, sin posiciones, modo laboratorio desactivado; se recomienda haber superado la Fase 0 del plan), escribe REAL y confirma. Las estadísticas se reinician con la equidad real.",
        "Inicia el bot. A partir de ahora las órdenes, el PnL y la equidad vienen de Pepperstone; las señales se siguen calculando con velas de Yahoo. Si la conexión cae más del tiempo configurado te avisará y no abrirá nada hasta recuperarse.",
        "Para volver a paper: detén el bot, cierra posiciones y pulsa «Volver a paper» (la cuenta paper vuelve a $500).",
      ] : fx ? [
        "Instala el puente MT (carpeta windows_bridge) en tu VPS Windows, abre el terminal MetaTrader de la cuenta con Algo Trading activo y arranca mt_bridge.py. En HFM necesitas el login, la contraseña MASTER (no la de inversor: con ella el bot solo podría leer) y el nombre exacto del servidor (p. ej. HFMarketsGlobal-Demo).",
        "Ajustes → HotForex · cuenta real: pega token, login, contraseña, servidor, plataforma y región; «Guardar» y luego «Probar conexión» (el bot lee la cuenta del terminal a través del puente; tarda 1–3 min la primera vez).",
        "«Probar conexión» debe mostrar equidad, moneda, apalancamiento y «trading permitido». Al probar la conexión el bot detecta el sufijo de símbolos de tu tipo de cuenta HFM (Premium sin sufijo, Zero «b», Pro «r», Cent «c», InfinityX «x») y lo aplica; «Comprobar símbolos» verifica que tus pares cotizan Y son operables en el bróker (un par de otro tipo de cuenta cotiza pero HFM responde «Trade is disabled»).",
        "Modo de operación: cumple la lista (cuenta vinculada y verificada, trading permitido, bot detenido, sin posiciones; se recomienda haber superado la Fase 0 del plan), escribe REAL y confirma. Las estadísticas se reinician con la equidad real.",
        "Inicia el bot. A partir de ahora las órdenes, el PnL y la equidad vienen de HFM; las señales se siguen calculando con velas de Yahoo. Si la conexión cae más de 60 s te avisará y no abrirá nada hasta recuperarse.",
        "Para volver a paper: detén el bot, cierra posiciones y pulsa «Volver a paper» (la cuenta paper vuelve a $500).",
      ] : [
        "En Bybit crea una API key con permisos de Contratos/Derivados (lectura + órdenes), sin retiros, idealmente restringida a la IP de tu servidor. Para practicar usa el entorno Demo (demo trading) o Testnet.",
        "Ajustes → Bybit · cuenta real: elige el entorno, pega key y secret y pulsa «Probar conexión»: verás equidad, disponible, posiciones y desfase horario (debe ser pequeño).",
        "Modo de operación: cumple la lista (credenciales verificadas, bot detenido, sin posiciones; se recomienda haber superado la Fase 0 del plan), escribe REAL y confirma. Las estadísticas se reinician con la equidad real de tu wallet UNIFIED.",
        "Inicia el bot. Las órdenes son a mercado con stop y objetivo adjuntos (tpslMode Full), el trailing se envía al exchange y los cierres hechos en Bybit se detectan en segundos.",
        "Importante: Bybit bloquea su API desde algunas regiones (p. ej. EE. UU.); el servidor que ejecute el bot debe estar en una región permitida. En paper, si Bybit no responde, los precios se toman de Bitget.",
        "Para volver a paper: detén el bot, cierra posiciones y pulsa «Volver a paper».",
      ],
    },
    {
      id: "vps", icon: "Server", title: "13 · Ejecutarlo en tu propio servidor (VPS)",
      steps: [
        "Pulsa el botón «Instalar en tu VPS» de la cabecera: descarga un zip con la app completa y actual (los tres bots, el chat IA expert y todas las estrategias; sin tus claves ni datos) y sigue la guía detallada con los comandos listos para copiar. La misma guía viaja dentro del zip en deploy/INSTALACION.md.",
        "Carpeta deploy/: `install.sh` instala Docker, genera secretos y contraseña, construye los contenedores (Mongo + backend + web con HTTPS automático si usas dominio) y programa los crons: revisión IA 08:00 UTC, walk-forward lunes 07:00, resumen semanal domingos 20:00 y copia de seguridad 03:00.",
        "`update-zip.sh archivo.zip` actualiza a una versión nueva descargada del panel (conserva claves, copias y base de datos); `backup.sh` crea una copia ahora (`--telegram` para enviarla); `restore.sh archivo.json.gz` restaura una copia; `reset-password.sh` cambia la contraseña si la olvidas; `clean-disk.sh` libera espacio (caché de compilación de Docker, imágenes huérfanas, journald).",
        "Las copias se guardan en deploy/backups/ (14 días) y también puedes descargarlas desde Ajustes → Copias de seguridad. Guarda a buen recaudo la CREDENTIAL_ENCRYPTION_KEY de backend.env: sin ella no se pueden leer las API keys de una copia.",
      ],
    },
    {
      id: "chat", icon: "MessageSquare", title: "14 · Chatea con IA expert",
      steps: [
        "Pulsa el botón negro y dorado «Chatea con IA expert» de la cabecera (en cualquiera de los tres bots): se abre un panel lateral con un experto en trading y gestión de riesgo que ve en vivo tus bots.",
        "Pregúntale lo que quieras: «¿por qué no ha abierto hoy?», «revisa el rendimiento de Pepperstone», «¿qué hace exactamente el filtro de rollover?» (puede leer el código, solo lectura), «¿subo el riesgo al 1,5 %?». El chip de arriba indica desde qué panel hablas; la conversación es una sola y compartida.",
        "Si propone cambiar un ajuste o ejecutar una acción, aparece una tarjeta con el detalle (antes → después, bot afectado) y los botones Aplicar / Descartar. Nada se ejecuta hasta que pulsas Aplicar; en modo REAL la tarjeta muestra un aviso adicional y pasa por las mismas comprobaciones que el panel.",
        "No puede tocar claves, contraseñas, el modo paper/real, el reinicio de cuentas ni el código. Usa el proveedor y modelo de Ajustes → IA (si configuras Gemini, el chat también usa Gemini).",
        "Memoria: a partir de 40 mensajes resume lo anterior y conserva los 10 últimos; recuerda tus preferencias a largo plazo. «Nueva conversación» (con confirmación) borra el hilo; la memoria a largo plazo se mantiene.",
      ],
    },
    ...(pep ? [{
      id: "lab", icon: "FlaskConical", title: "15 · Modo laboratorio (solo Pepperstone)",
      steps: [
        "Bajo la fila de KPIs del panel Pepperstone está la tarjeta «Modo laboratorio · solo Cruce EMA 10/20». Pulsa «Activar laboratorio» y confirma: tus ajustes se guardan y se aplica el preset (solo esa estrategia, 15 m, criterios relajados, 12 pares, filtro IA activo; apagados TF superior, trailing/parcial, filtros de mercado, noticias y revisión IA automática).",
        "Inicia el bot y deja que recoja data: la tarjeta muestra operaciones, acierto, PnL, pips, profit factor, cierres por cruce contrario y los motivos de cierre desde que lo activaste. Rendimiento → Por estrategia y el Historial tienen el detalle.",
        "No cambies ajustes a mano mientras dure la prueba: si lo haces, la tarjeta avisa de que la data deja de ser «solo Cruce EMA 10/20». Tampoco puedes pasar a REAL ni deshacer eventos de Evolución con el laboratorio activo.",
        "Para terminar pulsa «Desactivar y restaurar ajustes»: vuelve todo (ajustes, noticias, revisión automática) a como estaba y envía por Telegram el resumen de la data recogida. Aplicar un perfil de riesgo también lo termina: el perfil define sus ajustes y el resto vuelve al valor anterior.",
      ],
    }] : []),
  ];
}
