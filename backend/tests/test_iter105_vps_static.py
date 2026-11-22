"""Iteration 105 — VPS static serving via Caddy (old vs fixed).

Validates the fix for: stale cached '/' pointing to a missing hashed bundle
served back as index.html (text/html with 200), which makes the SPA render
as a black page. The fixed Caddyfile:
  - serves /static/* with immutable cache, 404 when missing (no SPA fallback)
  - serves the HTML shell with Cache-Control: no-cache
  - keeps /api/* proxied and gzip enabled

Tests auto-skip if the two local Caddy servers on 8097/8098 aren't running.
"""

import os
import re
import pytest
import requests

OLD = "http://localhost:8097"
NEW = "http://localhost:8098"


def _alive(url):
    try:
        return requests.get(url, timeout=3).status_code < 500
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_alive(OLD) and _alive(NEW)),
    reason="Caddy 8097/8098 not running in this environment",
)


def _bundle_hashes():
    js = sorted(os.listdir("/root/vpsbuild/static/js"))
    css = sorted(os.listdir("/root/vpsbuild/static/css"))
    js_main = next(f for f in js if f.startswith("main.") and f.endswith(".js"))
    css_main = next(f for f in css if f.startswith("main.") and f.endswith(".css"))
    return js_main, css_main


# ---------- OLD Caddyfile — reproduces the bug ----------

class TestOldReproducesBug:
    def test_missing_bundle_falls_back_to_index_html(self):
        r = requests.get(f"{OLD}/static/js/main.NOEXISTE.js")
        assert r.status_code == 200
        # The bug: HTML is served as a JS asset
        assert "text/html" in r.headers.get("content-type", "")
        assert "<html" in r.text.lower()

    def test_old_root_has_no_cache_control(self):
        r = requests.get(f"{OLD}/")
        assert r.status_code == 200
        # The old config did not set Cache-Control -> stale HTML reused
        assert "cache-control" not in {k.lower() for k in r.headers}


# ---------- NEW Caddyfile — verifies the fix ----------

class TestFixSpaRoutes:
    @pytest.mark.parametrize("path", ["/", "/bybit", "/hotforex", "/pepperstone", "/loquesea"])
    def test_spa_routes_serve_index_with_no_cache(self, path):
        r = requests.get(f"{NEW}{path}")
        assert r.status_code == 200, path
        assert "text/html" in r.headers.get("content-type", ""), path
        assert r.headers.get("Cache-Control") == "no-cache", path
        assert "<div id=\"root\"></div>" in r.text or "id=\"root\"" in r.text

    def test_etag_revalidation_returns_304(self):
        r = requests.get(f"{NEW}/")
        etag = r.headers.get("ETag")
        assert etag, "Caddy should emit an ETag for the HTML"
        r2 = requests.get(f"{NEW}/", headers={"If-None-Match": etag})
        assert r2.status_code == 304


class TestFixStaticBundles:
    def test_real_js_bundle(self):
        js_main, _ = _bundle_hashes()
        r = requests.get(f"{NEW}/static/js/{js_main}")
        assert r.status_code == 200
        assert "javascript" in r.headers.get("content-type", "").lower()
        assert r.headers.get("Cache-Control") == "public, max-age=31536000, immutable"
        assert "<html" not in r.text[:200].lower()

    def test_real_css_bundle(self):
        _, css_main = _bundle_hashes()
        r = requests.get(f"{NEW}/static/css/{css_main}")
        assert r.status_code == 200
        assert "css" in r.headers.get("content-type", "").lower()
        assert r.headers.get("Cache-Control") == "public, max-age=31536000, immutable"

    def test_missing_bundle_returns_404_not_html(self):
        r = requests.get(f"{NEW}/static/js/main.NOEXISTE.js")
        assert r.status_code == 404
        assert "text/html" not in r.headers.get("content-type", "") or "<html" not in r.text.lower()

    def test_gzip_still_active(self):
        js_main, _ = _bundle_hashes()
        r = requests.get(
            f"{NEW}/static/js/{js_main}",
            headers={"Accept-Encoding": "gzip"},
        )
        assert r.status_code == 200
        assert r.headers.get("Content-Encoding") == "gzip"


class TestFixApiProxy:
    def test_api_root_proxied(self):
        r = requests.get(f"{NEW}/api/")
        assert r.status_code == 200
        # backend returns JSON
        assert "application/json" in r.headers.get("content-type", "")

    def test_api_login_proxied(self):
        pwd = os.environ.get("INITIAL_PASSWORD", "BlackCat2233@")
        r = requests.post(f"{NEW}/api/auth/login", json={"password": pwd}, timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "token" in data or "access_token" in data


class TestCaddyfileRepo:
    def test_repo_caddyfile_has_static_block_and_no_cache(self):
        txt = open("/app/deploy/Caddyfile").read()
        assert re.search(r"handle\s+/static/\*", txt)
        assert "immutable" in txt
        assert re.search(r"Cache-Control\s+\"no-cache\"", txt)
        assert "try_files {path} /index.html" in txt

    def test_dockerfile_copies_caddyfile(self):
        dock = open("/app/deploy/frontend.Dockerfile").read()
        assert "Caddyfile" in dock
