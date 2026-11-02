import os, sys, json, requests
B = "https://repo-workspace-14--8ursakdp.preview.emergentagent.com"
t = requests.post(f"{B}/api/auth/login", json={"password": "BlackCat2233@"}).json()["token"]
H = {"Authorization": f"Bearer {t}"}
d = requests.get(f"{B}/api/fx/dashboard", headers=H).json()
s = d["settings"]
print("running", d["status"]["running"], "profile_active", d.get("profile_active"))
print("symbols", s["symbols"])
print("symbol_params", s["symbol_params"], "sessions_allowed", s.get("sessions_allowed"))
print("market_hours", json.dumps(d["status"].get("market_hours"))[:600])
for p in ["/api/fx/walk-forward/check", "/api/fx/ai/optimize-all"]:
    r = requests.get(f"{B}{p}", headers=H).json()
    print(p, json.dumps(r)[:400])
c = requests.get(f"{B}/api/fx/config", headers=H)
print("fx config", c.status_code, c.text[:300])
c = requests.get(f"{B}/api/config", headers=H)
print("crypto config", c.status_code, c.text[:300])
cd = requests.get(f"{B}/api/dashboard", headers=H).json()
print("crypto settings symbols", cd["settings"]["symbols"], cd["settings"].get("symbol_params"))
