"""Import smoke tests."""

from mocklimit import ratelimit


def test_ratelimit_importable() -> None:
    """Verify mocklimit.ratelimit is importable."""
    assert ratelimit.__name__ == "mocklimit.ratelimit"
