import logging
from jsonschema import validate, ValidationError
from typing import Any

from scripts.exceptions import ResponseValidationError

logger = logging.getLogger("renovate-operation-check.clients-schema_validator")

SCHEMAS = {
    "pull_requests": {
        "type": "object",
        "required": ["values"],
        "properties": {
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "title", "state"],
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "state": {"type": "string", "enum": ["OPEN", "DECLINED", "MERGED"]},
                        "version": {"type": "integer"},
                        "fromRef": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "displayId": {"type": "string"},
                                "repository": {"type": "object"},
                            },
                        },
                        "toRef": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "displayId": {"type": "string"},
                                "repository": {"type": "object"},
                            },
                        },
                        "author": {
                            "type": "object",
                            "properties": {
                                "user": {
                                    "type": "object",
                                    "properties": {"name": {"type": "string"}},
                                }
                            },
                        },
                        "createdDate": {"type": "integer"},
                    },
                },
            }
        },
    },
    "pull_request": {
        "type": "object",
        "required": ["id", "title", "state"],
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "string"},
            "state": {"type": "string", "enum": ["OPEN", "DECLINED", "MERGED"]},
            "version": {"type": "integer"},
            "fromRef": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "displayId": {"type": "string"}},
            },
            "toRef": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "displayId": {"type": "string"}},
            },
        },
    },
    "branches": {
        "type": "object",
        "required": ["values"],
        "properties": {
            "values": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["displayId"],
                    "properties": {
                        "displayId": {"type": "string"},
                        "name": {"type": "string"},
                        "isDefault": {"type": "boolean"},
                        "latestCommit": {"type": "string"},
                    },
                },
            }
        },
    },
    "branch": {
        "type": "object",
        "required": ["displayId"],
        "properties": {
            "displayId": {"type": "string"},
            "name": {"type": "string"},
            "isDefault": {"type": "boolean"},
            "latestCommit": {"type": "string"},
        },
    },
}


def validate_response_schema(data: Any, schema_name: str) -> None:
    if schema_name not in SCHEMAS:
        logger.warning(f"No schema defined for '{schema_name}', skipping validation")
        return

    try:
        validate(instance=data, schema=SCHEMAS[schema_name])
        logger.debug(f"Schema validation successful for '{schema_name}'")
    except ValidationError as e:
        error_msg = f"[SEC] Schema validation failed for '{schema_name}': {e.message}"
        logger.error(error_msg)
        raise ResponseValidationError(error_msg) from e
    except Exception as e:
        error_msg = f"Unexpected error during schema validation for '{schema_name}': {str(e)}"
        logger.error(error_msg)
        raise ResponseValidationError(error_msg) from e
