"""Iter117 — AI review of ONE open position on demand (POST /positions/{id}/ai-review)."""
import inspect
import os
import requests
import pytest
from routers import shared_routes

API = "http://localhost:8001/api"


@pytest.fixture(scope="module")
def auth():
    pw = os.environ.get("INITIAL_PASSWORD") or "BlackCat2233@"
    r = requests.post(f"{API}/auth/login", json={"password": pw}, timeout=15)
    if r.status_code != 200:
        pytest.skip("backend not reachable")
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_route_reviews_only_that_position():
    src = inspect.getsource(shared_routes)
    assert '@router.post("/positions/{pos_id}/ai-review")' in src and "only=[ObjectId(p.id)]" in src
    assert src.index('"/positions/ai-review"') < src.index('"/positions/{pos_id}/ai-review"')


@pytest.mark.parametrize("prefix", ["", "/fx", "/pepper"])
@pytest.mark.parametrize("pid,code", [("000000000000000000000000", 404), ("nope", 404)])
def test_unknown_position(auth, prefix, pid, code):
    assert requests.post(f"{API}{prefix}/positions/{pid}/ai-review", headers=auth, timeout=15).status_code == code


def test_requires_auth():
    assert requests.post(f"{API}/positions/000000000000000000000000/ai-review", timeout=15).status_code in (401, 403)
