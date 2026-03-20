# Changelog

## 0.1.0 (2026-03-20)

Initial release.

- OpenAPI spec auto-routing with dummy response generation
- Fixed window rate limiter with sub-second enforcement
- Quantized rate limiter for aligned reset windows
- Composite rate limits (multiple limits per endpoint)
- Provider-accurate rate limit headers (e.g. `x-ratelimit-*`)
- Configurable response latency simulation
- Token usage estimation for LLM API mocking
- YAML-based rate limit configuration
- CLI: `mocklimit serve --spec <spec> --rate-config <config>`
- Per-endpoint, per-key request statistics via `/mocklimit/stats`
