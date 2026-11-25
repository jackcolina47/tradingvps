"""Local stand-in for the Windows VPS: runs windows_bridge/mt_bridge.py on 127.0.0.1:8765 with a fake MT4 Expert Advisor (accounts hfm_v1 and pepperstone_v1).
Usage: python tests/fake_bridge_server.py   · token: see TOKEN below."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn  # noqa: E402
from test_iter120_mt_bridge import FakeEA, mt_bridge  # noqa: E402
from fx.symbols import SPECS  # noqa: E402

TOKEN = "preview-fake-bridge-token-0123456789"
if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="fake_bridge_"))
    accounts = {}
    for acc, suffix in (("hfm_v1", "b"), ("pepperstone_v1", "")):
        ea = FakeEA(tmp / acc)
        ea.SPECS = {**({s: (SPECS[s]["digits"], 0) for s in SPECS} if suffix else {}), **{f"{s}{suffix}": (SPECS[s]["digits"], 4) for s in SPECS}}
        ea.start()
        accounts[acc] = {"platform": "mt4", "files_dir": str(tmp / acc)}
    cfg = {"token": TOKEN, "allowed_ips": ["127.0.0.1"], "state_dir": str(tmp / "state"), "accounts": accounts}
    uvicorn.run(mt_bridge.create_app(cfg, mt_bridge.build_drivers(cfg)), host="127.0.0.1", port=8765, log_level="warning")
