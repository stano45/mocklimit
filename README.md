# mocklimit

[![CI](https://github.com/stano45/mocklimit/actions/workflows/ci.yml/badge.svg)](https://github.com/stano45/mocklimit/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/mocklimit)](https://pypi.org/project/mocklimit/)
[![Python](https://img.shields.io/pypi/pyversions/mocklimit)](https://pypi.org/project/mocklimit/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A configurable mock API server that simulates rate limiting. Point it at an OpenAPI spec and a YAML config, and you get a local server that responds like a real rate-limited API. Useful for testing backpressure, retry logic, and admission control without hitting production.

## Installation

```bash
pip install mocklimit
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add mocklimit
```

## Quick start

### 1. Write a rate limit config

```yaml
# limits.yaml
policies:
  chat:
    strategy: fixed_window
    limits:
      - dimension: requests
        limit: 60
        window_seconds: 60
        headers:
          limit: x-ratelimit-limit-requests
          remaining: x-ratelimit-remaining-requests
          reset: x-ratelimit-reset-requests
      - dimension: tokens
        limit: 150000
        window_seconds: 60
        headers:
          limit: x-ratelimit-limit-tokens
          remaining: x-ratelimit-remaining-tokens
          reset: x-ratelimit-reset-tokens
    scope: api_key
    format:
      reset: go_duration
      retry_after:
        header: retry-after-ms
        unit: milliseconds
    error_template:
      provider: openai

endpoints:
  /chat/completions:
    methods: [POST]
    policy: chat
    resources:
      requests:
        input:
          strategy: fixed
          value: 1
        output:
          strategy: fixed
          value: 0
      tokens:
        input:
          strategy: characters_div_4
        output:
          strategy: random
          range: [50, 500]
    timing:
      base_ms: [20, 100]
      scale:
        resource: tokens
        component: output
        ms_per_unit: 0.02
```

### 2. Start the server

```bash
mocklimit serve --spec openapi.yaml --rate-config limits.yaml
```

The server reads your OpenAPI spec for route definitions and response schemas, then applies rate limiting per the config. Once the limit is reached, requests get a 429 with the configured headers and error body.

### 3. CLI options

```
mocklimit serve --spec <path> --rate-config <path> [--host HOST] [--port PORT]
```

| Flag | Default | Description |
|---|---|---|
| `--spec` | (required) | Path to OpenAPI spec (YAML) |
| `--rate-config` | (required) | Path to rate limit config (YAML) |
| `--host` | `127.0.0.1` | Host to bind to |
| `--port` | `8000` | Port to listen on |

## Features

- Parses your OpenAPI spec and registers all endpoints with dummy responses
- Three rate limiting algorithms: fixed window, sliding window, token bucket
- Multiple limits per endpoint (e.g. RPM + TPM + input TPM)
- Per-limit header configuration (each limit can emit its own header group)
- Configurable reset format: relative seconds, Go-style duration, RFC 3339 timestamps
- Configurable retry-after: header name and unit (seconds or milliseconds)
- Provider-accurate 429 error bodies (OpenAI, Anthropic, Google templates)
- Configurable resource estimation with input/output components
- Response latency simulation with output-proportional scaling
- Per-key scoping by API key or IP address
- Request statistics via `/mocklimit/stats`
- Prometheus metrics at `/metrics`

## Config reference

### Policies

A policy defines how a group of endpoints are rate limited.

```yaml
policies:
  my_policy:
    strategy: fixed_window       # or: sliding_window, token_bucket
    limits: [...]                # list of limit definitions
    scope: api_key               # or: ip
    format:                      # optional, controls header value formatting
      reset: relative_seconds    # or: go_duration, rfc3339
      retry_after:
        header: Retry-After      # header name for retry signal
        unit: seconds            # or: milliseconds
    headers:                     # optional fallback headers (used when limits don't define their own)
      limit: x-ratelimit-limit
      remaining: x-ratelimit-remaining
      reset: x-ratelimit-reset
    error_template:              # optional, controls 429 response body
      provider: openai           # or: anthropic, google
    response_latency_ms: [0, 0]  # legacy latency range, prefer endpoint timing
```

### Limits

Each policy has one or more limits. A limit tracks usage of a single dimension.

For fixed window and sliding window:

```yaml
limits:
  - dimension: requests      # name of the resource to track
    limit: 60                # max allowed per window
    window_seconds: 60       # window duration
    headers:                 # optional, per-limit header names
      limit: x-ratelimit-limit-requests
      remaining: x-ratelimit-remaining-requests
      reset: x-ratelimit-reset-requests
```

For token bucket:

```yaml
limits:
  - dimension: tokens.output   # supports dotted notation for components
    capacity: 16000            # bucket capacity
    refill_rate: 266           # tokens per second refill
    headers:
      limit: anthropic-ratelimit-output-tokens-limit
      remaining: anthropic-ratelimit-output-tokens-remaining
      reset: anthropic-ratelimit-output-tokens-reset
```

The `dimension` field references a resource from the endpoint config. Use dotted notation to track a specific component: `tokens.input` tracks only the input component, `tokens.output` tracks only the output, and plain `tokens` tracks the total (input + output).

### Endpoints

Each endpoint maps an API path to a policy and defines how to estimate resource costs.

```yaml
endpoints:
  /chat/completions:
    methods: [POST]
    policy: chat
    resources:
      requests:
        input:
          strategy: fixed
          value: 1
        output:
          strategy: fixed
          value: 0
      tokens:
        input:
          strategy: characters_div_4
        output:
          strategy: random
          range: [50, 500]
    timing:
      base_ms: [20, 100]
      scale:
        resource: tokens
        component: output
        ms_per_unit: 0.02
```

### Resources

Each resource has an `input` and `output` component. The total cost is input + output. Every component has a strategy:

| Strategy | Config | Behavior |
|---|---|---|
| `fixed` | `value: N` | Always returns N |
| `random` | `range: [min, max]` | Uniform random integer between min and max |
| `characters_div_4` | (none) | `len(request_body) // 4`, rough token estimate |

The `requests` resource is typically `fixed: 1` input with `fixed: 0` output, so each request costs 1. The `tokens` resource usually uses `characters_div_4` for input and `random` for output, simulating variable LLM response lengths.

### Timing

Controls how long responses take. Useful for simulating real API latency.

```yaml
timing:
  base_ms: [20, 100]       # random base delay in this range
  scale:
    resource: tokens        # scale proportionally to this resource
    component: output       # use the output component
    ms_per_unit: 0.02       # add 0.02ms per output token
```

A request with 500 output tokens and base range [20, 100] would take roughly 20-100ms base + 10ms scaling = 30-110ms total.

### Reset formats

The `format.reset` field controls how the reset header value is formatted:

| Format | Example | Used by |
|---|---|---|
| `relative_seconds` | `4.3` | Default, plain seconds until reset |
| `go_duration` | `12ms`, `4.253s`, `1m0s` | OpenAI |
| `rfc3339` | `2026-06-07T15:30:00Z` | Anthropic |

### Retry-after

The `format.retry_after` block controls the 429 retry signal:

```yaml
retry_after:
  header: retry-after-ms    # header name (default: "Retry-After")
  unit: milliseconds        # seconds or milliseconds
```

### Error templates

When `error_template.provider` is set, 429 responses include a realistic error body instead of the dummy response. The bodies below are returned as JSON.

`openai`:

```yaml
error:
  message: "Rate limit reached for tokens in organization on tokens per min (TPM): Limit 150000, Used 150000, Requested 1. Please retry after 6m0s."
  type: tokens
  param: null
  code: rate_limit_exceeded
```

`anthropic`:

```yaml
type: error
error:
  type: rate_limit_error
  message: "Number of requests has exceeded your rate limit. Please retry after 5 seconds."
```

`google`:

```yaml
error:
  code: 429
  message: "Resource has been exhausted (e.g. check quota). Please retry in 3.500000s."
  status: RESOURCE_EXHAUSTED
  details:
    - "@type": type.googleapis.com/google.rpc.QuotaFailure
      violations:
        - quotaMetric: generativelanguage.googleapis.com/requests_count
          quotaLimit: "15"
          quotaDimensions:
            model: unknown
    - "@type": type.googleapis.com/google.rpc.RetryInfo
      retryDelay: "3.500000s"
```

### Header behavior

Headers are emitted on every response (200 and 429). There are two ways to configure them:

1. Per-limit headers: each limit in the policy defines its own header names. All configured header groups are emitted simultaneously. This is what Anthropic and OpenAI do (separate request and token headers).

2. Policy-level fallback headers: a single header group on the policy, used with whichever limit is most restrictive. Simpler, but you only see one dimension.

If no headers are configured on any limit or the policy, no rate-limit headers are emitted (Google Gemini behavior, which only signals limits in the 429 error body).

## Full example configs

See `tests/server/configs/` for complete working examples:
- `anthropic.yaml` - token bucket, RFC 3339 reset, 3 separate header groups, Anthropic error body
- `openai.yaml` - fixed window, Go-style duration, 2 header groups, millisecond retry-after, OpenAI error body
- `gemini.yaml` - fixed window, no success headers, input-only TPM, Google error body

## Prometheus metrics

Exposed at `/metrics/`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `mocklimit_requests_total` | Counter | `endpoint`, `method`, `scope_key`, `status` | Total requests |
| `mocklimit_rate_limited_total` | Counter | `endpoint`, `method`, `scope_key` | Requests denied (429) |
| `mocklimit_request_duration_seconds` | Histogram | `endpoint`, `method`, `status` | Response latency |
| `mocklimit_rate_limit_remaining` | Gauge | `endpoint`, `policy`, `scope_key` | Remaining budget |

Scrape config:

```yaml
scrape_configs:
  - job_name: mocklimit
    metrics_path: /metrics/
    static_configs:
      - targets: ["localhost:8000"]
```

## Programmatic usage

For use in tests without starting a subprocess:

```python
from mocklimit.server import create_app

app = create_app(spec_path="openapi.yaml", rate_config_path="limits.yaml")
```

This returns a FastAPI app you can use with any ASGI test client (httpx, starlette TestClient, etc).

## License

MIT
