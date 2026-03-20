"""Tests for the OpenAPI response generator."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletion

from mocklimit.openapi import generate_all_responses, generate_dummy_response

_FIXTURES = Path(__file__).parent / "fixtures"


class TestFlatSchema:
    """Flat object schemas with primitive property types."""

    def test_string_and_integer(self) -> None:
        """Strings become 'mock_string', integers become 1."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"name": "mock_string", "age": 1}

    def test_number_and_boolean(self) -> None:
        """Numbers become 1.0, booleans become True."""
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "active": {"type": "boolean"},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"score": 1.0, "active": True}

    def test_no_type_returns_none(self) -> None:
        """A property with no type produces None."""
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "unknown": {},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"unknown": None}

    def test_required_filters_properties(self) -> None:
        """Only required fields are generated when required is specified."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "string"},
                "c": {"type": "string"},
            },
            "required": ["a", "c"],
        }
        result = generate_dummy_response(schema)

        assert result == {"a": "mock_string", "c": "mock_string"}

    def test_no_required_generates_all(self) -> None:
        """All properties are generated when required is absent."""
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"x": 1, "y": 1}


class TestEnums:
    """Enum handling picks the first value."""

    def test_string_enum(self) -> None:
        """First enum value is selected."""
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"status": "active"}

    def test_integer_enum(self) -> None:
        """Works for non-string enums too."""
        schema = {
            "type": "object",
            "properties": {
                "code": {"type": "integer", "enum": [200, 404, 500]},
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"code": 200}


class TestNestedObjects:
    """Object schemas containing nested object properties."""

    def test_single_nesting(self) -> None:
        """A nested object property is recursed into."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "id": {"type": "integer"},
                    },
                },
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"user": {"name": "mock_string", "id": 1}}

    def test_double_nesting(self) -> None:
        """Two levels of nesting resolve correctly."""
        schema = {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {
                        "inner": {
                            "type": "object",
                            "properties": {
                                "value": {"type": "number"},
                            },
                        },
                    },
                },
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"outer": {"inner": {"value": 1.0}}}


class TestArrays:
    """Array schemas produce single-element lists."""

    def test_primitive_items(self) -> None:
        """Array of strings produces ['mock_string']."""
        schema = {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"tags": ["mock_string"]}

    def test_object_items(self) -> None:
        """Array of objects produces a list with one dummy object."""
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = generate_dummy_response(schema)

        assert result == {"items": [{"id": 1, "name": "mock_string"}]}


class TestRefResolution:
    """$ref values are resolved via the all_schemas dict."""

    def test_top_level_ref(self) -> None:
        """A top-level $ref resolves to the referenced schema."""
        schema = {"$ref": "#/components/schemas/User"}
        all_schemas = {
            "User": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            },
        }
        result = generate_dummy_response(schema, all_schemas)

        assert result == {"name": "mock_string"}

    def test_nested_ref(self) -> None:
        """A $ref inside a property resolves correctly."""
        schema = {
            "type": "object",
            "properties": {
                "author": {"$ref": "#/components/schemas/Person"},
            },
        }
        all_schemas = {
            "Person": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                },
            },
        }
        result = generate_dummy_response(schema, all_schemas)

        assert result == {"author": {"email": "mock_string"}}

    def test_ref_in_array_items(self) -> None:
        """A $ref inside array items resolves correctly."""
        schema = {
            "type": "object",
            "properties": {
                "users": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/User"},
                },
            },
        }
        all_schemas = {
            "User": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                },
            },
        }
        result = generate_dummy_response(schema, all_schemas)

        assert result == {"users": [{"id": 1}]}

    def test_missing_ref_returns_empty(self) -> None:
        """An unresolvable $ref produces an empty dict."""
        schema = {"$ref": "#/components/schemas/Missing"}
        result = generate_dummy_response(schema, {})

        assert result == {}


class TestGenerateAllResponses:
    """Integration with full spec parsing."""

    def test_returns_expected_routes(self) -> None:
        """All three routes from the OpenAI subset are present."""
        responses = generate_all_responses(str(_FIXTURES / "openai_subset.yaml"))

        assert "POST /chat/completions" in responses
        assert "GET /chat/completions" in responses
        assert "POST /embeddings" in responses

    def test_chat_completions_response_structure(self) -> None:
        """POST /chat/completions response has the expected top-level keys."""
        responses = generate_all_responses(str(_FIXTURES / "openai_subset.yaml"))
        response = responses["POST /chat/completions"]

        assert "id" in response
        assert "object" in response
        assert "created" in response
        assert "model" in response
        assert "choices" in response

    def test_embeddings_response_structure(self) -> None:
        """POST /embeddings response has the expected top-level keys."""
        responses = generate_all_responses(str(_FIXTURES / "openai_subset.yaml"))
        response = responses["POST /embeddings"]

        assert "object" in response
        assert "data" in response
        assert "model" in response
        assert "usage" in response


class TestOpenAISDKIntegration:
    """The generated ChatCompletion response parses with the openai SDK."""

    def test_chat_completion_validates(self) -> None:
        """openai.types.chat.ChatCompletion accepts the generated response."""
        responses = generate_all_responses(str(_FIXTURES / "openai_subset.yaml"))
        response = responses["POST /chat/completions"]

        completion = ChatCompletion.model_validate(response)

        assert completion.id == "mock_string"
        assert completion.object == "chat.completion"
        assert completion.model == "mock_string"
        assert completion.created == 1
        assert len(completion.choices) == 1
        assert completion.choices[0].finish_reason == "stop"
        assert completion.choices[0].index == 1
        assert completion.choices[0].message.role == "assistant"
        assert completion.choices[0].message.content == "mock_string"
