# AI Trading · guía de la aplicación (conocimiento del asistente)

## Qué es
Panel web (español) con tres bots de trading automático que comparten clave de acceso, IA, Telegram y copias de seguridad:
- **Bybit AI Trader** (`/bybit`, API `/api/*`, clave de bot `crypto`): futuros perpetuos USDT. Datos Bybit v5; si la región está bloqueada usa Bitget como espejo.
- **HotForex AI Trading** (`/hotforex`, API `/api/fx/*`, clave `fx`): forex y metales (12 pares) vía MetaApi/MetaTrader.
- **Pepperstone AI trading** (`/pepperstone`, API `/api/pepper/*`, clave `pepper`): el mismo motor FX como instancia independiente.
Cada bot tiene cuenta paper propia (capital inicial $500), ajustes, posiciones, historial, señales, logs, plan y evolución independientes.

## Arquitectura actual: Embudo IA (`funnel.py`)
Ya NO existen estrategias estáticas, backtest, optimizador, walk-forward, perfiles de riesgo, modos test, laboratorio ni agente de noticias con titulares/sentimiento.
Disparo en :00/:15/:30/:45 + 8 s (`funnel.next_trigger`), solo dentro del horario operativo y con huecos libres:
- **Paso 0** (sin IA): EMA 20/50/200, RSI, MACD, ADX, ATR, actividad (volumen o rango) y zonas de oferta/demanda. Descarta ADX < 18 o volumen < 0,8× (FX sin bróker conectado: solo ADX).
- **Paso 1** (modelo rápido): elige 0–3 candidatos en una llamada. No se repite si ningún par se movió 0,15 ATR en la misma vela.
- **Multitemporal (los tres bots)**: el Paso 1 usa Gemini 3.5 Flash (respaldo: modelo rápido normal) y evalúa 4H (estructura) + 1H (confirmación); `timeframe` es la temporalidad de entrada (recomendado 15m). Contra la tendencia de 4H se permite solo como rebote (contratendencia): SL fijo, TP de la IA antes de la primera EMA/zona de 4H-1H, R:R ≥ 1,5 (`COUNTER_MIN_RR`); si no, queda como CONTRA_4H sin abrir. El modelo potente es decisivo: PASS solo ante conflicto severo.
- **Paso 2** (modelo potente + reglas aprendidas): BUY / SELL / PASS con confianza 0–100.
- Un candidato del Paso 1 vuelve al Paso 2 en cada disparo de 15 minutos hasta que haya operación ejecutable o se llene `max_positions`. Los pares descartados en el Paso 1 no llegan al Paso 2.
- `ai_min_confidence` (10..90 en pasos de 10): confianza > umbral → se ejecuta; ≤ umbral → sombra `BAJA_CONFIANZA` (una por vela).
- SL/TP: `atr_levels_enabled` → `atr_sl_mult`×ATR / `atr_tp_mult`×ATR (2 / 4 por defecto); apagado → niveles de la IA si son válidos (stop 0,5–4 ATR, R:R ≥ 1).
- Tamaño: (equidad actual × `risk_per_trade` %) ÷ (distancia al stop + comisiones/spread): cada stop cuesta `risk_per_trade` % (1 % por defecto) de la equidad. `atr_levels_enabled` viene encendido (SL 2×ATR, TP 4×ATR). FX: `min_lot_max_risk` 1 % por defecto (si el lote mínimo arriesga más, no se abre).
- FX · regla del viernes (`close_before_weekend`): sin entradas desde las 12:00 NY y cierre de todo a las 15:30 NY (motivo FIN_SEMANA).
- FX: con la cuenta MetaApi desplegada usa velas y volumen de ticks del bróker; con Yahoo usa el rango de la vela.

## Horario y eventos rojos (`trade_guard.py`)
Hora de Nueva York, todos los días. FX: 03:00–04:00, 08:00–12:00, 19:00–00:00 (y mercado forex abierto). Cripto: 03:00–05:00, 08:30–12:00, 19:00–23:00.
Calendario de eventos rojos descargado 1 vez al día. Bloqueo de entradas ±15 min en los pares afectados (FX: pares con la divisa; cripto: eventos USD).
15 min antes: posiciones afectadas en ganancia → breakeven; en pérdida → cierre con motivo `NOTICIA`. En Bybit se cancelan las órdenes límite pendientes de los pares afectados.
Herramienta `get_guard` y campo `horario_y_eventos_rojos` del snapshot.

## Ajustes de cada bot (Settings)
symbols, timeframe, risk_per_trade, max_positions, leverage, daily_loss_limit, ai_min_confidence, atr_levels_enabled, atr_sl_mult, atr_tp_mult, breakeven_enabled, be_start_r, trailing_enabled, trail_start_r, trail_atr_mult, partial_tp_enabled, partial_tp_r, partial_tp_pct, rules_auto_learn, plan_*.
Solo cripto: entry_order_type (market | maker), maker_wait_bars, auto_prune_enabled, auto_prune_pf, auto_prune_min_trades.
Solo FX: close_before_weekend, rollover_guard, currency_exposure_limit, min_lot_max_risk, sessions_allowed.
Otros campos antiguos que aparezcan en el snapshot (strategy, signal_mode, filtros de régimen, estacionalidad…) no tienen efecto: no propongas cambiarlos.

## Ajustes generales (Config)
IA (proveedor, modelo potente, modelo rápido del embudo), Telegram (token, chats, alertas por tipo y por bot), revisión IA automática (auto_review_*), alertas de desconexión, chat.

## Se mantiene
Paper / REAL, Telegram, reglas aprendidas (`ai_rules`, aprendizaje tras cada cierre), Buy/Sell manual desde el gráfico (estrategia `manual`), breakeven, trailing, cierre parcial, operaciones sombra, plan de crecimiento simplificado, revisión IA automática y asesor IA simplificados (solo con operaciones reales y sombras), evolución con deshacer, copias de seguridad, resumen semanal, poda automática de pares (Bybit).

## Pestañas del panel
Posiciones, Historial, Rendimiento (por par, lado, motivo, día, sombras, recorrido), Pares 30d (solo Bybit), Eventos rojos, Plan de crecimiento, Evolución. Abajo: Configuración y registro.

## Conceptos
1 R = distancia al stop inicial. MFE/MAE = recorrido máximo a favor/en contra. PF = beneficio bruto / pérdida bruta. DD = drawdown desde el pico.

## Hechos prácticos
- Fuera de horario no hay llamadas a la IA. Si el bot «no opera», revisa: horario, eventos rojos, máximo de posiciones, límite diario, umbral de confianza.
- Operaciones anteriores al embudo conservan el nombre de su estrategia antigua solo como historial.
- Cambiar a REAL: bot detenido, sin posiciones, credenciales verificadas. Este asistente no puede hacerlo.
- Crons del VPS: revisión IA 08:00 UTC, resumen semanal domingo 20:00 UTC, copia 03:00 UTC.
- Logs y crons en UTC; horario operativo en hora de Nueva York.
