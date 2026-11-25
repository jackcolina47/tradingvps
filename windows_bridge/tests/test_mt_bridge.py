"""Windows bridge, MT5 only: three accounts (hfm_v1, hfm_v2, pepperstone_v1) served at the same time, each by its own terminal.
Run from the repo root:  python -m pytest windows_bridge/tests -q -o addopts=''
"""
import sys
import json
import time
import threading
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mt_bridge  # noqa: E402

TOKEN = "test-bridge-token-0123456789abcdef"
IP = "testclient"  # host that Starlette's TestClient reports as the caller
NOW = int(time.time())
ACTIVE = {"terminal": None}  # which terminal the (single) MetaTrader5 library connection of a process would be attached to


class FakeTerminal:
    """One MetaTrader 5 terminal as the MetaTrader5 module sees it after initialize(path=...)."""
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP, ORDER_TYPE_BUY, ORDER_TYPE_SELL, ORDER_TIME_GTC = 1, 6, 0, 1, 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    def __init__(self, login: int, company: str, currency: str, symbols: dict, delay: float = 0.0):
        self.login, self.company, self.currency, self.syms, self.delay = login, company, currency, symbols, delay
        self.pos, self.sent, self.inits, self.ticket, self.busy, self.overlap = {}, [], [], 1000 + login, 0, False

    def _enter(self):  # a real terminal is single-threaded: two calls at once would be a bug in the bridge
        self.busy += 1
        self.overlap = self.overlap or self.busy > 1
        time.sleep(self.delay)
        self.busy -= 1

    def initialize(self, **kw):
        self.inits.append(kw)
        return True

    def shutdown(self):
        return True

    def last_error(self):
        return (1, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=True, trade_allowed=True)

    def account_info(self):
        return SimpleNamespace(login=self.login, server=f"{self.company}-Server", company=self.company, name="T", currency=self.currency, balance=5000.0 + self.login, equity=5000.0 + self.login,
                               margin=0.0, margin_free=5000.0, leverage=200, trade_allowed=True, trade_expert=True)

    def symbols_get(self):
        return [SimpleNamespace(name=n) for n in self.syms]

    def symbol_select(self, *a):
        return True

    def symbol_info(self, name):
        self._enter()
        if name not in self.syms:
            return None
        digits, mode, bid = self.syms[name]
        return SimpleNamespace(digits=digits, point=10 ** -digits, volume_min=0.01, volume_step=0.01, volume_max=50.0, trade_contract_size=100000, trade_tick_size=10 ** -digits,
                               trade_tick_value=1.0, trade_mode=mode, trade_stops_level=0, filling_mode=2, bid=bid, ask=round(bid + 2 * 10 ** -digits, digits), time=NOW)

    def symbol_info_tick(self, name):
        i = self.symbol_info(name)
        return SimpleNamespace(bid=i.bid, ask=i.ask, time=NOW, time_msc=NOW * 1000) if i else None

    def positions_get(self, ticket=None, symbol=None):
        rows = list(self.pos.values())
        return [p for p in rows if p.ticket == ticket] if ticket else [p for p in rows if p.symbol == symbol] if symbol else rows

    def order_send(self, req):
        self._enter()
        self.sent.append(req)
        if req["action"] == self.TRADE_ACTION_DEAL and "position" not in req:
            self.ticket += 1
            self.pos[self.ticket] = SimpleNamespace(ticket=self.ticket, symbol=req["symbol"], type=req["type"], volume=req["volume"], price_open=req["price"], price_current=req["price"],
                                                    sl=req["sl"], tp=req["tp"], profit=0.0, swap=0.0, comment=req["comment"], magic=req["magic"], time=NOW)
            return SimpleNamespace(retcode=10009, comment="done", order=self.ticket, deal=1, price=req["price"], volume=req["volume"])
        p = self.pos[req["position"]]
        if req["action"] == self.TRADE_ACTION_SLTP:
            p.sl, p.tp = req["sl"], req["tp"]
        else:
            p.volume = round(p.volume - req["volume"], 2)
            if p.volume <= 0:
                del self.pos[p.ticket]
        return SimpleNamespace(retcode=10009, comment="done", order=p.ticket, deal=2, price=req.get("price", 0), volume=req.get("volume", 0))


FULL, DISABLED = 4, 0
DEMO = {"EURUSD": (5, FULL, 1.0950), "GBPUSD": (5, FULL, 1.2700), "XAUUSD": (2, FULL, 2400.0), "XAGUSD": (3, FULL, 30.0)}                       # HFM Demo Premium: no suffix
ZERO = {"EURUSD": (5, DISABLED, 1.0950), "EURUSDb": (5, FULL, 1.0951), "GBPUSDb": (5, FULL, 1.2701), "GOLDb": (2, FULL, 2401.0), "SILVERb": (3, FULL, 30.1)}  # HFM Real Zero: «b»
PEPPER = {"EURUSD": (5, FULL, 1.0949), "XAUUSD": (2, FULL, 2399.0), "XAGUSD": (3, FULL, 29.9), "SOLUSD": (2, FULL, 150.0)}
PATHS = {"hfm_v1": r"C:\MT5_HFM_1\terminal64.exe", "hfm_v2": r"C:\MT5_HFM_2\terminal64.exe", "pepperstone_v1": r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"}


@pytest.fixture
def stack(tmp_path):
    terms = {"hfm_v1": FakeTerminal(101, "HF Markets", "USD", DEMO, 0.02), "hfm_v2": FakeTerminal(102, "HF Markets", "USD", ZERO, 0.02), "pepperstone_v1": FakeTerminal(303, "Pepperstone", "GBP", PEPPER, 0.02)}
    cfg = {"token": TOKEN, "allowed_ips": [IP], "state_dir": str(tmp_path / "state"),
           "accounts": {k: {"platform": "mt5", "terminal_path": p, "login": terms[k].login, "server_utc_offset_hours": 0} for k, p in PATHS.items()}}
    drivers = mt_bridge.build_drivers(cfg, core_factory=lambda acc, a: mt_bridge.Mt5Core(terms[acc], a))
    http = TestClient(mt_bridge.create_app(cfg, drivers), headers={"Authorization": f"Bearer {TOKEN}"})
    return SimpleNamespace(terms=terms, cfg=cfg, drivers=drivers, http=http, base="/users/current/accounts")


def test_default_config_is_three_mt5_accounts(tmp_path, monkeypatch):
    monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"token": TOKEN, "allowed_ips": [IP]}))
    cfg = mt_bridge.load_config(str(p))
    assert {k: (a["platform"], a["terminal_path"]) for k, a in cfg["accounts"].items()} == {k: ("mt5", v) for k, v in PATHS.items()}
    example = json.loads((Path(mt_bridge.__file__).parent / "config_bridge.example.json").read_text("utf-8"))
    assert {k: (a["platform"], a["terminal_path"]) for k, a in example["accounts"].items()} == {k: ("mt5", v) for k, v in PATHS.items()}
    for bad, word in (({"a": {"platform": "mt4", "files_dir": "x"}}, "MetaTrader 5"), ({"a": {"platform": "mt5"}}, "terminal_path"),
                      ({"a": {"terminal_path": "C:/T/terminal64.exe"}, "b": {"terminal_path": "C:/T/terminal64.exe"}}, "mismo terminal")):
        p.write_text(json.dumps({"token": TOKEN, "allowed_ips": [IP], "accounts": bad}))
        with pytest.raises(SystemExit) as e:
            mt_bridge.load_config(str(p))
        assert word in str(e.value)
    assert not hasattr(mt_bridge, "Mt4Driver") and not (Path(mt_bridge.__file__).parent / "HFM_Bridge_EA.mq4").exists()


def test_token_and_ip_guard(stack):
    assert stack.http.get("/health").status_code == 200
    assert stack.http.get("/health", headers={"Authorization": "Bearer nope"}).status_code == 401
    other = TestClient(mt_bridge.create_app({**stack.cfg, "allowed_ips": ["198.51.100.9"]}, stack.drivers), headers={"Authorization": f"Bearer {TOKEN}"})
    assert other.get("/health").status_code == 403
    assert stack.http.get(f"{stack.base}/nope/positions").status_code == 404


def test_each_account_is_attached_to_its_own_terminal(stack):
    rows = {r["_id"]: r for r in stack.http.get("/health").json()["accounts"]}
    assert {k: (r["platform"], r["state"], r["connectionStatus"], r["login"]) for k, r in rows.items()} == {
        "hfm_v1": ("mt5", "DEPLOYED", "CONNECTED", "101"), "hfm_v2": ("mt5", "DEPLOYED", "CONNECTED", "102"), "pepperstone_v1": ("mt5", "DEPLOYED", "CONNECTED", "303")}
    for acc, t in stack.terms.items():  # one initialize per terminal, always with its own path: no terminal switching
        assert [i["path"] for i in t.inits] == [PATHS[acc]]
        info = stack.http.get(f"{stack.base}/{acc}/account-information").json()
        assert (info["login"], info["currency"], info["platform"]) == (str(t.login), t.currency, "mt5")


def test_wrong_login_in_terminal_blocks_the_account_only(stack):
    stack.terms["hfm_v2"].login = 999  # the terminal at that path is logged into another account
    assert stack.http.get(f"{stack.base}/hfm_v2").json()["state"] == "UNDEPLOYED"
    r = stack.http.post(f"{stack.base}/hfm_v2/trade", json={"actionType": "ORDER_TYPE_BUY", "symbol": "EURUSD", "volume": 0.1, "stopLoss": 1.09})
    assert r.json()["stringCode"] == "ERR_NO_CONNECTION" and not stack.terms["hfm_v2"].sent
    assert stack.http.get(f"{stack.base}/hfm_v1").json()["connectionStatus"] == "CONNECTED"


def test_symbols_resolve_with_and_without_suffix(stack):
    want = {"hfm_v1": {"EURUSD": "EURUSD", "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD", "EURUSDb": "EURUSD", "GOLDb": "XAUUSD", "SILVERb": "XAGUSD", "GOLD": "XAUUSD"},
            "hfm_v2": {"EURUSD": "EURUSDb", "GBPUSD": "GBPUSDb", "XAUUSD": "GOLDb", "XAGUSD": "SILVERb", "GOLD": "GOLDb", "EURUSDb": "EURUSDb", "XAUUSDb": "GOLDb"},
            "pepperstone_v1": {"EURUSD": "EURUSD", "XAUUSD": "XAUUSD", "SOLUSDT": "SOLUSD"}}
    for acc, table in want.items():
        for asked, real in table.items():
            spec = stack.http.get(f"{stack.base}/{acc}/symbols/{asked}/specification").json()
            assert (acc, asked, spec["symbol"], spec["tradeMode"]) == (acc, asked, real, "SYMBOL_TRADE_MODE_FULL")
    assert stack.http.get(f"{stack.base}/hfm_v2/symbols/XAUUSD/current-tick").json()["bid"] == 2401.0
    assert stack.http.get(f"{stack.base}/hfm_v1/symbols/NOPEUSD/specification").status_code == 404
    lot = stack.http.post(f"{stack.base}/hfm_v2/lot-size", json={"symbol": "EURUSD", "riskMoney": 50, "slDistance": 0.0025}).json()
    assert (lot["symbol"], lot["lots"]) == ("EURUSDb", 0.2)


def test_three_accounts_trade_at_the_same_time_without_interference(stack):
    def session(acc: str, n: int):
        out = []
        for i in range(n):
            r = stack.http.post(f"{stack.base}/{acc}/trade", json={"actionType": "ORDER_TYPE_BUY", "symbol": "EURUSD", "volume": 0.1, "stopLoss": 1.09, "clientId": f"{acc}_{i}"}).json()
            assert r["stringCode"] == "TRADE_RETCODE_DONE", r
            stack.http.get(f"{stack.base}/{acc}/symbols/XAUUSD/current-tick")
            out.append(r["positionId"])
        return acc, out
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=9) as pool:  # three callers per account, all at once
        done = list(pool.map(lambda a: session(*a), [(acc, 4) for acc in PATHS for _ in range(3)]))
    elapsed = time.time() - t0
    for acc, t in stack.terms.items():
        ids = [pid for a, out in done if a == acc for pid in out]
        assert len(ids) == len(set(ids)) == 12 and not t.overlap                                   # calls to one terminal never overlap…
        assert {r["symbol"] for r in t.sent} == {"EURUSDb" if acc == "hfm_v2" else "EURUSD"}       # …each order went to its own terminal, with its own symbol name
        assert {r["comment"].rsplit("_", 1)[0] for r in t.sent} == {acc}
        pos = stack.http.get(f"{stack.base}/{acc}/positions").json()
        assert sorted(p["id"] for p in pos) == sorted(ids) and {p["clientId"].rsplit("_", 1)[0] for p in pos} == {acc}
        assert [i["path"] for i in t.inits] == [PATHS[acc]]
    serial = sum(len(t.sent) for t in stack.terms.values()) * 0.02 * 4
    assert elapsed < serial, (elapsed, serial)  # accounts ran in parallel: one terminal never waits for another


def test_slow_terminal_does_not_block_the_others(stack):
    stack.terms["hfm_v1"].delay = 1.0
    slow = threading.Thread(target=lambda: stack.http.get(f"{stack.base}/hfm_v1/symbols/GBPUSD/specification"))
    slow.start()
    time.sleep(0.1)
    t0 = time.time()
    assert stack.http.get(f"{stack.base}/pepperstone_v1/symbols/XAGUSD/current-tick").status_code == 200
    assert stack.http.get(f"{stack.base}/hfm_v2/symbols/XAGUSD/current-tick").json()["symbol"] == "SILVERb"
    assert time.time() - t0 < 0.6
    slow.join()


def test_modify_partial_and_close_on_mt5(stack):
    b = f"{stack.base}/hfm_v2"
    pid = stack.http.post(f"{b}/trade", json={"actionType": "ORDER_TYPE_SELL", "symbol": "XAUUSD", "volume": 0.37, "stopLoss": 2420.129, "clientId": "AIT_x"}).json()["positionId"]
    req = stack.terms["hfm_v2"].sent[-1]
    assert (req["symbol"], req["volume"], req["sl"], req["price"], req["type_filling"]) == ("GOLDb", 0.37, 2420.13, 2401.0, 1)
    assert stack.http.post(f"{b}/trade", json={"actionType": "POSITION_MODIFY", "positionId": pid, "stopLoss": 2410.0}).json()["stringCode"] == "TRADE_RETCODE_DONE"
    assert stack.http.post(f"{b}/trade", json={"actionType": "POSITION_PARTIAL", "positionId": pid, "volume": 0.17}).json()["stringCode"] == "TRADE_RETCODE_DONE"
    p = stack.http.get(f"{b}/positions").json()[0]
    assert (p["id"], p["volume"], p["stopLoss"], p["symbol"]) == (pid, 0.2, 2410.0, "GOLDb")  # same position id after a partial close
    assert stack.http.post(f"{b}/trade", json={"actionType": "POSITION_CLOSE_ID", "positionId": pid}).json()["stringCode"] == "TRADE_RETCODE_DONE"
    assert stack.http.post(f"{b}/trade", json={"actionType": "POSITION_CLOSE_ID", "positionId": pid}).json()["stringCode"] == "TRADE_RETCODE_POSITION_CLOSED"
    assert stack.http.get(f"{stack.base}/hfm_v1/positions").json() == [] and stack.http.get(f"{stack.base}/pepperstone_v1/positions").json() == []


class _PidTerminal(FakeTerminal):
    """Fake MetaTrader5 module for the real worker processes: reports the process id as login and the initialize() path as company."""
    def __init__(self):
        super().__init__(0, "", "USD", DEMO)

    def initialize(self, **kw):
        import os
        self.login, self.company = os.getpid(), kw["path"]
        return True


@pytest.mark.skipif(sys.platform == "win32", reason="needs fork to hand the fake MetaTrader5 module to the workers")
def test_one_worker_process_per_account(tmp_path, monkeypatch):
    import os
    import multiprocessing
    monkeypatch.setitem(sys.modules, "MetaTrader5", _PidTerminal())
    monkeypatch.setattr(mt_bridge, "mp", multiprocessing.get_context("fork"))
    cfg = {"token": TOKEN, "allowed_ips": [IP], "state_dir": str(tmp_path / "state"), "accounts": {k: {"platform": "mt5", "terminal_path": p, "server_utc_offset_hours": 0} for k, p in PATHS.items()}}
    drivers = mt_bridge.build_drivers(cfg)
    http = TestClient(mt_bridge.create_app(cfg, drivers), headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        for _ in range(2):  # the second round reuses the same three workers
            rows = {r["_id"]: r for r in http.get("/health").json()["accounts"]}
            assert {k: r["broker"] for k, r in rows.items()} == PATHS                       # each worker initialized ITS terminal path
            pids = {k: int(r["login"]) for k, r in rows.items()}
            assert len(set(pids.values())) == 3 and os.getpid() not in pids.values()         # three separate processes, none of them the HTTP server
            assert pids == {k: d.proc.pid for k, d in drivers.items()}
        assert http.get("/users/current/accounts/hfm_v2/symbols/GOLDb/specification").json()["symbol"] == "XAUUSD"
    finally:
        for d in drivers.values():
            if d.proc:
                d.proc.kill()
