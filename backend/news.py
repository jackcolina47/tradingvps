import re
import time
import asyncio
import logging
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import httpx
from models import now_iso
from llm_json import parse_llm_json
from ai_advisor import _chat, _ask, record_usage
import notify

logger = logging.getLogger("news")
FEEDS = [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss"), ("CoinTelegraph", "https://cointelegraph.com/rss"),
         ("The Block", "https://www.theblock.co/rss.xml"), ("Decrypt", "https://decrypt.co/feed")]
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
ATOM = "{http://www.w3.org/2005/Atom}"
RISKS = ("BAJO", "MEDIO", "ALTO")
HISTORY_DAYS = 30
RETRY_AFTER_FAIL_SEC = 300  # after a failed analysis (invalid AI answer, feed down) wait before spending more AI calls
IDLE_INTERVAL_MIN = 60
ALERT_TTL_MIN = 24 * 60
COIN_WORDS = {"BTCUSDT": ["bitcoin", "btc"], "ETHUSDT": ["ethereum", "ether ", " eth"], "SOLUSDT": ["solana", " sol "], "XRPUSDT": ["xrp", "ripple"],
              "AVAXUSDT": ["avalanche", "avax"], "LINKUSDT": ["chainlink", " link "], "BNBUSDT": ["bnb", "binance"], "DOGEUSDT": ["doge"],
              "ADAUSDT": ["cardano", " ada "], "NEARUSDT": ["near protocol", " near "], "SUIUSDT": [" sui "], "LTCUSDT": ["litecoin", " ltc"]}
SYSTEM = (
    "Eres analista de noticias y riesgo de eventos para un bot cuantitativo de futuros perpetuos de criptomonedas. Recibes titulares recientes "
    "(con fuente y antigüedad) y el calendario macro de alto impacto. Tu salida alimenta la gestión de riesgo, no genera operaciones. Sé sobrio: "
    "la mayoría de los titulares son ruido; solo marca alertas ante hechos graves y verificables (hack/exploit, insolvencia o problemas de retiros "
    "en un exchange, demanda o sanción regulatoria relevante, delisting, fallo de red, decisión sobre ETF, liquidaciones masivas, giro de política "
    "monetaria). No inventes hechos que no estén en los titulares. Responde EXCLUSIVAMENTE con JSON válido: "
    '{"market":{"sentiment":entero -100..100,"risk":"BAJO"|"MEDIO"|"ALTO","summary":"2-3 frases en español con lo que importa hoy"},'
    '"pairs":{"SYMBOL":{"sentiment":entero -100..100,"risk":"BAJO"|"MEDIO"|"ALTO","note":"1 frase en español"}} solo para pares de la lista con noticia relevante,'
    '"alerts":[{"symbol":"SYMBOL o MARKET","severity":"ALTA"|"CRITICA","title":"título corto","why":"1 frase: por qué afecta al precio"}]}'
)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _when(item) -> str:
    raw = item.findtext("pubDate") or item.findtext(f"{ATOM}published") or item.findtext(f"{ATOM}updated") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
    try:
        dt = parsedate_to_datetime(raw) if raw and "," in raw else datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return now_iso()


def parse_feed(source: str, xml: str) -> list:
    root = ET.fromstring(xml)
    out = []
    items = list(root.iter("item")) or list(root.iter(f"{ATOM}entry"))
    for it in items:
        title = _clean(it.findtext("title") or it.findtext(f"{ATOM}title"))
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        if not link:
            el = it.find(f"{ATOM}link")
            link = el.get("href", "") if el is not None else ""
        out.append({"source": source, "title": title[:200], "link": link[:300], "published": _when(it),
                    "summary": _clean(it.findtext("description") or it.findtext(f"{ATOM}summary") or "")[:220]})
    return out


def _parse_json(text: str):
    return parse_llm_json(text)


def _age_min(iso: str) -> int:
    return int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60)


def _risk(v) -> str:
    r = str(v or "MEDIO").upper()
    return r if r in RISKS else "MEDIO"


def relevant(headline: dict, symbols: list) -> list:
    text = f" {headline['title']} {headline['summary']} ".lower()
    return [s for s in symbols if any(w in text for w in COIN_WORDS.get(s, [s.replace('USDT', '').lower()]))]


class NewsAgent:
    KIND = "news"

    def __init__(self, db, engine):
        self.db, self.engine = db, engine
        self.cfg = None
        self.latest = None
        self.task = None
        self._seen_alerts = set()
        self._last_risk = None
        self._retry_at = 0.0
        self.client = httpx.AsyncClient(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; CryptoBot/1.0)"})

    async def configure(self, cfg):
        self.cfg = cfg

    async def reload(self):
        doc = await self.db.news.find_one({"_id": "latest"}, {"_id": 0})
        self.latest = doc
        self._seen_alerts = {a["key"] for a in (doc or {}).get("alerts", [])}
        self._last_risk = (doc or {}).get("market", {}).get("risk")

    async def init(self):
        await self.reload()
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        await asyncio.sleep(15)
        while True:
            try:
                if self.cfg and self.cfg.news_enabled and time.time() >= self._retry_at:
                    if not self.latest or _age_min(self.latest["updated_at"]) >= self.interval_min():
                        await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("news loop")
                self._retry_at = time.time() + RETRY_AFTER_FAIL_SEC
                await self.engine.log("ERROR", f"Agente de noticias: {str(e)[:140]} · se mantiene el análisis anterior · nuevo intento en {RETRY_AFTER_FAIL_SEC // 60} min")
            await asyncio.sleep(60)

    def interval_min(self) -> int:
        """Configured cadence while the bot runs; with the bot stopped the agent slows down to IDLE_INTERVAL_MIN (resumes within a minute of starting)."""
        return self.cfg.news_interval_min if getattr(self.engine, "running", False) else max(self.cfg.news_interval_min, IDLE_INTERVAL_MIN)

    @staticmethod
    def fingerprint(headlines: list, events: list, symbols: list) -> str:
        keys = sorted(re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60] for h in headlines[:45])
        ev = sorted(f"{e['title']}|{e.get('country', '')}|{e['time']}|{e.get('forecast', '')}|{e.get('actual', '')}" for e in events)
        return hashlib.sha1("\n".join(keys + ev + sorted(symbols)).encode()).hexdigest()

    async def fetch_headlines(self) -> list:
        async def one(source, url):
            try:
                r = await self.client.get(url)
                r.raise_for_status()
                return parse_feed(source, r.text)
            except Exception as e:
                logger.warning(f"feed {source}: {e}")
                return []
        results = await asyncio.gather(*(one(s, u) for s, u in FEEDS))
        seen, out = set(), []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        for h in sorted((h for group in results for h in group), key=lambda x: x["published"], reverse=True):
            k = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
            if k in seen or h["published"] < cutoff:
                continue
            seen.add(k)
            out.append(h)
        return out[:60]

    async def fetch_events(self) -> list:
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
            if e.get("country") not in ("USD", "All") or e.get("impact") not in ("High", "Medium"):
                continue
            try:
                dt = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
            except Exception:
                continue
            if dt < now - timedelta(hours=3) or dt > now + timedelta(hours=72):
                continue
            out.append({"title": e["title"], "time": dt.isoformat(), "impact": "ALTO" if e["impact"] == "High" else "MEDIO",
                        "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
        return sorted(out, key=lambda x: x["time"])

    async def analyze(self, headlines: list, events: list, symbols: list) -> dict:
        lines = [f"- [{h['source']} · hace {_age_min(h['published'])} min] {h['title']}" + (f" — {h['summary'][:120]}" if h['summary'] else "") for h in headlines[:45]]
        ev = [f"- {e['time'][:16]} UTC · {e['title']} · impacto {e['impact']}" for e in events[:10]] or ["- ninguno en 72h"]
        prompt = (f"PARES ACTIVOS: {', '.join(symbols)}\n\nTITULARES (más recientes primero):\n" + "\n".join(lines) +
                  "\n\nCALENDARIO MACRO USD (UTC):\n" + "\n".join(ev) +
                  "\n\nDevuelve solo el JSON. Dentro de los textos no uses comillas dobles (usa comillas simples) ni saltos de línea.")
        chat = _chat(SYSTEM, "news")
        text = await _ask(chat, prompt)
        data = _parse_json(text)
        if data is None:
            text = await _ask(chat, "Tu respuesta anterior no era JSON válido. Repite la respuesta como un único objeto JSON válido, sin texto adicional, "
                                    "sin comillas dobles dentro de los textos.")
            data = _parse_json(text)
        if data is None:
            logger.warning(f"news: respuesta IA no válida ({len(text or '')} caracteres): {text!r}")
            raise ValueError(f"Respuesta IA no válida: {text[:160]}")
        market = data.get("market") or {}
        pairs = {k: v for k, v in (data.get("pairs") or {}).items() if k in symbols and isinstance(v, dict)}
        alerts = []
        for a in data.get("alerts") or []:
            if not isinstance(a, dict) or not a.get("title"):
                continue
            sym = str(a.get("symbol") or "MARKET").upper()
            alerts.append({"symbol": sym if sym in symbols or sym == "MARKET" else "MARKET", "severity": "CRITICA" if str(a.get("severity", "")).upper().startswith("CRIT") else "ALTA",
                           "title": str(a["title"])[:140], "why": str(a.get("why", ""))[:220],
                           "key": hashlib.sha1(str(a["title"]).lower().encode()).hexdigest()[:12], "at": now_iso()})
        risk = str(market.get("risk", "MEDIO")).upper()
        return {
            "market": {"sentiment": int(max(-100, min(100, int(market.get("sentiment", 0) or 0)))), "risk": _risk(risk),
                       "summary": str(market.get("summary", ""))[:600]},
            "pairs": {k: {"sentiment": int(max(-100, min(100, int(v.get("sentiment", 0) or 0)))), "risk": _risk(v.get("risk")), "note": str(v.get("note", ""))[:200]} for k, v in pairs.items()},
            "alerts": alerts,
        }

    async def refresh(self, force: bool = False) -> dict:
        settings = await self.engine.get_settings()
        headlines, events = await asyncio.gather(self.fetch_headlines(), self.fetch_events())
        for h in headlines:
            h["symbols"] = relevant(h, settings.symbols)
        fp = self.fingerprint(headlines, events, settings.symbols)
        if not force and self.latest and self.latest.get("fingerprint") == fp and self.latest.get("market"):
            # Nothing new since the last analysis → keep the AI verdict, refresh sources/timestamp only (no LLM call).
            doc = {**self.latest, "events": events, "headlines": headlines[:40], "updated_at": now_iso(), "skipped_ai": self.latest.get("skipped_ai", 0) + 1}
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
        doc = {**analysis, "events": events, "headlines": headlines[:40], "updated_at": now, "analysis_at": now, "fingerprint": fp, "skipped_ai": 0, "symbols": settings.symbols}
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
            await self.engine.log("NEWS", f"Riesgo de mercado por noticias: {risk} · sentimiento {doc['market']['sentiment']:+d} · {doc['market']['summary'][:140]}")
        for a in doc["alerts"]:
            if a["key"] in self._seen_alerts:
                continue
            self._seen_alerts.add(a["key"])
            await self.engine.log("NEWS", f"ALERTA {a['severity']} · {a['symbol']} · {a['title']}")
            await notify.notify(f"📰 <b>Alerta de noticias {a['severity']}</b> · {a['symbol']}\n<b>{a['title']}</b>\n{a['why']}", "news", bot="bybit")

    def _event_block(self):
        if not (self.cfg and self.cfg.news_block_events and self.latest):
            return None
        now = datetime.now(timezone.utc)
        for e in self.latest.get("events", []):
            if e["impact"] != "ALTO":
                continue
            t = datetime.fromisoformat(e["time"])
            if t - timedelta(minutes=self.cfg.news_event_before_min) <= now <= t + timedelta(minutes=self.cfg.news_event_after_min):
                return f"evento macro de alto impacto ({e['title']} · {t.strftime('%H:%M')} UTC)"
        return None

    def _active_critical(self) -> list:
        latest = self.latest or {}
        return [a for a in latest.get("alerts", []) if a["severity"] == "CRITICA" and _age_min(a.get("at") or latest["updated_at"]) <= ALERT_TTL_MIN]

    def gate(self, symbol: str):
        """Returns (block_reason | None, size_factor)."""
        if not (self.cfg and self.cfg.news_enabled and self.latest):
            return None, 1.0
        block = self._event_block()
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
        return {
            "actualizado_hace_min": _age_min(self.latest["updated_at"]),
            "mercado": self.latest["market"], "par": self.latest.get("pairs", {}).get(symbol),
            "alertas": [f"{a['severity']} {a['symbol']}: {a['title']}" for a in self.latest.get("alerts", [])[:4]],
            "eventos_proximos": [f"{e['title']} en {int((datetime.fromisoformat(e['time']) - now).total_seconds() / 60)} min ({e['impact']})"
                                 for e in self.latest.get("events", []) if datetime.fromisoformat(e["time"]) > now][:3],
            "titulares_del_par": [h["title"] for h in self.latest.get("headlines", []) if symbol in h.get("symbols", [])][:4],
        }

    def summary(self) -> dict:
        if not self.latest:
            return {"enabled": bool(self.cfg and self.cfg.news_enabled), "ready": False}
        now = datetime.now(timezone.utc)
        upcoming = [e for e in self.latest.get("events", []) if e["impact"] == "ALTO" and datetime.fromisoformat(e["time"]) > now]
        return {"enabled": bool(self.cfg and self.cfg.news_enabled), "ready": True, "risk": self.latest["market"]["risk"],
                "sentiment": self.latest["market"]["sentiment"], "updated_at": self.latest["updated_at"], "analysis_at": self.latest.get("analysis_at"),
                "interval_min": self.interval_min() if self.cfg else None, "blocked": self._event_block(),
                "critical": [a["symbol"] for a in self._active_critical()],
                "next_event": upcoming[0] if upcoming else None, "alerts": len(self.latest.get("alerts", []))}

    async def aclose(self):
        if self.task:
            self.task.cancel()
        await self.client.aclose()
