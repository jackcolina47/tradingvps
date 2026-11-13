"""FxEngine mixin · signals: scan loop, risk/market gates, AI validation and shadow trades, month-end fix scan and news trades."""
import time
import asyncio
from datetime import datetime, timezone, timedelta
from models import Signal, now_iso
from indicators import trend_label, htf_trend, snapshot
from strategies import evaluate, exit_signal, EXIT_ON_CROSS, EXIT_REASON
from fx.models import FxSettings
from fx.market import HTF, market_open, market_status, in_sessions, session_names
from fx.symbols import SPECS, pips
from fx import filters
from fx import regime as fx_regime
from fx.strategies_slow import fix_signal, fix_window
from fx.ai import evaluate_signal
import ai_funnel
import price_action
import ai_rules
from ai_advisor import label as ai_label
from fx.backtest import build_fx_df
from fx.shadow import open_shadow
from fx.engine_core import logger, BLOCK_BY, FX_STRATEGY_INFO


class SignalsMixin:
    async def _scan_loop(self):
        await asyncio.sleep(3)
        while True:
            settings = await self.get_settings()
            try:
                await self.scan_once(settings)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("scan error")
                await self.log("ERROR", f"Error en escaneo: {e}")
            self.next_scan_ts = time.time() + settings.scan_interval_sec
            try:
                await asyncio.wait_for(self.scan_event.wait(), timeout=settings.scan_interval_sec)
            except asyncio.TimeoutError:
                pass
            self.scan_event.clear()

    @staticmethod
    def _df(klines: list, atr_period: int = 14):
        return build_fx_df(klines, atr_period)

    async def scan_once(self, settings: FxSettings):
        if not market_open():
            if not self._closed_logged:
                self._closed_logged = True
                nxt = market_status()["next_change"]
                await self.log("SCAN", f"Mercado forex cerrado · sin escaneos hasta {nxt[:16].replace('T', ' ')} UTC")
            return
        self._closed_logged = False
        self.scanning = True
        found = 0
        htf_tf = HTF[settings.timeframe]
        try:
            for symbol in settings.symbols:
                try:
                    params = settings.symbol_params.get(symbol) or {}
                    df = self._df(await self.market.fetch_klines(symbol, settings.timeframe, 300, cache_sec=60), int(params.get("atr_period", 14)))
                    i = len(df) - 2
                    row = df.iloc[i]
                    htf = None
                    if settings.htf_filter:
                        htf = htf_trend(self._df(await self.market.fetch_klines(symbol, htf_tf, 200, cache_sec=300)))
                    if self.running:
                        await self._exit_on_cross(symbol, df, i)
                    sig = evaluate(df, i, settings.strategy, htf, params, settings.signal_mode, settings.disabled_strategies)
                    vol_pct = filters.vol_percentile(df["atr"].values, i)
                    self.analysis[symbol] = {
                        "trend": trend_label(row), "rsi": round(float(row["rsi"]), 1), "macd_hist": float(row["macd_hist"]),
                        "adx": round(float(row["adx"]), 1), "atr_pips": round(pips(symbol, float(row["atr"])), 1), "htf": htf,
                        "vol_pct": vol_pct, "vol_regime": filters.vol_label(vol_pct, settings), "season_bias": filters.seasonality_bias(symbol),
                        "signal": sig["side"] if sig else None, "signal_strategy": sig["strategy"] if sig else None, "updated_at": now_iso(),
                    }
                    if sig and self.running:
                        found += 1
                        await self._handle_signal(symbol, sig, settings, df, htf, htf_tf)
                except Exception as e:
                    logger.exception(f"scan {symbol}")
                    await self.log("ERROR", f"{symbol}: {e}")
            if self.running and "fix_reversion" not in settings.disabled_strategies and settings.strategy in ("all", "fix_reversion"):
                found += await self._scan_slow(settings)
            self.last_scan = now_iso()
            if self.running:
                await self.log("SCAN", f"Escaneo {settings.timeframe} completado · {len(settings.symbols)} pares · {found} señales")
        finally:
            self.scanning = False

    async def _exit_on_cross(self, symbol: str, df, i: int):
        """Strategy exits decided at candle close (ema_cross: the EMAs crossed back against the position)."""
        row = df.iloc[i]
        async for d in self.db.positions.find({"symbol": symbol, "strategy": {"$in": list(EXIT_ON_CROSS)}}, {"_id": 1, "side": 1, "strategy": 1}):
            if exit_signal(d["strategy"], d["side"], float(row["ema10"]), float(row["ema20"])):
                await self.log("INFO", f"{symbol}: la EMA10 volvió a cruzar la EMA20 en contra de la posición {d['side'].upper()} · cierre por regla de la estrategia")
                await self.close_position(str(d["_id"]), None, EXIT_REASON)

    async def _blocks(self, symbol: str, settings: FxSettings, acc: dict, side: str = None, horizon: str = "short"):
        open_docs = await self.db.positions.find({}, {"symbol": 1, "side": 1, "horizon": 1}).to_list(100)
        open_syms = [p["symbol"] for p in open_docs]
        if not market_open():
            return "mercado cerrado", "MERCADO", open_syms
        if horizon == "short" and not in_sessions(settings.sessions_allowed):
            return f"fuera de las sesiones permitidas ({session_names(settings.sessions_allowed)})", "SESION", open_syms
        if symbol in open_syms:
            return "ya existe una posición abierta en el par", "BLOQUEADA", open_syms
        slot = self._slot_block(open_docs, settings, horizon)
        if slot:
            return slot, "BLOQUEADA", open_syms
        if acc["daily_paused"]:
            return "límite de pérdida diaria alcanzado", "BLOQUEADA", open_syms
        roll = filters.rollover_block(settings.rollover_guard)
        if roll:
            return roll, "ROLLOVER", open_syms
        if side:
            exp = filters.exposure_block(open_docs, symbol, side, settings.currency_exposure_limit)
            if exp:
                return exp, "EXPOSICION", open_syms
        if self.live and self.hfm["down_since"]:
            return f"cuenta {self.bk} sin conexión con MetaApi", "BLOQUEADA", open_syms
        if self.live and self.cfg and symbol in (self.cfg.hfm_symbols_bad or []):
            return f"par no operable en {self.bk} con el sufijo de símbolos configurado (Ajustes → {self.bl} → Comprobar símbolos)", "BLOQUEADA", open_syms
        return None, None, open_syms

    async def _market_gates(self, symbol: str, side: str, settings: FxSettings, horizon: str) -> tuple:
        """Short-horizon only: volatility regime, intraday seasonality, USD regime and the FOMC rule. Returns (block, kind, size_factor, ctx)."""
        a = self.analysis.get(symbol, {})
        ctx = {"vol_pct": a.get("vol_pct"), "vol_regime": a.get("vol_regime"), "season_bias": filters.seasonality_bias(symbol)}
        if horizon != "short":
            return None, None, 1.0, ctx
        factor = 1.0
        vb, vf = filters.vol_gate(a.get("vol_pct"), settings)
        factor *= vf
        if vb:
            return vb, "VOLATILIDAD", factor, ctx
        sb, sf = filters.seasonality_gate(symbol, side, settings)
        factor *= sf
        if sb:
            return sb, "ESTACIONAL", factor, ctx
        if settings.usd_regime_filter:
            rg = await fx_regime.usd_regime(self.market, settings.usd_regime_sma)
            ctx["usd_regime"] = rg.get("state")
            rb = fx_regime.regime_block(rg, symbol, side)
            if rb:
                return rb, "REGIMEN_USD", factor, ctx
        if settings.fomc_rule and self.news and "USD" in SPECS[symbol]["base"] + SPECS[symbol]["quote"]:
            ev = filters.fomc_event(self.news.latest.get("events", []) if self.news.latest else [])
            if ev:
                rows = await self.market.fetch_klines(symbol, "1h", 100, cache_sec=120)
                direction = filters.post_event_direction(rows, ev["t"], self.market.tickers.get(symbol, {}).get("price"))
                ctx["fomc"] = {"event": ev["title"], "direction": direction}
                fb = filters.fomc_block(symbol, side, ev, direction, True)
                if fb:
                    return fb, "FOMC", factor, ctx
        return None, None, factor, ctx

    async def _handle_signal(self, symbol, sig, settings: FxSettings, df, htf, htf_tf, horizon: str = "short", tf: str = None):
        key = f"{symbol}:{sig['strategy']}:{sig['side']}:{sig['key_ts']}"
        if key in self.signal_keys:
            return
        self.signal_keys.add(key)
        tf = tf or settings.timeframe
        await self.log("SIGNAL", f"{symbol} {sig['side'].upper()} · {FX_STRATEGY_INFO[sig['strategy']]['name']} · {sig['score']} confirmaciones" + (" · largo plazo" if horizon == "long" else ""))
        acc = await self.account_summary()
        block, kind, open_syms = await self._blocks(symbol, settings, acc, sig["side"], horizon)
        news_block, risk_factor = self.news.gate(symbol) if self.news else (None, 1.0)
        if not block and news_block:
            block, kind = news_block, "NOTICIAS"
        g_block, g_kind, g_factor, g_ctx = await self._market_gates(symbol, sig["side"], settings, horizon)
        if not block and g_block:
            block, kind = g_block, g_kind
        pa = price_action.from_df(df, sig["side"], sig["price"], sig["indicators"].get("atr", 0), sig["sl"], sig["tp"], upto=len(df) - 1,
                                  max_run=settings.price_action_max_candles, swing_atr=settings.price_action_swing_atr)
        pa_block = price_action.hard_block(pa) if settings.price_action_block and horizon == "short" else ""
        if not block and pa_block:
            block, kind = pa_block, "PRECIO_EXTENDIDO"
        pf_block, pf_kind = ai_funnel.pre_filter(sig, settings.min_rr, SPECS[symbol]["spread"], settings.max_spread_sl_pct) if horizon == "short" else (None, None)
        if not block and pf_block:
            block, kind = pf_block, pf_kind
            logger.info(f"[STAGE 0 REJECT] {symbol} {sig['side']} · {pf_block}")
        risk_factor *= g_factor
        t = self.market.tickers.get(symbol, {})
        news_ctx = self.news.context(symbol) if self.news else None
        context = {"mode": "REAL" if self.live else "PAPER", "sessions": [x["id"] for x in market_status()["sessions"] if x["open"]], "htf_tf": htf_tf,
                   "htf_trend": {1: "ALCISTA", -1: "BAJISTA", 0: "NEUTRAL"}.get(htf, "N/D"), "price_now": t.get("price"), "change_24h": t.get("change_pct"),
                   "spread_pips": round(pips(symbol, SPECS[symbol]["spread"]), 1), "sl_pips": round(pips(symbol, abs(sig["price"] - sig["sl"])), 1),
                   "open_positions": len(open_syms), "max_positions": settings.max_positions, "daily_pnl_pct": acc["daily_pnl_pct"], "equity": acc["equity"],
                   "news_risk": (news_ctx or {}).get("mercado", {}).get("risk") if news_ctx else None, "news_pair": (news_ctx or {}).get("par") if news_ctx else None,
                   "news_size_factor": risk_factor, "ai_enabled": settings.ai_enabled, "ai_min_confidence": settings.ai_min_confidence,
                   "horizon": horizon, "price_action": pa, **g_ctx}
        base = dict(symbol=symbol, side=sig["side"], strategy=sig["strategy"], timeframe=tf, price=sig["price"], sl=sig["sl"], tp=sig["tp"],
                    score=sig["score"], reasons=sig["reasons"], indicators=sig["indicators"], key=key, context=context, horizon=horizon)
        if block:
            await self.log("RISK", f"{symbol}: señal descartada · {block}")
            res = await self.db.signals.insert_one(Signal(**base, ai_decision=kind, note=f"Bloqueada por {BLOCK_BY.get(kind, 'riesgo')}: {block}").to_mongo())
            if kind != "MERCADO":
                await open_shadow(self, str(res.inserted_id), symbol, sig, settings, kind, acc, risk_factor=risk_factor, note=block)
            return
        signal = Signal(**base, ai_decision="SIN_IA")
        approve, shadow_kind = True, None
        if settings.ai_enabled:
            await self.log("AI_EVAL", f"{ai_label()} evaluando {symbol} {sig['side'].upper()}...")
            closes = df["close"].iloc[-22:-1]
            ctx = {
                "htf_tf": htf_tf, "htf_trend": context["htf_trend"],
                "change_24h": t.get("change_pct", 0), "sessions": market_status()["sessions"],
                "recent_changes": [round(x, 3) for x in (closes.pct_change().dropna() * 100).tolist()],
                "open_positions": len(open_syms), "daily_pnl_pct": acc["daily_pnl_pct"],
                "news": news_ctx, "news_weight": settings.ai_news_weight,
                "horizon": horizon, "vol_regime": g_ctx.get("vol_regime"), "season_bias": g_ctx.get("season_bias"), "usd_regime": g_ctx.get("usd_regime"),
                "price_action": pa,
                "learned_rules": await ai_rules.active(self.db, symbol),
            }
            try:
                fast = None
                if settings.ai_funnel_enabled and horizon == "short":
                    fast = await ai_funnel.quick_filter(symbol, settings.timeframe, sig, ctx, self.profile.ai_kind)
                if fast and not fast["pass"]:
                    signal.ai_decision, signal.ai_confidence, signal.ai_stage, signal.ai_model, signal.ai_ms = "RECHAZADA", fast["confidence"], 1, fast["model"], fast["ms"]
                    signal.ai_reasoning = f"[Filtro rápido] {fast['reason'] or 'sin alineación técnica'}"
                    signal.ai_min_confidence = settings.ai_min_confidence
                    approve, shadow_kind = False, "FILTRO_IA"
                else:
                    res = await ai_funnel.final_eval(evaluate_signal, symbol, settings.timeframe, sig, ctx, self.profile.ai_kind)
                    signal.ai_decision, signal.ai_confidence, signal.ai_risk, signal.ai_reasoning = res["decision"], res["confidence"], res["risk"], res["reasoning"]
                    signal.ai_min_confidence = settings.ai_min_confidence
                    signal.ai_stage, signal.ai_model, signal.ai_ms = 2, res["model"], res["ms"] + (fast["ms"] if fast else 0)
                    signal.ai_adjusted_sl, signal.ai_adjusted_tp = res.get("adjusted_sl"), res.get("adjusted_tp")
                    approve = res["decision"] == "APROBADA" and res["confidence"] >= settings.ai_min_confidence  # risk is informative only
                    if res["decision"] == "APROBADA" and not approve:
                        signal.note = f"Confianza {res['confidence']}% menor al mínimo ({settings.ai_min_confidence}%)"
                        shadow_kind = "BAJA_CONFIANZA"
                    elif not approve:
                        shadow_kind = "RECHAZADA"
            except Exception as e:
                logger.exception("ai error")
                signal.ai_decision, signal.note, approve, shadow_kind = "ERROR", f"Error IA: {str(e)[:180]}", False, "ERROR"
            await self.log("AI_EVAL", f"{symbol}: {signal.ai_decision} · confianza {signal.ai_confidence}% · riesgo {signal.ai_risk}"
                           + (f" · etapa {signal.ai_stage} · {signal.ai_model} · {signal.ai_ms} ms" if signal.ai_stage else ""))
            await self.tg(f"🤖 <b>{signal.ai_model or ai_label()} · {symbol} {sig['side'].upper()}</b>: {signal.ai_decision} ({signal.ai_confidence}% · riesgo {signal.ai_risk})\n"
                          f"{signal.ai_reasoning[:300] or signal.note}", "ai")
        res = await self.db.signals.insert_one(signal.to_mongo())
        sid = str(res.inserted_id)
        if approve:
            risk_reason = ("riesgo de noticias" if news_block or (self.news and self.news.gate(symbol)[1] < 1) else "volatilidad extrema / sesgo estacional") if risk_factor < 1 else ""
            pos, reason = await self.open_position(symbol, sig, settings, sid, risk_factor, risk_reason, expires_at=sig.get("expires_at"), horizon=horizon)
            await self.db.signals.update_one({"_id": res.inserted_id}, {"$set": {"executed": True} if pos else {"note": f"No ejecutada: {reason}"}})
            if not pos:
                await open_shadow(self, sid, symbol, sig, settings, "NO_EJECUTADA", acc, signal.ai_confidence, signal.ai_risk, risk_factor, note=reason or "")
        elif shadow_kind:
            await open_shadow(self, sid, symbol, sig, settings, shadow_kind, acc, signal.ai_confidence, signal.ai_risk, risk_factor, note=signal.note or signal.ai_reasoning)

    async def _scan_slow(self, settings: FxSettings) -> int:
        """Month-end London fix reversion on 15m candles (long horizon). Only evaluated inside the 30-minute window after the fix (two 15m candles)."""
        now = datetime.now(timezone.utc)
        if not fix_window(now):
            return 0
        found = 0
        for symbol in settings.symbols:
            if SPECS[symbol]["group"] == "Metales":
                continue
            try:
                price = self.market.tickers.get(symbol, {}).get("price")
                rows15 = await self.market.fetch_klines(symbol, "15m", 300, cache_sec=60)
                df1h = self._df(await self.market.fetch_klines(symbol, "1h", 300, cache_sec=120))
                sig = fix_signal(symbol, rows15, float(df1h["atr"].iloc[-2]), now, price)
                if sig:
                    found += 1
                    await self._handle_signal(symbol, sig, settings, df1h, None, "1h", horizon="long", tf="15m")
            except Exception as e:
                logger.exception(f"fix scan {symbol}")
                await self.log("ERROR", f"{symbol}: fix reversion · {e}")
        return found

    # ---------- news trading ----------
    async def news_trade(self, symbol: str, side: str, confidence: int, reasoning: str, event: dict, cfg) -> dict:
        settings = await self.get_settings()
        if not self.running:
            return {"status": "OMITIDA", "reason": "bot detenido"}
        if symbol not in settings.symbols:
            return {"status": "OMITIDA", "reason": "par no activo en ajustes"}
        acc = await self.account_summary()
        block, _, _ = await self._blocks(symbol, settings, acc, side)
        if block:
            return {"status": "OMITIDA", "reason": block}
        tickers = await self.market.fetch_tickers(settings.symbols, cache_sec=5)
        price = tickers.get(symbol, {}).get("price")
        if not price:
            return {"status": "OMITIDA", "reason": "sin precio"}
        df = self._df(await self.market.fetch_klines(symbol, "15m", 200, cache_sec=20))
        row = df.iloc[-2]
        atr = float(row["atr"])
        t_event = datetime.fromisoformat(event["time"])
        ref = None
        for k in await self.market.fetch_klines(symbol, "5m", 300, cache_sec=20):
            if k[0] <= int(t_event.timestamp() * 1000):
                ref = k[1]
        move = (price - ref) if ref else 0.0
        confirmed = (move > 0) if side == "long" else (move < 0)
        move_txt = f"{pips(symbol, move):+.1f} pips desde la publicación"
        if cfg.news_trade_confirm_price and not confirmed:
            return {"status": "OMITIDA", "reason": f"el precio no confirma ({move_txt})"}
        long = side == "long"
        sl = price - cfg.news_trade_sl_atr * atr if long else price + cfg.news_trade_sl_atr * atr
        tp = price + cfg.news_trade_tp_atr * atr if long else price - cfg.news_trade_tp_atr * atr
        key = f"{symbol}:news_event:{side}:{event['key']}"
        if key in self.signal_keys:
            return {"status": "OMITIDA", "reason": "evento ya operado en este par"}
        self.signal_keys.add(key)
        label = f"{event['country']} {event['title']}"
        sig = {"side": side, "strategy": "news_event", "price": price, "sl": sl, "tp": tp, "atr": atr, "score": max(1, confidence // 10),
               "reasons": [f"Noticia: {label} · dato {event.get('actual') or 'n/d'} vs previsión {event.get('forecast') or 'n/d'} · sorpresa {event.get('surprise', '')}",
                           f"Precio confirma: {move_txt}", f"SL {cfg.news_trade_sl_atr}×ATR15m · TP {cfg.news_trade_tp_atr}×ATR15m · cierre máx. {cfg.news_trade_max_hours}h"],
               "key_ts": int(t_event.timestamp() * 1000), "indicators": snapshot(row)}
        signal = Signal(symbol=symbol, side=side, strategy="news_event", timeframe="15m", price=price, sl=sl, tp=tp, score=sig["score"], reasons=sig["reasons"],
                        indicators=sig["indicators"], ai_decision="APROBADA", ai_confidence=confidence, ai_risk="MEDIO", ai_reasoning=reasoning,
                        note=f"Operación de noticias · {label}", key=key)
        res = await self.db.signals.insert_one(signal.to_mongo())
        expires = (datetime.now(timezone.utc) + timedelta(hours=cfg.news_trade_max_hours)).isoformat()
        pos, reason = await self.open_position(symbol, sig, settings, str(res.inserted_id), cfg.news_trade_risk_factor, "operación de noticias", expires_at=expires)
        if not pos:
            await self.db.signals.update_one({"_id": res.inserted_id}, {"$set": {"note": f"No ejecutada: {reason}"}})
            await open_shadow(self, str(res.inserted_id), symbol, sig, settings, "NO_EJECUTADA", acc, confidence, "MEDIO", cfg.news_trade_risk_factor, note=reason or "")
            return {"status": "OMITIDA", "reason": reason}
        await self.db.signals.update_one({"_id": res.inserted_id}, {"$set": {"executed": True}})
        return {"status": "EJECUTADA", "reason": move_txt, "position_id": pos.id, "entry": price, "sl": sl, "tp": tp, "lots": pos.qty}
