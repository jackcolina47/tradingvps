"""Iter120: own MetaTrader bridge. Backend adapter (fx/bridge.py), Windows bridge server (windows_bridge/mt_bridge.py) with its MT4 file
protocol answered by a fake Expert Advisor, the MT5 core with a fake MetaTrader5 module, and the whole chain FxLiveBroker → HTTP → bridge → EA."""
import os
import sys
import json
import time
import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows_bridge"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows_bridge" / "tests"))
os.environ.pop("MT_BRIDGE_URL", None)
os.environ.pop("MT_BRIDGE_TOKEN", None)

import mt_bridge  # noqa: E402
from fx.bridge import MtBridge, BridgeError, env_defaults  # noqa: E402
from fx.live import FxLiveBroker  # noqa: E402
from fx.config import FxConfig, FxConfigStore, FxConfigUpdate, bridge_settings  # noqa: E402
from fx.broker import HFM, PEPPERSTONE  # noqa: E402

TOKEN = "t" * 32
NOW = int(time.time())


# ---------------------------------------------------------------- fake MT4 Expert Advisor


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- bridge server: security and routing


# ---------------------------------------------------------------- market data


# ---------------------------------------------------------------- trading through the whole chain


# ---------------------------------------------------------------- adapter against HTTP failures
def test_adapter_network_failures():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path.endswith("/trade"):
            raise httpx.ReadTimeout("slow", request=req)
        if req.url.path.endswith("/positions"):
            raise httpx.ConnectError("refused", request=req)
        return httpx.Response(504, json={"detail": "EA mudo"})

    async def go():
        api = MtBridge("http://94.72.105.219:8000/", TOKEN, transport=httpx.MockTransport(handler))
        api.retries = 0
        with pytest.raises(BridgeError) as e:
            await api.market_order("pepperstone_v1", "EURUSD", "short", 0.1, 1.2, 1.1, "AIT_cccccccc_o")
        assert e.value.kind == "unknown"  # sent, no answer: may have executed
        body = json.loads(calls[-1].content)
        assert calls[-1].headers["authorization"] == f"Bearer {TOKEN}" and str(calls[-1].url) == "http://94.72.105.219:8000/users/current/accounts/pepperstone_v1/trade"
        assert body == {"actionType": "ORDER_TYPE_SELL", "symbol": "EURUSD", "volume": 0.1, "stopLoss": 1.2, "takeProfit": 1.1, "clientId": "AIT_cccccccc_o", "magic": 777001}
        with pytest.raises(BridgeError) as e:
            await api.positions("pepperstone_v1")
        assert e.value.kind == "transient"
        with pytest.raises(BridgeError) as e:
            await api.account_info("pepperstone_v1")
        assert e.value.status == 504 and e.value.kind == "transient" and "EA mudo" in e.value.msg
        await api.aclose()
    run(go())

    async def refused():
        def h(req):
            raise httpx.ConnectError("refused", request=req)
        api = MtBridge("http://x", TOKEN, transport=httpx.MockTransport(h))
        with pytest.raises(BridgeError) as e:
            await api.close_position("hfm_v1", "1")
        assert e.value.kind == "transient"  # never reached the bridge → safe to retry
    run(refused())


# ---------------------------------------------------------------- backend configuration
def test_bridge_settings_panel_env_and_defaults(monkeypatch):
    assert bridge_settings(FxConfig(), HFM)["account_id"] == "hfm_v1" and bridge_settings(FxConfig(), PEPPERSTONE)["account_id"] == "pepperstone_v1"
    assert not bridge_settings(FxConfig(), HFM)["url"]
    monkeypatch.setenv("MT_BRIDGE_URL", "http://94.72.105.219:8000/")
    monkeypatch.setenv("MT_BRIDGE_TOKEN", TOKEN)
    monkeypatch.setenv("MT_BRIDGE_ACCOUNT_PEPPERSTONE", "pepperstone_v9")
    assert env_defaults("hfm", "hfm_v1") == {"url": "http://94.72.105.219:8000", "token": TOKEN, "account_id": "hfm_v1"}
    b = bridge_settings(FxConfig(), PEPPERSTONE)
    assert (b["url"], b["token"], b["account_id"]) == ("http://94.72.105.219:8000", TOKEN, "pepperstone_v9") and b["from_env"]["url"]
    b = bridge_settings(FxConfig(bridge_url="https://mi.puente", bridge_token="p" * 30, bridge_account_id="hfm_v2"), HFM)
    assert (b["url"], b["token"], b["account_id"]) == ("https://mi.puente", "p" * 30, "hfm_v2") and not b["from_env"]["url"]
    store = FxConfigStore(SimpleNamespace(__getitem__=lambda s, k: None) if False else {"fx_config": None}, HFM)
    store.cfg = FxConfig(bridge_token="p" * 30, hfm_verified_at="2026-01-01", hfm_state="DEPLOYED")
    pub = store.public()
    assert pub["hfm_configured"] and pub["bridge_token"].startswith("••") and "p" * 30 not in json.dumps(pub) and pub["bridge_default_account"] == "hfm_v1"
    assert not [k for k in pub if "metaapi" in k]
    patch = store.patch_from(FxConfigUpdate(bridge_account_id="hfm_v2"))
    assert patch["hfm_verified_at"] is None and patch["hfm_state"] == ""  # another account: verify again
    for bad in ({"bridge_url": "ftp://x"}, {"bridge_account_id": "hfm v1!"}):
        with pytest.raises(ValueError):
            store.patch_from(FxConfigUpdate(**bad))


# ---------------------------------------------------------------- MT5 core with a fake MetaTrader5 module
class FakeMt5:
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_GTC = 1, 6, 0, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self):
        self.pos, self.sent, self.retcode = {}, [], 10009

    def initialize(self, **kw):
        self.init = kw
        return True

    def last_error(self):
        return (1, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=True)

    def account_info(self):
        return SimpleNamespace(login=77, server="Pepperstone-Demo", company="Pepperstone", name="T", currency="USD", balance=5000.0, equity=5005.0, margin=1.0, margin_free=5004.0,
                               leverage=200, trade_allowed=True, trade_expert=True)

    def symbol_select(self, *a):
        return True

    def symbol_info(self, name):
        return SimpleNamespace(digits=2, point=0.01, volume_min=0.1, volume_step=0.1, volume_max=100.0, trade_contract_size=10, trade_tick_size=0.01, trade_tick_value=0.1, trade_mode=4,
                               trade_stops_level=0, filling_mode=2, bid=150.0, ask=150.1, time=NOW) if name == "SOLUSD" else None

    def symbol_info_tick(self, name):
        return SimpleNamespace(bid=150.0, ask=150.1, time=NOW, time_msc=NOW * 1000)

    def positions_get(self, ticket=None, symbol=None):
        rows = list(self.pos.values())
        return [p for p in rows if p.ticket == ticket] if ticket else rows

    def order_send(self, req):
        self.sent.append(req)
        if self.retcode != 10009:
            return SimpleNamespace(retcode=self.retcode, comment="rejected", order=0, deal=0, price=0, volume=0)
        if req["action"] == self.TRADE_ACTION_DEAL and "position" not in req:
            self.pos[900] = SimpleNamespace(ticket=900, symbol=req["symbol"], type=req["type"], volume=req["volume"], price_open=req["price"], price_current=req["price"], sl=req["sl"],
                                            tp=req["tp"], profit=0.0, swap=0.0, comment=req["comment"], magic=req["magic"], time=NOW)
        elif req["action"] == self.TRADE_ACTION_SLTP:
            self.pos[900].sl, self.pos[900].tp = req["sl"], req["tp"]
        else:
            self.pos[900].volume = round(self.pos[900].volume - req["volume"], 2)
            if self.pos[900].volume <= 0:
                del self.pos[900]
        return SimpleNamespace(retcode=10009, comment="done", order=900, deal=1, price=req.get("price", 0), volume=req.get("volume", 0))


def test_mt5_core_orders(tmp_path):
    fake = FakeMt5()
    cfg = {"platform": "mt5", "terminal_path": "C:/Pepperstone/terminal64.exe", "login": "77", "password": "pw", "server": "Pepperstone-Demo", "server_utc_offset_hours": 0}
    d = mt_bridge.Mt5Driver("pepperstone_v1", cfg, tmp_path, core=mt_bridge.Mt5Core(fake, cfg))
    fake.symbols_get = lambda: [SimpleNamespace(name="SOLUSD")]
    assert d.status()["connectionStatus"] == "CONNECTED" and fake.init == {"path": cfg["terminal_path"], "timeout": 60000, "login": 77, "password": "pw", "server": "Pepperstone-Demo"}
    assert d.account_info()["freeMargin"] == 5004.0
    r = d.trade({"actionType": "ORDER_TYPE_SELL", "symbol": "SOLUSD", "volume": 0.37, "stopLoss": 155.123, "clientId": "AIT_dddddddd_o", "magic": 777001})
    assert (r["stringCode"], r["positionId"]) == ("TRADE_RETCODE_DONE", "900")
    req = fake.sent[-1]
    assert (req["volume"], req["price"], req["sl"], req["tp"], req["type"], req["type_filling"], req["comment"]) == (0.3, 150.0, 155.12, 0.0, 1, 1, "AIT_dddddddd_o")
    p = d.positions()[0]
    assert (p["id"], p["type"], p["clientId"], p["stopLoss"], p["takeProfit"]) == ("900", "POSITION_TYPE_SELL", "AIT_dddddddd_o", 155.12, None)
    assert d.trade({"actionType": "POSITION_MODIFY", "positionId": "900", "stopLoss": 150.0})["stringCode"] == "TRADE_RETCODE_DONE" and fake.pos[900].sl == 150.0
    assert d.trade({"actionType": "POSITION_PARTIAL", "positionId": "900", "volume": 0.1})["stringCode"] == "TRADE_RETCODE_DONE" and fake.pos[900].volume == 0.2
    assert fake.sent[-1]["position"] == 900 and fake.sent[-1]["type"] == 0 and fake.sent[-1]["price"] == 150.1  # a short is closed buying at the ask
    fake.retcode = 10019
    assert d.trade({"actionType": "POSITION_CLOSE_ID", "positionId": "900"})["stringCode"] == "TRADE_RETCODE_NO_MONEY"
    fake.retcode = 10009
    assert d.trade({"actionType": "POSITION_CLOSE_ID", "positionId": "900"})["stringCode"] == "TRADE_RETCODE_DONE" and not fake.pos
    assert d.trade({"actionType": "POSITION_CLOSE_ID", "positionId": "900"})["stringCode"] == "TRADE_RETCODE_POSITION_CLOSED"
    assert d.trade({"actionType": "ORDER_TYPE_BUY", "symbol": "SOLUSD", "volume": 0.05, "stopLoss": 140})["stringCode"] == "TRADE_RETCODE_INVALID_VOLUME"
    with pytest.raises(mt_bridge.Fault) as e:
        d.spec("NOPEUSD")
    assert e.value.status == 404


def test_legacy_metaapi_link_must_be_verified_again():
    class Col:
        def __init__(self):
            self.doc = {"_id": "main", "trading_mode": "live", "metaapi_account_id": "abc", "metaapi_token": "x", "hfm_verified_at": "2026-01-01", "hfm_state": "DEPLOYED", "mt_symbol_suffix": "b"}

        async def find_one(self, q):
            return dict(self.doc)

        async def update_one(self, q, u, **kw):
            self.doc.update(u["$set"])
    col = Col()
    store = FxConfigStore({"fx_config": col}, HFM)
    run(store.load())
    assert store.cfg.hfm_verified_at is None and store.cfg.hfm_state == "" and store.cfg.trading_mode == "live" and store.cfg.mt_symbol_suffix == "b"
    assert col.doc["bridge_migrated"] is True and col.doc["metaapi_token"] == "x" and not store.bridge_ready()
    col.doc.update(hfm_verified_at="2026-02-02")  # verified later through the bridge: a restart must not reset it again
    run(store.load())
    assert store.cfg.hfm_verified_at == "2026-02-02"




# ---------------------------------------------------------------- backend adapter → bridge → MT5 terminal (HFM Demo without suffix, HFM Zero with «b»)
def test_live_broker_over_mt5_bridge_detects_suffix_and_trades(tmp_path):
    from test_mt_bridge import FakeTerminal, DEMO, ZERO
    terms = {"hfm_v1": FakeTerminal(101, "HF Markets", "USD", ZERO), "hfm_v2": FakeTerminal(102, "HF Markets", "USD", DEMO)}
    cfg = {"token": TOKEN, "allowed_ips": ["203.0.113.7"], "state_dir": str(tmp_path / "state"),
           "accounts": {k: {"platform": "mt5", "terminal_path": f"C:/{k}/terminal64.exe", "server_utc_offset_hours": 0} for k in terms}}
    app = mt_bridge.create_app(cfg, mt_bridge.build_drivers(cfg, core_factory=lambda acc, a: mt_bridge.Mt5Core(terms[acc], a)))
    api = lambda: MtBridge("http://bridge", TOKEN, transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 5000)))  # noqa: E731

    async def go():
        for acc, suffix, name in (("hfm_v1", "b", "EURUSDb"), ("hfm_v2", "", "EURUSD")):
            b = FxLiveBroker("http://bridge", TOKEN, acc)
            b.api = api()
            assert (await b.detect_suffix())["suffix"] == suffix  # the bridge answering EURUSD with EURUSDb must not hide the account's real suffix
            b.suffix = suffix
            r = await b.open("EURUSD", "long", 0.057, 1.09, 1.12)
            pid = r["position_id"]
            assert r["filled"] and r["lots"] == 0.05 and terms[acc].sent[-1]["symbol"] == name and terms[acc].sent[-1]["comment"] == r["link_id"]
            await b.set_stop("EURUSD", pid, 1.0951, None)
            await b.close_partial(pid, 0.02, r["link_id"])
            pos = await b.position(pid)
            assert pos["volume"] == 0.03 and pos["sl"] == 1.0951 and pos["client_id"] == r["link_id"] and b.our_symbol(pos["symbol"]) == "EURUSD"
            await b.close(pid, r["link_id"])
            assert await b.position(pid) is None
            with pytest.raises(BridgeError) as e:
                await b.close(pid)
            assert e.value.code == "TRADE_RETCODE_POSITION_CLOSED"
            await b.aclose()
    run(go())
