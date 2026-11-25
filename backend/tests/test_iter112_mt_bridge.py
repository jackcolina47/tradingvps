"""Iter112 — direct MetaTrader bridge (replaces MetaApi): Bearer client routed by account id, HFM Zero aliases (GOLDb / SILVERb),
tradeMode FULL / tradeAllowed, partial close through POSITION_CLOSE_ID + volume, config defaults + legacy migration.
The client talks in-process to scripts/mt_bridge_mock.py (same REST contract as the Windows bridge)."""
import sys
import glob
import httpx
import pytest

sys.path.insert(0, "/app/scripts")
import mt_bridge_mock as mock  # noqa: E402
from fx import bridge  # noqa: E402
from fx.bridge import MtBridge, BridgeError, tradable, account_state  # noqa: E402
from fx.live import FxLiveBroker  # noqa: E402
from fx.broker import HFM, PEPPERSTONE  # noqa: E402
from fx.config import FxConfig, FxConfigStore, FxConfigUpdate, bridge_conn, LEGACY_KEYS  # noqa: E402
from fx.symbols import SPECS  # noqa: E402

URL = "http://bridge.test"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    mock.reset()
    mock.state["prices"].update({s: {"XAUUSD": 2650.0, "XAGUSD": 31.0}.get(s, 150.0 if s.endswith("JPY") else 1.1) for s in SPECS})
    monkeypatch.delenv("MT_BRIDGE_URL", raising=False)
    monkeypatch.delenv("MT_BRIDGE_TOKEN", raising=False)


def _api(token=mock.TOKEN):
    return MtBridge(URL, token, transport=httpx.ASGITransport(app=mock.app))


def _broker(profile=HFM, suffix="b", account="hfm_v2", symbol_map=None):
    return FxLiveBroker(URL, mock.TOKEN, account, suffix, symbol_map, profile.suffixes, profile.symbol_aliases, transport=httpx.ASGITransport(app=mock.app))


# ---------- client: auth + routing ----------
class TestClient:
    @pytest.mark.asyncio
    async def test_bearer_token_and_account_routing(self):
        api = _api()
        acc = await api.account("hfm_v2")
        assert acc["state"] == "DEPLOYED" and acc["connection"] == "CONNECTED" and acc["trade_allowed"] is True
        info = await api.account_info("hfm_v2")
        assert {"balance", "equity", "margin", "freeMargin", "leverage", "currency", "server", "name"} <= set(info)
        assert "GET /users/current/accounts/hfm_v2/account-information" in mock.state["requests"]
        assert (await api.health())["accounts"]["hfm_v2"]["state"] == "DEPLOYED"

    @pytest.mark.asyncio
    async def test_wrong_token_is_401(self):
        with pytest.raises(BridgeError) as e:
            await _api("nope").account("hfm_v2")
        assert e.value.status == 401 and "Token del Puente" in e.value.msg

    @pytest.mark.asyncio
    async def test_unknown_account_is_404_with_bridge_detail(self):
        with pytest.raises(BridgeError) as e:
            await _api().account("otra")
        assert e.value.status == 404 and "otra" in e.value.msg

    @pytest.mark.asyncio
    async def test_reconnect_and_candles(self):
        api = _api()
        mock.state["accounts"]["hfm_v2"].update(connectionStatus="DISCONNECTED")
        await api.reconnect("hfm_v2")
        assert (await api.account("hfm_v2"))["connection"] == "CONNECTED"
        rows = await api.candles("hfm_v2", "EURUSDb", "15m", limit=50)
        assert len(rows) == 50 and rows == sorted(rows) and len(rows[0]) == 6 and rows[0][5] > 0

    def test_timestamps_iso_or_epoch(self):
        assert bridge.ts_ms("2026-01-01T00:00:00Z") == bridge.ts_ms(1767225600) == bridge.ts_ms(1767225600000) == 1767225600000

    @pytest.mark.parametrize("raw,expected", [
        ({"state": "DEPLOYED", "connectionStatus": "CONNECTED", "tradeAllowed": True}, ("DEPLOYED", "CONNECTED", True)),
        ({"deployed": True, "connected": True, "trade_allowed": 1}, ("DEPLOYED", "CONNECTED", True)),
        ({"state": "DEPLOYED", "connected": False, "trade_allowed": False}, ("DEPLOYED", "DISCONNECTED", False)),
        ({}, ("", "", None))])
    def test_account_state_normalisation(self, raw, expected):
        a = account_state(raw)
        assert (a["state"], a["connection"], a["trade_allowed"]) == expected

    def test_env_overrides(self, monkeypatch):
        assert bridge.env_url() == "http://94.72.105.219:8000" and bridge.env_token() == ""
        monkeypatch.setenv("MT_BRIDGE_URL", "http://10.0.0.1:9000/")
        monkeypatch.setenv("MT_BRIDGE_TOKEN", "tok")
        assert bridge.env_url() == "http://10.0.0.1:9000" and bridge.env_token() == "tok"


# ---------- tradability ----------
class TestTradable:
    @pytest.mark.parametrize("spec,ok", [
        ({"tradeMode": "SYMBOL_TRADE_MODE_FULL"}, True), ({"tradeAllowed": True}, True), ({"tradeAllowed": 1}, True), ({"allowed": True}, True),
        ({"tradeMode": "SYMBOL_TRADE_MODE_DISABLED", "tradeAllowed": True}, True), ({"tradeMode": "SYMBOL_TRADE_MODE_FULL", "tradeAllowed": False}, True),
        ({}, True), ({"tradeMode": "SYMBOL_TRADE_MODE_DISABLED"}, False), ({"tradeMode": "SYMBOL_TRADE_MODE_CLOSEONLY"}, False),
        ({"tradeAllowed": False}, False), ({"tradeAllowed": 0, "allowed": False}, False)])
    def test_matrix(self, spec, ok):
        assert tradable(spec) is ok


# ---------- HFM Zero aliases ----------
class TestAliases:
    def test_profiles(self):
        assert HFM.bridge_account == "hfm_v2" and HFM.default_suffix == "b" and HFM.symbol_aliases == {"b": {"XAUUSD": "GOLD", "XAGUSD": "SILVER"}}
        assert PEPPERSTONE.bridge_account == "pepperstone_v2" and PEPPERSTONE.symbol_aliases == {} and PEPPERSTONE.default_suffix == ""

    def test_broker_symbol_mapping(self):
        b = _broker()
        assert [b.broker_symbol(s) for s in ("XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY")] == ["GOLDb", "SILVERb", "EURUSDb", "GBPUSDb", "USDJPYb"]
        assert [b.our_symbol(s) for s in ("GOLDb", "SILVERb", "EURUSDb")] == ["XAUUSD", "XAGUSD", "EURUSD"]

    def test_alias_only_on_zero_suffix(self):
        assert _broker(suffix="").broker_symbol("XAUUSD") == "XAUUSD" and _broker(suffix="r").broker_symbol("XAUUSD") == "XAUUSDr"
        assert _broker(PEPPERSTONE, "", "pepperstone_v2").broker_symbol("XAUUSD") == "XAUUSD"

    def test_saved_symbol_map_wins(self):
        assert _broker(symbol_map={"XAUUSD": "XAUUSD.b"}).broker_symbol("XAUUSD") == "XAUUSD.b"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ours,theirs", [("XAUUSD", "GOLDb"), ("XAGUSD", "SILVERb"), ("EURUSD", "EURUSDb")])
    async def test_spec_tick_candles_use_broker_name(self, ours, theirs):
        b = _broker()
        row = await b.check_symbol(ours)
        assert row["ok"] and row["quoted"] and row["broker_symbol"] == theirs and row["trade_mode"] == "operable" and not row["resolved"]
        assert len(await b.candles(ours, "15m", limit=20)) == 20
        base = "/users/current/accounts/hfm_v2"
        for path in (f"{base}/symbols/{theirs}/specification", f"{base}/symbols/{theirs}/current-tick", f"{base}/historical-market-data/symbols/{theirs}/timeframes/15m/candles"):
            assert f"GET {path}" in mock.state["requests"], path
        assert not any("XAUUSDb" in r or "XAGUSDb" in r for r in mock.state["requests"])

    @pytest.mark.asyncio
    async def test_wrong_suffix_quotes_but_is_not_tradable(self):
        row = await _broker(suffix="r").check_symbol("EURUSD")
        assert row["quoted"] and not row["ok"] and "NO es operable" in row["error"]

    @pytest.mark.asyncio
    async def test_detect_suffix_finds_zero(self):
        det = await _broker(suffix="").detect_suffix()
        assert det["suffix"] == "b" and det["account_type"] == "Zero" and det["matches"] is False


# ---------- trading ----------
class TestTrading:
    @pytest.mark.asyncio
    async def test_open_gold_modify_partial_and_close(self):
        b = _broker()
        r = await b.open("XAUUSD", "long", 0.10, 2630.0, 2700.0)
        pos = mock.state["positions"][r["position_id"]]
        assert pos["symbol"] == "GOLDb" and pos["volume"] == 0.10 and pos["comment"] == r["link_id"] and r["filled"] and r["attempts"] == 1
        live = (await b.positions(max_age=0))[0]
        assert live["client_id"] == r["link_id"] and live["side"] == "long" and b.our_symbol(live["symbol"]) == "XAUUSD"

        await b.set_stop("XAUUSD", r["position_id"], 2640.0, 2700.0)
        assert mock.state["positions"][r["position_id"]]["stopLoss"] == 2640.0

        part = await b.close_partial(r["position_id"], 0.05, r["link_id"])
        assert part["position_id"] is None and mock.state["positions"][r["position_id"]]["volume"] == 0.05

        await b.close(r["position_id"], r["link_id"])
        assert mock.state["positions"] == {}
        res = await b.closed_result(r["position_id"])
        assert res and res["exit"] > 0 and "pnl" in res

    @pytest.mark.asyncio
    async def test_partial_close_is_close_id_with_volume(self, monkeypatch):
        b, sent = _broker(), []
        real = b.api.trade

        async def spy(account_id, payload):
            sent.append((account_id, dict(payload)))
            return await real(account_id, payload)
        monkeypatch.setattr(b.api, "trade", spy)
        r = await b.open("EURUSD", "short", 0.20, 1.11, 1.08)
        await b.close_partial(r["position_id"], 0.10, r["link_id"])
        await b.close(r["position_id"], r["link_id"])
        o, p, c = (x[1] for x in sent)
        assert all(x[0] == "hfm_v2" for x in sent)
        assert o["actionType"] == "ORDER_TYPE_SELL" and o["symbol"] == "EURUSDb" and o["volume"] == 0.20 and o["stopLoss"] == 1.11 and o["takeProfit"] == 1.08 and o["comment"] == r["link_id"]
        assert p["actionType"] == "POSITION_CLOSE_ID" and p["positionId"] == r["position_id"] and p["volume"] == 0.10
        assert c["actionType"] == "POSITION_CLOSE_ID" and "volume" not in c

    @pytest.mark.asyncio
    async def test_mt4_partial_reticket_is_followed(self):
        mock.state["mt4_partial"] = True
        b = _broker()
        r = await b.open("EURUSD", "long", 0.20, 1.09, 1.12)
        part = await b.close_partial(r["position_id"], 0.10, r["link_id"])
        assert part["position_id"] and part["position_id"] != r["position_id"]
        assert mock.state["positions"][part["position_id"]]["volume"] == 0.10

    @pytest.mark.asyncio
    async def test_unknown_result_is_reconciled_by_comment(self):
        mock.state["faults"].update(trade_times=1, trade_code="ERR_TRADE_TIMED_OUT", trade_execute=True)
        b = _broker()
        r = await b.open("EURUSD", "long", 0.10, 1.09, 1.12)
        assert r["position_id"] in mock.state["positions"] and "confirmada" in r["note"] and len(mock.state["positions"]) == 1

    @pytest.mark.asyncio
    async def test_rejection_propagates(self):
        with pytest.raises(BridgeError) as e:
            await _broker().open("EURUSD", "long", 0.10, 1.20, 1.30)   # stop above the entry of a long
        assert e.value.code == "TRADE_RETCODE_INVALID_STOPS" and e.value.kind == "rejected"

    @pytest.mark.asyncio
    async def test_response_without_retcode(self, monkeypatch):
        api = _api()

        async def call(*a, **k):
            return call.res
        monkeypatch.setattr(api, "_call", call)
        call.res = {"success": True, "positionId": "77"}
        assert (await api.trade("hfm_v2", {}))["positionId"] == "77"
        for bad in ({"success": False, "error": "Market closed"}, {}):
            call.res = bad
            with pytest.raises(BridgeError):
                await api.trade("hfm_v2", {})


# ---------- configuration ----------
class _Col:
    def __init__(self, doc=None):
        self.doc, self.deleted = doc, None

    async def find_one(self, q):
        return self.doc

    async def insert_one(self, d):
        self.doc = d

    async def update_one(self, q, u, upsert=False):
        self.doc = {**{k: v for k, v in (self.doc or {}).items() if k not in u.get("$unset", {})}, **u.get("$set", {})}

    async def delete_many(self, q):
        self.deleted = q
        return type("R", (), {"deleted_count": 2})()


class _DB(dict):
    def __getitem__(self, k):
        return self.setdefault(k, _Col())


class TestConfig:
    def test_effective_connection(self, monkeypatch):
        assert bridge_conn(FxConfig(), HFM) == ("http://94.72.105.219:8000", "", "hfm_v2")
        assert bridge_conn(FxConfig(), PEPPERSTONE)[2] == "pepperstone_v2"
        monkeypatch.setenv("MT_BRIDGE_TOKEN", "envtok")
        monkeypatch.setenv("MT_BRIDGE_URL", "http://env:8000")
        assert bridge_conn(FxConfig(), HFM) == ("http://env:8000", "envtok", "hfm_v2")
        assert bridge_conn(FxConfig(bridge_url="http://x:1/", bridge_token="t", bridge_account_id="acc"), HFM) == ("http://x:1", "t", "acc")

    def test_model_has_no_metaapi_fields(self):
        assert not any("metaapi" in f for f in list(FxConfig.model_fields) + list(FxConfigUpdate.model_fields))
        assert {"bridge_url", "bridge_token", "bridge_account_id"} <= set(FxConfig.model_fields) & set(FxConfigUpdate.model_fields)
        assert not {"mt_password", "mt_platform"} & set(FxConfig.model_fields)

    @pytest.mark.asyncio
    async def test_new_install_defaults(self):
        st = FxConfigStore(_DB(), HFM)
        await st.load()
        pub = st.public()
        assert st.cfg.trading_mode == "paper" and st.cfg.mt_symbol_suffix == "b" and pub["hfm_configured"] is False
        assert pub["bridge_defaults"] == {"url": "http://94.72.105.219:8000", "account_id": "hfm_v2"} and pub["bridge_effective"]["token_source"] == ""

    @pytest.mark.asyncio
    async def test_legacy_metaapi_config_goes_back_to_paper(self):
        db = _DB()
        db["fx_config"].doc = {"_id": "main", "trading_mode": "live", "live_since": "2026-01-01", "metaapi_token": "x", "metaapi_account_id": "abc", "mt_login": "123",
                               "mt_server": "HFMarketsGlobal-Live2", "mt_password": "x", "hfm_verified_at": "2026-01-01", "hfm_state": "DEPLOYED", "hfm_alert_after_sec": 240}
        st = FxConfigStore(db, HFM)
        await st.load()
        c = st.cfg
        assert c.trading_mode == "paper" and c.live_since is None and c.hfm_verified_at is None and c.hfm_state == "" and c.mt_server == "" and c.mt_symbol_suffix == "b"
        assert c.hfm_alert_after_sec == 240 and not any(k in db["fx_config"].doc for k in LEGACY_KEYS)
        assert db["fx_positions"].deleted == {"live": True}
        await st.load()   # idempotent: nothing left to migrate
        assert db["fx_config"].doc["trading_mode"] == "paper"

    @pytest.mark.asyncio
    async def test_patch_validation_and_reset(self):
        st = FxConfigStore(_DB(), HFM)
        await st.load()
        await st.update({"bridge_token": "tok", "hfm_verified_at": "2026-01-01", "hfm_state": "DEPLOYED", "hfm_symbols_bad": [], "mt_server": "S"})
        assert st.public()["bridge_token"] != "tok" and st.public()["hfm_configured"] and st.public()["bridge_effective"]["token_source"] == "panel"
        assert st.patch_from(FxConfigUpdate(bridge_token="••••")) == {}
        p = st.patch_from(FxConfigUpdate(bridge_account_id="hfm_v3"))
        assert p["hfm_verified_at"] is None and p["hfm_symbols_bad"] is None and p["mt_server"] == ""
        p = st.patch_from(FxConfigUpdate(bridge_token="nuevo"))
        assert p["hfm_verified_at"] is None and "hfm_symbols_bad" not in p
        for bad in (FxConfigUpdate(bridge_url="ftp://x"), FxConfigUpdate(bridge_account_id="con espacios")):
            with pytest.raises(ValueError):
                st.patch_from(bad)
        assert st.patch_from(FxConfigUpdate(bridge_url="http://1.2.3.4:8000/"))["bridge_url"] == "http://1.2.3.4:8000"


def test_metaapi_is_gone_from_the_codebase():
    import os
    assert not os.path.exists("/app/backend/fx/metaapi.py")
    for path in glob.glob("/app/backend/**/*.py", recursive=True) + glob.glob("/app/frontend/src/**/*.js*", recursive=True):
        if "/tests/" in path or path.endswith("fx/config.py"):
            continue
        assert "metaapi" not in open(path).read().lower(), path
