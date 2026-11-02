# Per-bot trade history isolation + trade detail/note/candles routes (/api, /api/fx, /api/pepper)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PWD = "BlackCat2233@"

IDS = {
    "": {"BTCUSDT": "6ae84e68799fc5d2666cceae", "ETHUSDT": "6ae84e68799fc5d2666cceaf"},
    "/fx": {"XAUUSD": "6ae84e68799fc5d2666cceb0", "EURUSD": "6ae84e68799fc5d2666cceb1"},
    "/pepper": {"GBPUSD": "6ae84e68799fc5d2666cceb2"},
}


@pytest.fixture(scope="module")
def s():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"password": PWD}, timeout=30)
    if r.status_code != 200:
        pytest.skip("login failed")
    ses = requests.Session()
    ses.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return ses


@pytest.mark.parametrize("prefix", ["", "/fx", "/pepper"])
def test_trades_isolated(s, prefix):
    r = s.get(f"{BASE_URL}/api{prefix}/trades?limit=500", timeout=30)
    assert r.status_code == 200
    syms = {t["symbol"] for t in r.json()}
    assert syms == set(IDS[prefix].keys()), syms
    ids = {t["id"] for t in r.json()}
    for other, m in IDS.items():
        if other != prefix:
            assert not (ids & set(m.values()))


@pytest.mark.parametrize("prefix,sym", [("", "BTCUSDT"), ("/fx", "XAUUSD"), ("/pepper", "GBPUSD")])
def test_detail(s, prefix, sym):
    tid = IDS[prefix][sym]
    r = s.get(f"{BASE_URL}/api{prefix}/trades/{tid}/detail", timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["trade"]["symbol"] == sym
    if sym == "BTCUSDT":
        assert d.get("signal"), d.keys()


@pytest.mark.parametrize("prefix,sym", [("/fx", "XAUUSD"), ("", "ETHUSDT")])
def test_cross_bot_detail_404(s, prefix, sym):
    # a trade id from another bot must not be found
    tid = IDS[prefix][sym]
    other = "/pepper"
    r = s.get(f"{BASE_URL}/api{other}/trades/{tid}/detail", timeout=30)
    assert r.status_code == 404
    r = s.get(f"{BASE_URL}/api/trades/{IDS['/fx']['XAUUSD']}/detail", timeout=30)
    assert r.status_code == 404


@pytest.mark.parametrize("prefix,sym", [("", "BTCUSDT"), ("/fx", "XAUUSD"), ("/pepper", "GBPUSD")])
def test_candles(s, prefix, sym):
    tid = IDS[prefix][sym]
    r = s.get(f"{BASE_URL}/api{prefix}/trades/{tid}/candles", timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    print(sym, {k: (len(v) if isinstance(v, list) else v) for k, v in d.items()})
    assert isinstance(d.get("candles"), list) and len(d["candles"]) > 0


def test_fx_note_roundtrip(s):
    tid = IDS["/fx"]["EURUSD"]
    orig = s.get(f"{BASE_URL}/api/fx/trades/{tid}/detail", timeout=30).json()["trade"]
    r = s.put(f"{BASE_URL}/api/fx/trades/{tid}/note", json={"note": "TEST_nota api", "tags": ["revisar"]}, timeout=30)
    assert r.status_code == 200, r.text
    assert r.json()["note"] == "TEST_nota api"
    d = s.get(f"{BASE_URL}/api/fx/trades/{tid}/detail", timeout=30).json()["trade"]
    assert d["note"] == "TEST_nota api" and "revisar" in d["tags"]
    # crypto router must not find it
    assert s.put(f"{BASE_URL}/api/trades/{tid}/note", json={"note": "x", "tags": []}, timeout=30).status_code == 404
    # restore
    s.put(f"{BASE_URL}/api/fx/trades/{tid}/note", json={"note": orig.get("note") or "", "tags": orig.get("tags") or []}, timeout=30)
