"""Local stand-in for the Windows VPS: runs windows_bridge/mt_bridge.py on 127.0.0.1:8765 with fake MT5 terminals (accounts hfm_v1 and pepperstone_v1).
Usage: python tests/fake_bridge_server.py   · token: see TOKEN below."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "windows_bridge" / "tests"))
import uvicorn  # noqa: E402
from test_mt_bridge import FakeTerminal, mt_bridge  # noqa: E402
from fx.symbols import SPECS  # noqa: E402

TOKEN = "preview-fake-bridge-token-0123456789"
if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="fake_bridge_"))
    price = lambda s: 150.0 if SPECS[s]["digits"] <= 3 else 1.1  # noqa: E731
    terms = {"hfm_v1": FakeTerminal(101, "HF Markets", "USD", {f"{s}b": (SPECS[s]["digits"], 4, price(s)) for s in SPECS}),   # HFM Zero: «b» suffix
             "pepperstone_v1": FakeTerminal(303, "Pepperstone", "GBP", {s: (SPECS[s]["digits"], 4, price(s)) for s in SPECS})}  # Pepperstone UK accounts are in GBP
    cfg = {"token": TOKEN, "allowed_ips": ["127.0.0.1"], "state_dir": str(tmp / "state"),
           "accounts": {k: {"platform": "mt5", "terminal_path": f"C:/{k}/terminal64.exe", "server_utc_offset_hours": 0} for k in terms}}
    drivers = mt_bridge.build_drivers(cfg, core_factory=lambda acc, a: mt_bridge.Mt5Core(terms[acc], a))
    uvicorn.run(mt_bridge.create_app(cfg, drivers), host="127.0.0.1", port=8765, log_level="warning")
