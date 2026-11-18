"""Daily counter of LLM calls per kind (db.ai_usage, one doc per UTC day) with an in-memory mirror for synchronous status reads."""
from datetime import datetime, timezone

BOT_KINDS = {
    "crypto": {"signals": ("signal", "signal-fast"), "news": ("news",), "news_trades": (), "reviews": ("advisor",)},
    "fx": {"signals": ("fx-signal", "fx-signal-fast"), "news": ("fx-news",), "news_trades": ("fx-news-trade",), "reviews": ("fx-advisor",)},
    "pepper": {"signals": ("pep-signal", "pep-signal-fast"), "news": ("pep-news",), "news_trades": ("pep-news-trade",), "reviews": ("pep-advisor",)},
}


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class AiUsage:
    def __init__(self, db):
        self.db = db
        self.day = today()
        self.calls: dict = {}
        self.skipped: dict = {}

    async def load(self):
        doc = await self.db.ai_usage.find_one({"_id": today()})
        self.day, self.calls, self.skipped = today(), dict((doc or {}).get("calls", {})), dict((doc or {}).get("skipped", {}))

    def _roll(self):
        if self.day != today():
            self.day, self.calls, self.skipped = today(), {}, {}

    async def record(self, kind: str, skipped: bool = False):
        self._roll()
        bucket = "skipped" if skipped else "calls"
        target = self.skipped if skipped else self.calls
        target[kind] = target.get(kind, 0) + 1
        await self.db.ai_usage.update_one({"_id": self.day}, {"$inc": {f"{bucket}.{kind}": 1}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)

    def snapshot(self, bot: str) -> dict:
        self._roll()
        groups = BOT_KINDS[bot]
        out = {g: sum(self.calls.get(k, 0) for k in kinds) for g, kinds in groups.items()}
        out["skipped_news"] = sum(self.skipped.get(k, 0) for k in groups["news"])
        out["total"] = sum(out[g] for g in groups)
        out["chat"] = self.calls.get("chat", 0) + self.calls.get("chat-summary", 0)
        out["total_all"] = sum(self.calls.values())
        out["date"] = self.day
        return out
