"""Helper: print state-integrity snapshot (mode, running, symbols, symbol_params, fx flags)."""
import os, json, requests
B = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
pw = open("/app/memory/test_credentials.md").read().split("`")[1]
t = requests.post(f"{B}/api/auth/login", json={"password": pw}).json()["token"]
H = {"Authorization": f"Bearer {t}"}
def g(p):
    r = requests.get(B + p, headers=H, timeout=60)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:200]
print("mode", g("/api/mode"))
s = g("/api/settings")[1]
print("settings keys", list(s.keys())[:60] if isinstance(s, dict) else s)
for k in ("symbols", "symbol_params", "reopt_symbols", "running"):
    print(k, json.dumps(s.get(k) if isinstance(s, dict) else None)[:600])
print("status", json.dumps(g("/api/status")[1])[:500])
for pre in ("/api/fx", "/api/pepper"):
    st = g(pre + "/status")[1]
    print(pre, "status", json.dumps(st)[:500])
    cfg = g(pre + "/settings")[1]
    print(pre, "settings pa_block", cfg.get("price_action_block") if isinstance(cfg, dict) else cfg, "mode", cfg.get("mode") if isinstance(cfg, dict) else None)
