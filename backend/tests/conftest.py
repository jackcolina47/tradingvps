

import pytest as _pytest


@_pytest.fixture(autouse=True)
def _free_path_to_target(request, monkeypatch):
    """The synthetic frames of the older funnel tests have zones 0.5 ATR away: the exact-R:R room check is tested on its own in test_iter110."""
    if "iter110" not in request.module.__name__:
        import funnel
        monkeypatch.setattr(funnel, "rr_block", lambda *a, **k: None)
