"""Import smoke tests."""

import pytest

from mocklimit import ratelimit


def test_ratelimit_importable() -> None:
    """Verify mocklimit.ratelimit is importable."""
    if ratelimit.__name__ != "mocklimit.ratelimit":
        pytest.fail(f"unexpected module name: {ratelimit.__name__}")
