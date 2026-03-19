# mocklimit


Configurable mock API server with realistic rate limiting for testing.

## Features

- OpenAPI spec auto-routing
- Configurable rate limiting strategies
- Sub-second enforcement
- Composite limits
- SDK-compatible responses
- Provider-accurate headers

## Installation

```bash
uv add mocklimit
```

## Quick Start

```bash
python -m mocklimit serve --spec openapi.yaml --rate-config limits.yaml
```

## License

MIT
