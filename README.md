# mocklimit

[![CI](https://github.com/stano45/mocklimit/actions/workflows/ci.yml/badge.svg)](https://github.com/stano45/mocklimit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mocklimit)](https://pypi.org/project/mocklimit/)
[![Python](https://img.shields.io/pypi/pyversions/mocklimit)](https://pypi.org/project/mocklimit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Configurable mock API server with realistic rate limiting for testing.
Point it at an OpenAPI spec, define rate limit policies in YAML, and get a
local server that behaves like a rate-limited production API, complete with
correct headers, 429 responses, and token usage estimation.

## Features

- **OpenAPI spec auto-routing** - parses your spec and registers all endpoints with dummy responses
- **Fixed window rate limiting** with sub-second precision
- **Quantized rate limiter** for aligned reset windows
- **Composite limits** - stack multiple limits per endpoint (e.g. RPM + TPM)
- **Provider-accurate headers** - configurable header names (`x-ratelimit-limit-requests`, etc.)
- **Token usage estimation** for LLM API mocking
- **Configurable response latency** simulation
- **Per-key scoping** by API key or IP address
- **Request statistics** via `/mocklimit/stats`

## Installation

```bash
pip install mocklimit
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add mocklimit
```

## Quick start

### 1. Create a rate limit config

```yaml
# limits.yaml
policies:
  openai_chat:
    strategy: fixed_window
    limits:
      - max_requests: 5
        window_seconds: 60
    scope: api_key
    response_latency_ms: [0, 0]
    headers:
      limit: x-ratelimit-limit-requests
      remaining: x-ratelimit-remaining-requests
      reset: x-ratelimit-reset-requests

endpoints:
  /chat/completions:
    methods: [POST]
    policy: openai_chat
    token_estimation:
      input: characters_div_4
      output: [50, 500]
```

### 2. Start the server

```bash
mocklimit serve --spec openapi.yaml --rate-config limits.yaml
```

The server reads your OpenAPI spec for route definitions and response schemas,
then applies rate limiting according to the config. Requests beyond the limit
get a `429` with appropriate `Retry-After` and rate limit headers.

### 3. Options

```
mocklimit serve --spec <path> --rate-config <path> [--host HOST] [--port PORT]
```

| Flag | Default | Description |
|---|---|---|
| `--spec` | *(required)* | Path to OpenAPI spec (YAML) |
| `--rate-config` | *(required)* | Path to rate limit config (YAML) |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to listen on |

## Rate limit config reference

### Policies

Each policy defines a rate limiting strategy:

| Field | Type | Description |
|---|---|---|
| `strategy` | `"fixed_window"` | Rate limiting algorithm |
| `limits` | list | One or more `{max_requests, window_seconds}` pairs |
| `scope` | `"api_key"` \| `"ip"` | How to identify clients |
| `response_latency_ms` | `[min, max]` | Simulated response delay range (ms) |
| `headers.limit` | string | Header name for the request limit |
| `headers.remaining` | string | Header name for remaining requests |
| `headers.reset` | string | Header name for reset time |

### Endpoints

Map API paths to policies:

| Field | Type | Description |
|---|---|---|
| `methods` | list of strings | HTTP methods to rate limit |
| `policy` | string | Name of the policy to apply |
| `token_estimation` | object (optional) | `{input: "characters_div_4", output: [min, max]}` |

## Programmatic usage

You can also embed the server directly in tests:

```python
from mocklimit.server import create_app

app = create_app("openapi.yaml", "limits.yaml")
```

This returns a standard FastAPI app that can be used with any ASGI test client.

## License

MIT
