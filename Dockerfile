FROM python:3.13-slim AS build

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

COPY README.md ./
COPY src/ src/
RUN uv build --wheel --out-dir /app/dist

FROM python:3.13-slim

COPY --from=build /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

EXPOSE 8000

ENTRYPOINT ["mocklimit"]
