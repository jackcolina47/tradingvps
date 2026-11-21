"""Forex & metals news agent: RSS headlines + ForexFactory calendar (configurable currencies) → Claude → currency bias, pair risk, alerts,
event gate, and news trading (post-release data extraction → directional trades when confidence is high)."""
import re
import time
import asyncio
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import httpx
from models import now_iso
from ai_advisor import _chat, _ask, record_usage
from news import parse_feed, _parse_json, _age_min, _risk, CALENDAR_URL, HISTORY_DAYS, ALERT_TTL_MIN, RETRY_AFTER_FAIL_SEC, NewsAgent
from fx import ai as fx_ai
from fx.symbols import CURRENCIES, CURRENCY_NAMES, relevant_pairs, pairs_for_currency, currencies

logger = logging.getLogger("fx.news")
FEEDS = [("ForexLive", "https://www.forexlive.com/feed/news"), ("ForexLive BC", "https://www.forexlive.com/feed/centralbank"),
         ("Investing Forex", "https://www.investing.com/rss/news_1.rss"), ("Investing Commodities", "https://www.investing.com/rss/news_11.rss"),
         ("FXEmpire", "https://www.fxempire.com/api/v1/en/articles/rss/news"), ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines")]
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
COUNTRY_EN = {"USD": "US", "EUR": "eurozone", "GBP": "UK", "CHF": "Swiss", "CAD": "Canada", "NZD": "New Zealand", "AUD": "Australia", "JPY": "Japan", "CNY": "China"}
SYSTEM = (
    "Eres analista macro y de riesgo de eventos para un bot cuantitativo de forex y metales (oro, plata). Recibes titulares recientes (fuente y antigüedad) "
    "y el calendario económico de alto y medio impacto de las divisas operadas. Tu salida alimenta la gestión de riesgo, no genera operaciones. Sé sobrio: "
    "la mayoría de titulares son ruido; marca alertas solo ante hechos graves y verificables (decisión o giro inesperado de un banco central, intervención "
    "cambiaria, crisis bancaria o de deuda, escalada geopolítica grave, dato macro muy desviado, cierre de gobierno, aranceles relevantes). "
    "No inventes hechos. Responde EXCLUSIVAMENTE con JSON válido: "
    '{"market":{"sentiment":entero -100..100 (apetito por riesgo: positivo = risk-on),"risk":"BAJO"|"MEDIO"|"ALTO","summary":"2-3 frases en español con lo que mueve hoy a divisas y metales"},'
    '"currencies":{"USD":{"bias":entero -100..100 (positivo = divisa fuerte),"note":"1 frase"},...} para USD, EUR, GBP, CHF, CAD, NZD, AUD, JPY, XAU, XAG,'
    '"pairs":{"SYMBOL":{"sentiment":entero -100..100 (positivo = alcista para el par),"risk":"BAJO"|"MEDIO"|"ALTO","note":"1 frase en español"}} solo para pares de la lista con noticia relevante,'
    '"alerts":[{"symbol":"SYMBOL o MARKET","severity":"ALTA"|"CRITICA","title":"título corto","why":"1 frase: por qué afecta al precio"}]}'
)


def _words(title: str) -> list:
    return [w for w in re.findall(r"[a-z]{3,}", title.lower()) if w not in ("the", "and", "for", "with", "rate", "m/m", "y/y", "q/q", "final", "flash", "prelim", "revised", "index")]


class FxNewsAgent:
    KIND = "fx-news"
    interval_min = NewsAgent.interval_min
    fingerprint = staticmethod(NewsAgent.fingerprint)

    def __init__(self, db, engine):
        self.db, self.engine = db, engine
        self.cfg = None
        self.latest = None
        self.task = None
        self._seen_alerts = set()
        self._last_risk = None
        self._retry_at = 0.0
        self._processed = set()
        self._pending_since = {}
        self.client = httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; FxBot/1.0)"})

    async def configure(self, cfg):
        self.cfg = cfg

    async def reload(self):
        doc = await self.db.news.find_one({"_id": "latest"}, {"_id": 0})
        self.latest = doc
        self._seen_alerts = {a["key"] for a in (doc or {}).get("alerts", [])}
        self._last_risk = (doc or {}).get("market", {}).get("risk")
        self._processed = set()
        async for d in self.db.news_trades.find({}, {"key": 1}).sort("_id", -1).limit(300):
            self._processed.add(d["key"])

    async def init(self):
        await self.reload()
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        await asyncio.sleep(20)
        while True:
            try:
                if self.cfg and self.cfg.news_enabled and not self.engine.profile.ema_only and time.time() >= self._retry_at:
                    if not self.latest or _age_min(self.latest["updated_at"]) >= self.interval_min():
                        await self.refresh()
                    if self.cfg.news_trading_enabled:
                        await self.news_trading_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("fx news loop")
                self._retry_at = time.time() + RETRY_AFTER_FAIL_SEC
                await self.engine.log("ERROR", f"Agente de noticias forex: {str(e)[:140]} · se mantiene el análisis anterior · nuevo intento en {RETRY_AFTER_FAIL_SEC // 60} min")
            await asyncio.sleep(45)

    # ---------- sources ----------
    async def _feed(self, source: str, url: str) -> list:
        try:
            r = await self.client.get(url)
            r.raise_for_status()
            return parse_feed(source, r.text)
        except Exception as e:
            logger.warning(f"feed {source}: {e}")
            return []

    async def fetch_headlines(self) -> list:
        results = await asyncio.gather(*(self._feed(s, u) for s, u in FEEDS))
        seen, out = set(), []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        for h in sorted((h for group in results for h in group), key=lambda x: x["published"], reverse=True):
            k = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
            if k in seen or h["published"] < cutoff:
                continue
            seen.add(k)
            out.append(h)
        return out[:70]

    async def fetch_events(self) -> list:
        countries = set((self.cfg.news_countries if self.cfg else CURRENCIES) or CURRENCIES)
        try:
            r = await self.client.get(CALENDAR_URL)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            logger.warning(f"calendar: {e}")
            return (self.latest or {}).get("events", [])
        now = datetime.now(timezone.utc)
        out = []
        for e in rows:
            if e.get("country") not in countries or e.get("impact") not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except Exception:
                continue
            if dt < now - timedelta(hours=3) or dt > now + timedelta(hours=72):
                continue
            out.append({"title": e["title"], "country": e["country"], "country_name": CURRENCY_NAMES.get(e["country"], e["country"]), "time": dt.isoformat(),
                        "impact": "ALTO" if e["impact"] == "High" else "MEDIO", "forecast": e.get("forecast", ""), "previous": e.get("previous", ""),
                        "key": hashlib.sha1(f"{e['title']}|{e['country']}|{dt.isoformat()}".encode()).hexdigest()[:12]})
        return sorted(out, key=lambda x: x["time"])

    # ---------- analysis ----------
    async def analyze(self, headlines: list, events: list, symbols: list) -> dict:
        lines = [f"- [{h['source']} · hace {_age_min(h['published'])} min] {h['title']}" + (f" — {h['summary'][:120]}" if h['summary'] else "") for h in headlines[:50]]
        ev = [f"- {e['time'][:16]} UTC · {e['country']} · {e['title']} · impacto {e['impact']}" + (f" · previsión {e['forecast']}" if e['forecast'] else "") for e in events[:14]] or ["- ninguno en 72h"]
        prompt = (f"PARES ACTIVOS: {', '.join(symbols)}\n\nTITULARES (más recientes primero):\n" + "\n".join(lines) +
                  "\n\nCALENDARIO ECONÓMICO (UTC):\n" + "\n".join(ev) +
                  "\n\nDevuelve solo el JSON. Dentro de los textos no uses comillas dobles (usa comillas simples) ni saltos de línea.")
        chat = _chat(SYSTEM, f"{self.engine.profile.ai_kind}-news")
        text = await _ask(chat, prompt)
        data = _parse_json(text)
        if data is None:
            text = await _ask(chat, "Tu respuesta anterior no era JSON válido. Repite la respuesta como un único objeto JSON válido, sin texto adicional, sin comillas dobles dentro de los textos.")
            data = _parse_json(text)
        if data is None:
            logger.warning(f"fx news: respuesta IA no válida ({len(text or '')} caracteres): {text!r}")
            raise ValueError(f"Respuesta IA no válida: {text[:160]}")
        market = data.get("market") or {}
        cur = {}
        for k, v in (data.get("currencies") or {}).items():
            k = str(k).upper()
            if k in CURRENCY_NAMES and isinstance(v, dict):
                cur[k] = {"bias": int(max(-100, min(100, int(v.get("bias", 0) or 0)))), "note": str(v.get("note", ""))[:160]}
        pairs = {k: v for k, v in (data.get("pairs") or {}).items() if k in symbols and isinstance(v, dict)}
        alerts = []
        for a in data.get("alerts") or []:
            if not isinstance(a, dict) or not a.get("title"):
                continue
            sym = str(a.get("symbol") or "MARKET").upper()
            alerts.append({"symbol": sym if sym in symbols or sym == "MARKET" else "MARKET", "severity": "CRITICA" if str(a.get("severity", "")).upper().startswith("CRIT") else "ALTA",
                           "title": str(a["title"])[:140], "why": str(a.get("why", ""))[:220],
                           "key": hashlib.sha1(str(a["title"]).lower().encode()).hexdigest()[:12], "at": now_iso()})
        return {
            "market": {"sentiment": int(max(-100, min(100, int(market.get("sentiment", 0) or 0)))), "risk": _risk(market.get("risk")), "summary": str(market.get("summary", ""))[:600]},
            "currencies": cur,
            "pairs": {k: {"sentiment": int(max(-100, min(100, int(v.get("sentiment", 0) or 0)))), "risk": _risk(v.get("risk")), "note": str(v.get("note", ""))[:200]} for k, v in pairs.items()},
            "alerts": alerts,
        }

    async def refresh(self, force: bool = False) -> dict:
        settings = await self.engine.get_settings()
        headlines, events = await asyncio.gather(self.fetch_headlines(), self.fetch_events())
        for h in headlines:
            h["symbols"] = relevant_pairs(f"{h['title']} {h['summary']}", settings.symbols)
        fp = self.fingerprint(headlines, events, settings.symbols)
        if not force and self.latest and self.latest.get("fingerprint") == fp and self.latest.get("market"):
            doc = {**self.latest, "events": events, "headlines": headlines[:45], "updated_at": now_iso(), "skipped_ai": self.latest.get("skipped_ai", 0) + 1}
            await self.db.news.update_one({"_id": "latest"}, {"$set": {k: doc[k] for k in ("events", "headlines", "updated_at", "skipped_ai")}})
            self.latest = doc
            await record_usage(self.KIND, skipped=True)
            logger.info(f"{self.KIND}: sin cambios en titulares/calendario · análisis IA omitido")
            return doc
        analysis = await self.analyze(headlines, events, settings.symbols)
        first_seen = {a["key"]: a["at"] for a in (self.latest or {}).get("alerts", [])}
        for a in analysis["alerts"]:
            a["at"] = first_seen.get(a["key"], a["at"])
        now = now_iso()
        doc = {**analysis, "events": events, "headlines": headlines[:45], "updated_at": now, "analysis_at": now, "fingerprint": fp, "skipped_ai": 0, "symbols": settings.symbols}
        await self.db.news.update_one({"_id": "latest"}, {"$set": doc}, upsert=True)
        await self.db.news_history.insert_one({"at": doc["updated_at"], "market": doc["market"], "alerts": [a["title"] for a in doc["alerts"]]})
        await self.db.news_history.delete_many({"at": {"$lt": (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()}})
        self.latest = doc
        await self._announce(doc)
        return doc

    async def _announce(self, doc: dict):
        risk = doc["market"]["risk"]
        if risk != self._last_risk:
            self._last_risk = risk
            await self.engine.log("NEWS", f"Riesgo de mercado por noticias: {risk} · apetito por riesgo {doc['market']['sentiment']:+d} · {doc['market']['summary'][:140]}")
        for a in doc["alerts"]:
            if a["key"] in self._seen_alerts:
                continue
            self._seen_alerts.add(a["key"])
            await self.engine.log("NEWS", f"ALERTA {a['severity']} · {a['symbol']} · {a['title']}")
            await self.engine.tg(f"📰 <b>Alerta de noticias {a['severity']}</b> · {a['symbol']}\n<b>{a['title']}</b>\n{a['why']}", "news")

    # ---------- gates & context ----------
    def _event_block(self, symbol: str):
        if not (self.cfg and self.cfg.news_block_events and self.latest):
            return None
        now = datetime.now(timezone.utc)
        curs = set(currencies(symbol)) | ({"USD"} if symbol in ("XAUUSD", "XAGUSD") else set())
        for e in self.latest.get("events", []):
            if e["impact"] != "ALTO" or e["country"] not in curs:
                continue
            t = datetime.fromisoformat(e["time"])
            if t - timedelta(minutes=self.cfg.news_event_before_min) <= now <= t + timedelta(minutes=self.cfg.news_event_after_min):
                return f"evento de alto impacto {e['country']} ({e['title']} · {t.strftime('%H:%M')} UTC)"
        return None

    def _active_critical(self) -> list:
        latest = self.latest or {}
        return [a for a in latest.get("alerts", []) if a["severity"] == "CRITICA" and _age_min(a.get("at") or latest["updated_at"]) <= ALERT_TTL_MIN]

    def gate(self, symbol: str):
        if not (self.cfg and self.cfg.news_enabled and self.latest):
            return None, 1.0
        block = self._event_block(symbol)
        if not block and self.cfg.news_block_critical:
            for a in self._active_critical():
                if a["symbol"] in (symbol, "MARKET"):
                    block = f"noticia crítica · {a['title']}"
                    break
        factor = 1.0
        if self.cfg.news_reduce_on_high_risk:
            pair = self.latest.get("pairs", {}).get(symbol, {})
            if self.latest["market"]["risk"] == "ALTO" or pair.get("risk") == "ALTO":
                factor = self.cfg.news_size_factor
        return block, factor

    def context(self, symbol: str):
        if not (self.cfg and self.cfg.news_enabled and self.latest):
            return None
        now = datetime.now(timezone.utc)
        base, quote = currencies(symbol)
        cur = self.latest.get("currencies", {})
        bias = [f"{c}: {cur[c]['bias']:+d} ({cur[c]['note']})" for c in (base, quote) if c in cur]
        return {
            "actualizado_hace_min": _age_min(self.latest["updated_at"]),
            "mercado": self.latest["market"], "par": self.latest.get("pairs", {}).get(symbol),
            "alertas": [f"{a['severity']} {a['symbol']}: {a['title']}" for a in self.latest.get("alerts", [])[:4]],
            "eventos_proximos": [f"{e['country']} {e['title']} en {int((datetime.fromisoformat(e['time']) - now).total_seconds() / 60)} min ({e['impact']})"
                                 for e in self.latest.get("events", []) if datetime.fromisoformat(e["time"]) > now and e["country"] in (base, quote, "USD")][:3],
            "titulares_del_par": ([f"Sesgo por divisa → {' · '.join(bias)}"] if bias else []) + [h["title"] for h in self.latest.get("headlines", []) if symbol in h.get("symbols", [])][:3],
        }

    def summary(self) -> dict:
        enabled = bool(self.cfg and self.cfg.news_enabled)
        trading = bool(self.cfg and self.cfg.news_trading_enabled)
        if not self.latest:
            return {"enabled": enabled, "ready": False, "news_trading": trading}
        now = datetime.now(timezone.utc)
        upcoming = [e for e in self.latest.get("events", []) if e["impact"] == "ALTO" and datetime.fromisoformat(e["time"]) > now]
        blocked = next((b for s in self.latest.get("symbols", []) if (b := self._event_block(s))), None)
        return {"enabled": enabled, "ready": True, "risk": self.latest["market"]["risk"], "sentiment": self.latest["market"]["sentiment"],
                "updated_at": self.latest["updated_at"], "analysis_at": self.latest.get("analysis_at"), "interval_min": self.interval_min() if self.cfg else None,
                "blocked": blocked, "critical": [a["symbol"] for a in self._active_critical()],
                "next_event": upcoming[0] if upcoming else None, "alerts": len(self.latest.get("alerts", [])), "news_trading": trading,
                "currencies": {k: v["bias"] for k, v in self.latest.get("currencies", {}).items()}}

    # ---------- news trading ----------
    async def fetch_event_headlines(self, event: dict) -> list:
        t_event = datetime.fromisoformat(event["time"])
        q = quote_plus(f"{COUNTRY_EN.get(event['country'], event['country'])} {event['title']} when:3h")
        google, live = await asyncio.gather(self._feed("Google News", GOOGLE_NEWS.format(q=q)), self._feed("ForexLive", FEEDS[0][1]))
        words = set(_words(event["title"]))
        out = []
        for h in google + live:
            try:
                pub = datetime.fromisoformat(h["published"])
            except Exception:
                continue
            if pub < t_event - timedelta(minutes=3):
                continue
            text = f"{h['title']} {h['summary']}".lower()
            if h["source"] != "Google News" and not (words & set(re.findall(r"[a-z]{3,}", text))):
                continue
            out.append({**h, "age_min": _age_min(h["published"]), "has_number": bool(re.search(r"\d", h["title"]))})
        out.sort(key=lambda h: (not h["has_number"], h["age_min"]))
        return out[:25]

    async def news_trading_tick(self):
        if not self.latest:
            return
        now = datetime.now(timezone.utc)
        settings = await self.engine.get_settings()
        for e in self.latest.get("events", []):
            if e["impact"] != "ALTO" or e["key"] in self._processed:
                continue
            age = (now - datetime.fromisoformat(e["time"])).total_seconds() / 60
            if age < 2 or age > self.cfg.news_trade_window_min:
                continue
            candidates = pairs_for_currency(e["country"], settings.symbols) + ([s for s in ("XAUUSD", "XAGUSD") if s in settings.symbols] if e["country"] == "USD" else [])
            candidates = list(dict.fromkeys(candidates))
            if not candidates:
                self._processed.add(e["key"])
                continue
            headlines = await self.fetch_event_headlines(e)
            if not any(h["has_number"] for h in headlines) and age < 6:
                continue
            self._processed.add(e["key"])
            await self.process_event(e, headlines, candidates)

    async def process_event(self, e: dict, headlines: list, candidates: list) -> dict:
        ctx = {c: v["bias"] for c, v in (self.latest or {}).get("currencies", {}).items()} if self.latest else None
        try:
            res = await fx_ai.assess_news_event(e, headlines, candidates, ctx, self.engine.profile.ai_kind)
        except Exception as ex:
            logger.exception("news trade assess")
            res = {"actual": None, "forecast": e.get("forecast", ""), "previous": e.get("previous", ""), "surprise": "DESCONOCIDA",
                   "summary": f"Error IA: {str(ex)[:120]}", "trades": []}
        doc = {"key": e["key"], "event": e, "at": now_iso(), "headlines": len(headlines), "candidates": candidates, **res, "results": []}
        executed = 0
        for t in res["trades"]:
            if executed >= self.cfg.news_trade_max_per_event:
                break
            if t["confidence"] < self.cfg.news_trade_min_confidence:
                doc["results"].append({**t, "status": "OMITIDA", "reason": f"confianza {t['confidence']}% < {self.cfg.news_trade_min_confidence}%"})
                continue
            r = await self.engine.news_trade(t["symbol"], t["side"], t["confidence"], t["reasoning"], {**e, "actual": res["actual"], "surprise": res["surprise"]}, self.cfg)
            doc["results"].append({**t, **r})
            executed += r["status"] == "EJECUTADA"
        doc["executed"] = executed
        await self.db.news_trades.insert_one(doc)
        doc.pop("_id", None)
        head = f"{e['country']} {e['title']}: dato {res['actual'] or 'n/d'} vs previsión {res['forecast'] or 'n/d'} · sorpresa {res['surprise']}"
        if executed:
            ops = ", ".join(f"{r['side'].upper()} {r['symbol']} ({r['confidence']}%)" for r in doc["results"] if r.get("status") == "EJECUTADA")
            await self.engine.log("NEWS", f"Operación de noticias · {head} → {ops}")
            await self.engine.tg(f"📰⚡ <b>Operación de noticias</b>\n{head}\n{res['summary']}\n→ <b>{ops}</b>", "news")
        else:
            why = "; ".join(f"{r['symbol']}: {r.get('reason', '')}" for r in doc["results"]) or "sin propuesta de la IA"
            await self.engine.log("NEWS", f"Noticia evaluada sin operar · {head} · {why}")
        return doc

    async def aclose(self):
        if self.task:
            self.task.cancel()
        await self.client.aclose()
