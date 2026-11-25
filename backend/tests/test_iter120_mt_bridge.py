"""Iter120: own MetaTrader bridge. Backend adapter (fx/bridge.py), Windows bridge server (windows_bridge/mt_bridge.py) with its MT4 file
protocol answered by a fake Expert Advisor, the MT5 core with a fake MetaTrader5 module, and the whole chain FxLiveBroker → HTTP → bridge → EA."""
import os
import sys
import json
import time
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows_bridge"))
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
class FakeEA(threading.Thread):
    """Answers the request files like HFM_Bridge_EA.mq4 (server clock = UTC+2)."""
    MODES = {0: "DISABLED", 4: "FULL"}
    CURRENCY = "USD"
    SPECS = {"EURUSD": (5, 0), "EURUSDb": (5, 4), "GOLDb": (2, 4), "SILVERb": (3, 4), "SOLUSD": (2, 4), "XRPUSD.a": (4, 4)}

    def __init__(self, files_dir: Path):
        super().__init__(daemon=True)
        self.dir = files_dir / "aitb"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.orders, self.history, self.next_ticket = {}, [], 1000
        self.alive, self.mute, self.fail_open, self.seen = True, False, 0, []
        self.history.append({"ticket": 5, "symbol": "", "type": 6, "lots": 0, "open_price": 0, "close_price": 0, "open_time": NOW + 7200 - 60, "close_time": NOW + 7200 - 60,
                             "profit": 500.0, "swap": 0, "commission": 0, "comment": "Deposit", "magic": 0})

    def beat(self):
        s = {"login": 123456, "server": "HFMarketsGlobal-Demo", "company": "HF Markets", "name": "Test", "currency": self.CURRENCY, "connected": 1, "trade_allowed": 1, "balance": 1000.0,
             "equity": 1010.5, "margin": 20.0, "free_margin": 990.5, "leverage": 500, "server_time": int(time.time()) + 7200, "gmt_time": int(time.time()), "tick_age": 0}
        tmp = self.dir / "status.tmp"
        tmp.write_text(json.dumps(s))
        os.replace(tmp, self.dir / "status.json")

    def run(self):
        while self.alive:
            self.beat()
            for req in sorted(self.dir.glob("req_*.json")):
                if self.mute:
                    continue
                try:
                    j = json.loads(req.read_text())
                    req.unlink()
                except (OSError, ValueError):
                    continue
                self.seen.append(j)
                body = self.handle(j)
                if body is None:
                    continue
                tmp = self.dir / f"res_{j['id']}.tmp"
                tmp.write_text(json.dumps({"id": j["id"], **body}))
                os.replace(tmp, self.dir / f"res_{j['id']}.json")
            time.sleep(0.01)

    def handle(self, j: dict):
        c = j["cmd"]
        if c == "symbols":
            return {"ok": 1, "symbols": list(self.SPECS)}
        if c == "spec":
            if j["symbol"] not in self.SPECS:
                return {"ok": 0, "err": 4106}
            d, mode = self.SPECS[j["symbol"]]
            return {"ok": 1, "symbol": j["symbol"], "digits": d, "point": 10 ** -d, "min_lot": 0.01, "lot_step": 0.01, "max_lot": 50.0, "contract": 100000, "tick_size": 10 ** -d,
                    "tick_value": 1.0, "trade_mode": self.MODES[mode], "trade_allowed": 1 if mode else 0, "stops_level": 0, "bid": 1.1, "ask": 1.1002, "time": int(time.time()) + 7200}
        if c == "candles":
            base = (j["start"] or NOW + 7200) // 3600 * 3600
            return {"ok": 1, "rows": [[base - 3600 * (j["limit"] - 1 - i), 1.1, 1.2, 1.0, 1.15, 100 + i] for i in range(j["limit"])]}
        if c == "positions":
            return {"ok": 1, "positions": [{**o, "price": 1.1} for o in self.orders.values()]}
        if c == "history":
            return {"ok": 1, "orders": [o for o in self.history if j["from"] <= o["close_time"] <= j["to"]]}
        if c == "open":
            if self.fail_open:
                err, self.fail_open = self.fail_open, 0
                return None if err == -1 else {"ok": 0, "err": err}
            t = self.next_ticket = self.next_ticket + 1
            self.orders[t] = {"ticket": t, "symbol": j["symbol"], "type": j["side"], "lots": j["lots"], "open_price": 1.1002, "sl": j["sl"], "tp": j["tp"], "profit": 0.0, "swap": 0.0,
                              "commission": -0.07, "comment": j["comment"], "magic": j["magic"], "open_time": int(time.time()) + 7200}
            return {"ok": 1, "ticket": t, "price": 1.1002, "lots": j["lots"]}
        if c == "close":
            o = self.orders.pop(j["ticket"], None)
            if not o:
                return {"ok": 0, "err": 4108}
            lots, new = (j["lots"] or o["lots"]), 0
            if lots < o["lots"] - 1e-9:
                new = self.next_ticket = self.next_ticket + 1
                self.orders[new] = {**o, "ticket": new, "lots": round(o["lots"] - lots, 2), "comment": f"from #{o['ticket']}"}
            self.history.append({**o, "lots": lots, "close_price": 1.105, "close_time": int(time.time()) + 7200, "profit": round(lots * 480, 2), "swap": -0.1,
                                 "comment": o["comment"] + ("[sl]" if j.get("slippage") == 99 else "")})
            return {"ok": 1, "ticket": o["ticket"], "new_ticket": new, "price": 1.105, "lots": lots}
        if c == "modify":
            o = self.orders.get(j["ticket"])
            if not o:
                return {"ok": 0, "err": 4108}
            if o["sl"] == j["sl"] and (j["tp"] < 0 or o["tp"] == j["tp"]):
                return {"ok": 0, "err": 1}
            o["sl"], o["tp"] = j["sl"], (o["tp"] if j["tp"] < 0 else j["tp"])
            return {"ok": 1}
        return {"ok": 0, "err": 3}


@pytest.fixture
def stack(tmp_path):
    ea = FakeEA(tmp_path / "mt4files")
    ea.start()
    time.sleep(0.05)
    cfg = {"token": TOKEN, "allowed_ips": ["203.0.113.7"], "state_dir": str(tmp_path / "state"),
           "accounts": {"hfm_v1": {"platform": "mt4", "files_dir": str(tmp_path / "mt4files")}, "hfm_v2": {"platform": "mt4", "files_dir": str(tmp_path / "other")}}}
    drivers = mt_bridge.build_drivers(cfg)
    app = mt_bridge.create_app(cfg, drivers)
    yield SimpleNamespace(ea=ea, cfg=cfg, drivers=drivers, app=app, d=drivers["hfm_v1"],
                          api=lambda token=TOKEN, ip="203.0.113.7": MtBridge("http://bridge", token, transport=httpx.ASGITransport(app=app, client=(ip, 5000))))
    ea.alive = False


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- bridge server: security and routing
def test_bridge_requires_token_and_allowed_ip(stack):
    async def go():
        assert [a["_id"] for a in await stack.api().list_accounts()] == ["hfm_v1", "hfm_v2"]
        with pytest.raises(BridgeError) as e:
            await stack.api(token="x" * 32).list_accounts()
        assert e.value.status == 401
        with pytest.raises(BridgeError) as e:
            await stack.api(ip="198.51.100.9").list_accounts()
        assert e.value.status == 403
        with pytest.raises(BridgeError) as e:
            await stack.api().account_info("nope")
        assert e.value.status == 404 and "hfm_v1" in e.value.msg
    run(go())


def test_bridge_config_validation(tmp_path):
    p = tmp_path / "c.json"
    for bad in ({"token": "short", "allowed_ips": ["1.1.1.1"], "accounts": {}}, {"token": TOKEN, "accounts": {}},
                {"token": TOKEN, "allowed_ips": ["1.1.1.1"], "accounts": {"a": {"platform": "mt4"}}}):
        p.write_text(json.dumps(bad))
        with pytest.raises(SystemExit):
            mt_bridge.load_config(str(p))
    p.write_text(json.dumps({"token": TOKEN, "allowed_ips": ["1.1.1.1"], "accounts": {"a": {"platform": "mt5", "terminal_path": "C:/t.exe"}}}))
    assert mt_bridge.load_config(str(p))["accounts"]["a"]["platform"] == "mt5"


def test_accounts_are_independent_and_report_state(stack):
    async def go():
        accs = {a["_id"]: a for a in await stack.api().list_accounts()}
        assert (accs["hfm_v1"]["state"], accs["hfm_v1"]["connectionStatus"], accs["hfm_v1"]["login"]) == ("DEPLOYED", "CONNECTED", "123456")
        assert (accs["hfm_v2"]["state"], accs["hfm_v2"]["connectionStatus"]) == ("UNDEPLOYED", "DISCONNECTED") and "Expert Advisor" in accs["hfm_v2"]["detail"]
        info = await stack.api().account_info("hfm_v1")
        assert (info["balance"], info["equity"], info["freeMargin"], info["leverage"], info["currency"], info["tradeAllowed"]) == (1000.0, 1010.5, 990.5, 500, "USD", True)
        with pytest.raises(BridgeError) as e:
            await stack.api().account_info("hfm_v2")
        assert e.value.status == 503 and e.value.kind == "transient"
    run(go())


# ---------------------------------------------------------------- market data
def test_symbol_suffix_resolution_ticks_candles_and_lot_size(stack):
    async def go():
        api = stack.api()
        api.retries = 0
        assert (await api.spec("hfm_v1", "EURUSD"))["tradeMode"] == "SYMBOL_TRADE_MODE_DISABLED"  # exact name wins: the backend's own suffix detection decides
        gold = await api.spec("hfm_v1", "GOLD")
        assert gold["symbol"] == "GOLDb" and gold["digits"] == 2  # only a suffixed variant exists → resolved
        assert (await api.spec("hfm_v1", "SOLUSDT"))["symbol"] == "SOLUSD" and (await api.spec("hfm_v1", "XRPUSDT"))["symbol"] == "XRPUSD.a"
        tick = await api.tick("hfm_v1", "EURUSDb")
        assert tick["bid"] == 1.1 and tick["ask"] == 1.1002 and abs(tick["spread"] - 0.0002) < 1e-9
        with pytest.raises(BridgeError) as e:
            await api.spec("hfm_v1", "XXXJPY", max_age=0)
        assert e.value.status == 404
        rows = await api.candles("hfm_v1", "EURUSDb", "1h", None, 5)
        assert len(rows) == 5 and rows == sorted(rows) and rows[-1][1:] == [1.1, 1.2, 1.0, 1.15, 104.0]
        assert abs(rows[-1][0] / 1000 - NOW) < 3700  # server time (UTC+2) converted back to UTC
        lot = await api.lot_size("hfm_v1", "EURUSDb", 25.0, 0.0050)  # 500 points × 1 USD = 500 USD per lot
        assert lot["lots"] == 0.05 and lot["lossPerLot"] == 500.0 and lot["riskMoney"] == 25.0
        assert (await api.lot_size("hfm_v1", "EURUSDb", 1.0, 0.0050))["belowMinimum"] is True
    run(go())
    assert stack.d.offset() == 7200


# ---------------------------------------------------------------- trading through the whole chain
def test_live_broker_open_modify_partial_close_and_result(stack):
    async def go():
        b = FxLiveBroker("http://bridge", TOKEN, "hfm_v1", suffix="b")
        b.api = stack.api()
        r = await b.open("EURUSD", "long", 0.057, 1.09, 1.12)
        pid = r["position_id"]
        assert r["filled"] and r["lots"] == 0.05 and r["entry"] == 1.1002 and r["sl"] == 1.09 and r["tp"] == 1.12 and r["link_id"].startswith("AIT_")
        sent = next(j for j in stack.ea.seen if j["cmd"] == "open")
        assert sent["symbol"] == "EURUSDb" and sent["comment"] == r["link_id"] and sent["lots"] == 0.05 and sent["ts"] >= NOW
        await b.set_stop("EURUSD", pid, 1.1002, None)
        pos = await b.position(pid)
        assert pos["sl"] == 1.1002 and pos["tp"] == 1.12 and pos["client_id"] == r["link_id"] and b.our_symbol(pos["symbol"]) == "EURUSD"
        await b.set_stop("EURUSD", pid, 1.1002, None)  # MT4 error 1 (no changes) is a success
        await b.close_partial(pid, 0.02, r["link_id"])
        pos = await b.position(pid)  # MT4 gave the remainder a new ticket: the position id and clientId stay the same
        assert pos and pos["volume"] == 0.03 and pos["client_id"] == r["link_id"] and str(stack.ea.next_ticket) != pid
        await b.set_stop("EURUSD", pid, 1.1010, None)
        assert stack.ea.orders[stack.ea.next_ticket]["sl"] == 1.101
        await b.close(pid, r["link_id"])
        assert await b.position(pid) is None
        res = await b.closed_result(pid)
        assert res["pnl"] == round(0.02 * 480 + 0.03 * 480 - 0.1 * 2 - 0.07 * 2, 4) and res["exit"] == 1.105
        with pytest.raises(BridgeError) as e:
            await b.close(pid)
        assert e.value.code == "TRADE_RETCODE_POSITION_CLOSED" and e.value.kind == "rejected"
        flows = await b.cash_flows((NOW - 3600) * 1000)
        assert [(f["amount"], f["note"]) for f in flows] == [(500.0, "Deposit")]
        await b.aclose()
    run(go())
    # the alias map survives a bridge restart
    again = mt_bridge.build_drivers(stack.cfg)["hfm_v1"]
    assert again.state["alias"] and again.state["offset"] == 7200


def test_trade_error_kinds(stack, monkeypatch):
    monkeypatch.setattr("fx.live.RETRY_DELAYS", (0.01, 0.01))

    async def go():
        b = FxLiveBroker("http://bridge", TOKEN, "hfm_v1", suffix="b")
        b.api = stack.api()
        stack.ea.fail_open = 134  # not enough money → definitive
        with pytest.raises(BridgeError) as e:
            await b.open("EURUSD", "long", 0.05, 1.09, 1.12)
        assert (e.value.code, e.value.kind) == ("ERR_NOT_ENOUGH_MONEY", "rejected") and "margen insuficiente" in e.value.msg
        stack.ea.fail_open = 146  # trade context busy → retried and filled
        r = await b.open("EURUSD", "long", 0.05, 1.09, 1.12)
        assert r["filled"] and r["attempts"] == 2
        with pytest.raises(ValueError):
            await b.open("EURUSD", "long", 0.004, 1.09, 1.12)
        res = await b.api.trade("hfm_v1", {"actionType": "POSITION_MODIFY", "positionId": r["position_id"], "stopLoss": 1.095})
        assert res["stringCode"] == "ERR_NO_ERROR"
        await b.aclose()
    run(go())


def test_ea_silence_is_unknown_only_when_the_request_was_taken(stack, monkeypatch):
    real = stack.d.request
    monkeypatch.setattr(stack.d, "request", lambda cmd, timeout=10.0, **kw: real(cmd, 0.4, **kw))
    stack.ea.fail_open = -1  # the EA reads the order and never answers → result unknown
    res = stack.d.trade({"actionType": "ORDER_TYPE_BUY", "symbol": "EURUSDb", "volume": 0.05, "stopLoss": 1.09, "clientId": "AIT_aaaaaaaa_o"})
    assert res["stringCode"] == "ERR_TRADE_TIMEOUT"
    stack.ea.mute = True  # the EA never takes the file → surely not executed, and the request file is removed so it can never run late
    res = stack.d.trade({"actionType": "ORDER_TYPE_BUY", "symbol": "EURUSDb", "volume": 0.05, "stopLoss": 1.09, "clientId": "AIT_bbbbbbbb_o"})
    assert res["stringCode"] == "ERR_NO_CONNECTION" and not list(stack.d.dir.glob("req_*"))
    stack.ea.alive = False
    time.sleep(0.05)
    os.utime(stack.d.dir / "status.json", (time.time() - 30, time.time() - 30))  # stale heartbeat: nothing is even written
    assert stack.d.trade({"actionType": "ORDER_TYPE_BUY", "symbol": "EURUSDb", "volume": 0.05, "stopLoss": 1.09})["stringCode"] == "ERR_NO_CONNECTION"
    assert stack.d.status()["state"] == "UNDEPLOYED"


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


def test_mt4_trade_mode_and_gold_silver_aliases(stack):
    full, dis = mt_bridge.MODE_FULL, "SYMBOL_TRADE_MODE_DISABLED"
    m = mt_bridge.mt4_trade_mode
    assert m("FULL") == full and m("CLOSEONLY") == "SYMBOL_TRADE_MODE_CLOSEONLY" and m("DISABLED", 0) == dis
    assert m(2) == full and m("2") == full and m(1) == "SYMBOL_TRADE_MODE_CLOSEONLY" and m(0, 0) == dis  # older EA: MT4 numbering, FULL = 2 (was read as «solo ventas»)
    assert m(0, 1) == full and m("CLOSEONLY", 1) == "SYMBOL_TRADE_MODE_CLOSEONLY"
    stack.d.cfg["symbol_suffix"] = "b"

    async def go():
        api = MtBridge("http://bridge", TOKEN)
        api.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stack.app), base_url="http://bridge", headers={"Authorization": f"Bearer {TOKEN}"})
        got = {s: (await api.spec("hfm_v1", s))["symbol"] for s in ("XAUUSD", "XAUUSDb", "XAGUSD", "XAGUSDb", "GOLDb", "EURUSDb")}
        assert got == {"XAUUSD": "GOLDb", "XAUUSDb": "GOLDb", "XAGUSD": "SILVERb", "XAGUSDb": "SILVERb", "GOLDb": "GOLDb", "EURUSDb": "EURUSDb"}
        assert (await api.spec("hfm_v1", "EURUSDb"))["tradeMode"] == full and (await api.spec("hfm_v1", "EURUSD"))["tradeMode"] == dis
        from fx.live import FxLiveBroker
        b = FxLiveBroker("http://bridge", TOKEN, "hfm_v1", "b")
        assert (b.broker_symbol("XAUUSD"), b.broker_symbol("XAGUSD"), b.broker_symbol("EURUSD")) == ("GOLDb", "SILVERb", "EURUSDb")
        assert (b.our_symbol("EURUSDb"), b.our_symbol("GOLDb"), b.our_symbol("SILVERb")) == ("EURUSD", "XAUUSD", "XAGUSD")
        b.symbol_map = {"XAUUSD": "XAUUSD.b"}
        assert b.broker_symbol("XAUUSD") == "XAUUSD.b"
        prem = FxLiveBroker("http://bridge", TOKEN, "hfm_v1", "")
        assert prem.broker_symbol("XAUUSD") == "XAUUSD"
        await api.aclose(); await b.aclose(); await prem.aclose()
    run(go())


def test_no_request_files_pile_up(tmp_path):
    """Iter126: EA down → immediate error and nothing written; timeouts leave no file; startup sweeps leftovers older than 60 s."""
    import os
    import time as _t
    d = tmp_path / "aitb"
    d.mkdir()
    old, fresh = d / "req_old.json", d / "res_fresh.json"
    old.write_text("{}"); fresh.write_text("{}"); (d / "res_old.json").write_text("{}"); (d / "status.json").write_text("{}")
    for f in (old, d / "res_old.json", d / "status.json"):
        os.utime(f, (_t.time() - 120, _t.time() - 120))
    drv = mt_bridge.Mt4Driver("hfm_v1", {"platform": "mt4", "files_dir": str(tmp_path)}, tmp_path / "state")
    assert sorted(os.listdir(d)) == ["res_fresh.json", "status.json"]  # stale req_/res_ swept at startup, status.json untouched
    t0 = _t.time()
    for _ in range(50):
        with pytest.raises(mt_bridge.Fault) as e:
            drv.request("positions", timeout=5)
        assert e.value.status == 503 and "no está activo" in e.value.detail
    assert _t.time() - t0 < 2 and sorted(os.listdir(d)) == ["res_fresh.json", "status.json"]  # heartbeat 120 s old: nothing written
    (d / "status.json").unlink()
    with pytest.raises(mt_bridge.Fault) as e:
        drv.request("positions", timeout=5)
    assert e.value.status == 503
    (d / "status.json").write_text("{}")  # EA «alive» but never answering
    for _ in range(3):
        with pytest.raises(mt_bridge.Fault) as e:
            drv.request("positions", timeout=0.2)
        assert e.value.status == 504 and e.value.picked is False
    assert sorted(os.listdir(d)) == ["res_fresh.json", "status.json"]
