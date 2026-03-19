set quiet

default: check

lint:
    uv run ruff check .
    uv run basedpyright

format:
    uv run ruff format .

test:
    uv run pytest

check: lint test
