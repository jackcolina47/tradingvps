# AI Trading · Embudo IA

Panel web (React + FastAPI + MongoDB) con tres bots de trading: **Bybit** (futuros perpetuos USDT), **HotForex** y **Pepperstone** (forex y metales vía MetaApi).

## Cómo decide una entrada

Cada 60 s, dentro del horario operativo y con huecos libres (`backend/funnel.py`):

1. **Paso 0 · filtro técnico (sin IA):** EMA 20/50/200, RSI, MACD, ADX, ATR, actividad y zonas de oferta/demanda.
2. **Paso 1 · modelo rápido:** elige de 0 a 3 candidatos en una llamada. En Bybit es multitemporal: Gemini 3.5 Flash evalúa 4H y 1H, y nunca se opera contra la tendencia de 4H.
3. **Paso 2 · modelo potente:** BUY / SELL / PASS con un % de confianza, aplicando las reglas aprendidas.

- Un candidato del Paso 1 vuelve al Paso 2 cada 5 minutos hasta que haya una operación ejecutable o se llene el máximo de posiciones.
- `ai_min_confidence` (10–90): confianza mayor que el umbral → se ejecuta; igual o menor → operación sombra.
- Tamaño: `(capital × riesgo %) ÷ distancia al stop`. SL/TP fijos por ATR (2× / 4×) o los que propone la IA.

## Horario y eventos rojos (`backend/trade_guard.py`)

Hora de Nueva York, todos los días.

| Bot | Ventanas |
| --- | --- |
| FX | 03:00–04:00 · 08:00–12:00 · 19:00–00:00 |
| Bybit | 03:00–05:00 · 08:30–12:00 · 19:00–23:00 |

El calendario de eventos rojos se descarga una vez al día. Los pares afectados quedan bloqueados ±15 min; 15 min antes las posiciones en ganancia pasan a breakeven y las que pierden se cierran. En Bybit se cancelan las órdenes límite pendientes de los pares afectados.

## Estructura

```
backend/
  server.py            API del bot Bybit y arranque
  engine.py            motor Bybit (paper / REAL)
  funnel.py            embudo IA compartido por los tres bots
  trade_guard.py       horario NY y calendario de eventos rojos
  ai_rules.py · trade_learning.py   reglas aprendidas
  review.py · plan.py · weekly.py   revisión IA, plan de crecimiento, resumen semanal
  maker.py             órdenes límite maker (Bybit)
  fx/                  motor FX (HotForex, Pepperstone), MetaApi
  routers/             rutas por área (IA, cron, copias, chat…)
  chat/                asistente «IA expert»
frontend/src/
  App.js               panel Bybit
  fx/                  panel FX
  components/bot/      componentes compartidos (GuardStrip, RedCalendar, SettingsPanel…)
  components/docs/     guía del bot dentro de la app
deploy/                kit de instalación en VPS (ver deploy/INSTALACION.md)
```

## Tareas programadas

`/api/cron/ai-review` (08:00 UTC), `/api/cron/weekly-summary` (domingo 20:00 UTC) y `/api/cron/backup` (03:00 UTC). Requieren `WEBHOOK_CRON_SECRET`.

## Pruebas

```
cd backend && python -m pytest tests -q
```

Se eliminaron las estrategias estáticas, el backtest, el optimizador, el walk-forward, los perfiles de riesgo, el laboratorio y el agente de noticias con titulares.
