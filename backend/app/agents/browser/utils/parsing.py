"""
JSON parsing utilities for the Browser Auto agent.

This module contains utilities for parsing, validating, and processing JSON data.
"""

import json
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


class JSONUtils:
    """Utilities for JSON operations."""

    @staticmethod
    def safe_loads(json_string: str, default: Any = None) -> Any:
        """
        Safely parse JSON string with error handling.

        Args:
            json_string: JSON string to parse
            default: Default value to return on error

        Returns:
            Parsed JSON object or default value
        """
        try:
            return json.loads(json_string)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "json.parse_failed", component="decision_parser", error=str(e), handled=False
            )
            return default

    @staticmethod
    def safe_dumps(obj: Any, indent: int = 2, default: Any = None) -> str:
        """
        Safely serialize object to JSON string.

        Args:
            obj: Object to serialize
            indent: Indentation level
            default: Default value for non-serializable objects

        Returns:
            JSON string
        """
        try:
            return json.dumps(obj, indent=indent, default=default)
        except (TypeError, ValueError) as e:
            logger.error(
                "json.serialize_failed", component="decision_parser", error=str(e), handled=False
            )
            return "{}"

    @staticmethod
    def extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        """
        Extract JSON object from text that may contain other content.

        Args:
            text: Text that may contain JSON

        Returns:
            Extracted JSON object or None if not found
        """
        try:
            # Find JSON boundaries
            start = text.find("{")
            if start == -1:
                return None

            # Find matching closing brace
            brace_count = 0
            end = start
            for i, char in enumerate(text[start:], start):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            if brace_count != 0:
                logger.warning("json.unmatched_braces", component="decision_parser")
                return None

            json_str = text[start:end]
            return json.loads(json_str)

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "json.extract_failed", component="decision_parser", error=str(e), handled=False
            )
            return None

    @staticmethod
    def validate_decision_json(data: Dict[str, Any]) -> bool:
        """
        Validate that a decision JSON has required fields.

        Args:
            data: JSON data to validate

        Returns:
            True if valid, False otherwise
        """
        required_fields = ["action_type", "description"]

        try:
            # Check required fields
            for field in required_fields:
                if field not in data:
                    logger.warning(
                        "json.validation_error",
                        component="decision_parser",
                        field=field,
                    )
                    return False

            # Check action_type values
            valid_action_types = ["goto", "act", "observe", "extract", "end"]
            if data.get("action_type") not in valid_action_types:
                logger.warning(
                    "json.invalid_action_type",
                    component="decision_parser",
                    action_type=data.get("action_type"),
                )
                return False

            return True

        except Exception as e:
            logger.error(
                "json.validation_error", component="decision_parser", error=str(e), handled=False
            )
            return False

    @staticmethod
    def clean_decision_json(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Clean and normalize decision JSON data.

        Args:
            data: Raw JSON data

        Returns:
            Cleaned JSON data
        """
        try:
            cleaned = {
                "action_type": data.get("action_type", "end"),
                "description": data.get("description", ""),
                "reasoning": data.get("reasoning", ""),
                "action_text": data.get("action_text", ""),
            }

            # Ensure action_type is valid
            if cleaned["action_type"] not in ["goto", "act", "observe", "extract", "end"]:
                cleaned["action_type"] = "end"

            return cleaned

        except Exception as e:
            logger.error(
                "json.clean_failed", component="decision_parser", error=str(e), handled=False
            )
            return {
                "action_type": "end",
                "description": "Error in JSON processing",
                "reasoning": f"Error: {str(e)}",
                "action_text": "",
            }


class DecisionParser:
    """Specialized parser for decision JSON data."""

    def __init__(self):
        """Initialize the decision parser."""
        self.logger = structlog.get_logger(__name__)

    def parse_decision(self, text: str) -> Dict[str, Any]:
        """
        Parse decision text and extract JSON.

        Args:
            text: Text containing decision JSON

        Returns:
            Parsed decision data
        """
        try:
            # Extract JSON from text
            decision = JSONUtils.extract_json_from_text(text)

            if decision is None:
                self.logger.warning("decision_parser.no_json_found", component="decision_parser")
                return self._create_error_decision("No JSON found in response")

            # Validate the decision
            if not JSONUtils.validate_decision_json(decision):
                self.logger.warning("decision_parser.invalid_json", component="decision_parser")
                return self._create_error_decision("Invalid decision JSON")

            # Clean and return
            return JSONUtils.clean_decision_json(decision)

        except Exception as e:
            self.logger.error(
                "decision_parser.parse_failed",
                component="decision_parser",
                error=str(e),
                handled=False,
            )
            return self._create_error_decision(f"Parse error: {str(e)}")

    def _create_error_decision(self, error_message: str) -> Dict[str, Any]:
        """Create an error decision object."""
        return {
            "action_type": "end",
            "description": "Failed to parse decision, ending process",
            "reasoning": error_message,
            "action_text": "",
        }
