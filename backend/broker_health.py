"""Shared broker-connection health (HFM/puente MT in FX, Bybit in crypto): one alert per outage, reminder every 6 h, recovery notice."""
import time
from datetime import datetime, timezone

REMIND_SEC = 6 * 3600


def fresh_health() -> dict:
    return {"ok": None, "state": "", "connection": "", "api_error": None, "error": None, "down_since": None, "alerted_at": None, "checked_at": 0.0}


def fmt_duration(sec: float) -> str:
    sec = int(sec)
    if sec < 90:
        return f"{sec} s"
    if sec < 3600:
        return f"{sec // 60} min"
    return f"{sec // 3600} h {(sec % 3600) // 60:02d} min"


def summary(h: dict, alert_after_sec) -> dict:
    return {"ok": h["ok"] is not False, "state": h["state"], "connection": h["connection"], "error": h["error"],
            "down_since": datetime.fromtimestamp(h["down_since"], timezone.utc).isoformat() if h["down_since"] else None,
            "down_sec": int(time.time() - h["down_since"]) if h["down_since"] else 0, "alerted": bool(h["alerted_at"]),
            "alert_after_sec": alert_after_sec}


def transition(h: dict, ok: bool, error: str, alert_after_sec: int, now: float = None) -> tuple:
    """Advances the state machine. Returns (event, outage_sec): event is "up" (recovered after an alert), "down" (first alert),
    "remind" (still down after REMIND_SEC) or None."""
    now = now or time.time()
    h["ok"] = ok
    if ok:
        event, dur = ("up", now - h["down_since"]) if h["alerted_at"] else (None, 0.0)
        h.update(down_since=None, alerted_at=None, error=None)
        return event, dur
    h["error"] = error
    if h["down_since"] is None:
        h["down_since"] = now
        return None, 0.0
    dur = now - h["down_since"]
    if h["alerted_at"] is None and dur >= alert_after_sec:
        h["alerted_at"] = now
        return "down", dur
    if h["alerted_at"] is not None and now - h["alerted_at"] >= REMIND_SEC:
        h["alerted_at"] = now
        return "remind", dur
    return None, dur
