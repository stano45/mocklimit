"""Tests for the OpenAPI spec parser."""

from __future__ import annotations

from pathlib import Path

from mocklimit.openapi import RouteDefinition, parse_spec

_FIXTURES = Path(__file__).parent / "fixtures"


class TestBasicSpecParsing:
    """Parse the basic test fixture with five endpoints."""

    def test_extracts_correct_number_of_routes(self) -> None:
        """Five operations across three path items."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))

        assert len(routes) == 5

    def test_paths_and_methods(self) -> None:
        """Each route has the expected path and HTTP method."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        pairs = [(r.path, r.method) for r in routes]

        assert ("/items", "GET") in pairs
        assert ("/items", "POST") in pairs
        assert ("/items/{item_id}", "GET") in pairs
        assert ("/items/{item_id}", "DELETE") in pairs
        assert ("/health", "GET") in pairs

    def test_operation_ids(self) -> None:
        """Operation IDs are extracted when present, None otherwise."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        by_op = {r.operation_id: r for r in routes}

        assert "listItems" in by_op
        assert "createItem" in by_op
        assert "getItem" in by_op
        assert "deleteItem" in by_op

        health = next(r for r in routes if r.path == "/health")
        assert health.operation_id is None

    def test_get_items_response_schema(self) -> None:
        """GET /items returns an array schema."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        route = next(r for r in routes if r.path == "/items" and r.method == "GET")

        assert route.response_schema["type"] == "array"
        assert "items" in route.response_schema

    def test_post_items_response_schema(self) -> None:
        """POST /items returns an object schema from a 201 response."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        route = next(r for r in routes if r.path == "/items" and r.method == "POST")

        assert route.response_schema["type"] == "object"
        assert "id" in route.response_schema["properties"]

    def test_delete_has_empty_response_schema(self) -> None:
        """DELETE /items/{item_id} returns 204 with no body."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        route = next(r for r in routes if r.method == "DELETE")

        assert route.response_schema == {}

    def test_health_has_empty_response_schema(self) -> None:
        """GET /health has a 200 but no content/schema."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))
        route = next(r for r in routes if r.path == "/health")

        assert route.response_schema == {}

    def test_returns_route_definitions(self) -> None:
        """All returned objects are RouteDefinition instances."""
        routes = parse_spec(str(_FIXTURES / "test_spec.yaml"))

        assert all(isinstance(r, RouteDefinition) for r in routes)


class TestOpenAISubsetParsing:
    """Parse the trimmed OpenAI spec subset."""

    def test_extracts_three_routes(self) -> None:
        """Two on /chat/completions (GET, POST) and one on /embeddings (POST)."""
        routes = parse_spec(str(_FIXTURES / "openai_subset.yaml"))

        assert len(routes) == 3

    def test_chat_completions_post_route(self) -> None:
        """POST /chat/completions resolves to CreateChatCompletionResponse."""
        routes = parse_spec(str(_FIXTURES / "openai_subset.yaml"))
        route = next(
            r for r in routes if r.path == "/chat/completions" and r.method == "POST"
        )

        assert route.operation_id == "createChatCompletion"
        schema = route.response_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "id" in props
        assert "choices" in props
        assert "model" in props
        assert "created" in props
        assert "object" in props

    def test_chat_completions_get_route(self) -> None:
        """GET /chat/completions resolves to ChatCompletionList."""
        routes = parse_spec(str(_FIXTURES / "openai_subset.yaml"))
        route = next(
            r for r in routes if r.path == "/chat/completions" and r.method == "GET"
        )

        assert route.operation_id == "listChatCompletions"
        schema = route.response_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "object" in props
        assert "data" in props
        assert "has_more" in props
        assert "first_id" in props
        assert "last_id" in props

    def test_embeddings_route(self) -> None:
        """POST /embeddings resolves to CreateEmbeddingResponse."""
        routes = parse_spec(str(_FIXTURES / "openai_subset.yaml"))
        route = next(r for r in routes if r.path == "/embeddings")

        assert route.operation_id == "createEmbedding"
        schema = route.response_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "object" in props
        assert "data" in props
        assert "model" in props
        assert "usage" in props
