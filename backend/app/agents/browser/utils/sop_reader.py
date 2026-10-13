"""
SOP Reader utility for the Browser Auto agent.

This module reads and parses Standard Operating Procedures from YAML files.
"""

from pathlib import Path
from typing import Optional, Dict, List, Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


class SOPReader:
    """Utility class for reading and parsing SOPs from YAML files."""

    def __init__(self, sop_file: str = "sop.yaml", params: Optional[Dict[str, Any]] = None):
        """
        Initialize the SOP reader.

        Args:
            sop_file: Path to the YAML SOP file
            params: Optional runtime parameters to pass to the SOP (e.g., {"erp_job_id": "HTL-001"})
        """
        self.sop_file = Path(sop_file)
        self.sop_data: Optional[Dict[str, Any]] = None
        self.params: Dict[str, Any] = params or {}

    def read_sop(self) -> str:
        """
        Read the active SOP from the YAML file.

        Returns:
            The active SOP as a string
        """
        if not self.sop_file.exists():
            logger.error(
                "sop_reader.file_not_found",
                component="sop_reader",
                sop_file=str(self.sop_file),
            )
            return ""

        try:
            with open(self.sop_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse YAML format
            sop = self._parse_yaml_sop(content)

            if not sop:
                logger.warning("sop_reader.no_active_sop", component="sop_reader")
                return ""

            logger.info(
                "sop_reader.read_ok",
                component="sop_reader",
                sop_file=str(self.sop_file),
            )
            return sop

        except Exception as e:
            logger.error(
                "sop_reader.read_error", component="sop_reader", error=str(e), handled=False
            )
            return ""

    def _parse_yaml_sop(self, content: str) -> str:
        """
        Parse YAML SOP format and store structured data.

        Returns:
            SOP as a readable string for display
        """
        try:
            data = yaml.safe_load(content)
            self.sop_data = data

            if not data or "steps" not in data:
                logger.error("sop_reader.missing_steps_key", component="sop_reader")
                return ""

            steps = data["steps"]
            if not steps:
                return ""

            # Convert to readable string format
            sop_lines = []
            for i, step in enumerate(steps, 1):
                action = step.get("action", "unknown")
                desc = step.get("description", "")
                sop_lines.append(f"{i}. [{action.upper()}] {desc}")

            return "\n".join(sop_lines)

        except yaml.YAMLError as e:
            logger.error(
                "sop_reader.yaml_parse_error", component="sop_reader", error=str(e), handled=False
            )
            return ""

    def get_steps(self) -> List[Dict[str, Any]]:
        """
        Get structured step data from the YAML file.

        Returns:
            List of step dictionaries with action and description
        """
        if self.sop_data and "steps" in self.sop_data:
            return self.sop_data["steps"]
        return []

    def get_step_config(self, step_idx: int) -> Optional[Dict[str, Any]]:
        """
        Get full configuration for a specific step.

        Args:
            step_idx: Step index (0-based)

        Returns:
            Step configuration dictionary with all fields (method, selector, etc.)
        """
        steps = self.get_steps()
        if 0 <= step_idx < len(steps):
            return steps[step_idx]
        return None

    def get_metadata(self) -> Dict[str, Any]:
        """
        Get metadata from YAML (retry config, name, version, etc.).

        Returns:
            Metadata dictionary
        """
        if self.sop_data:
            return self.sop_data.get("metadata", {})
        return {}

    def get_credentials_config(self) -> Dict[str, str]:
        """
        Get credential configuration (environment variable names).

        Returns:
            Credentials dictionary with env var names
        """
        if self.sop_data:
            return self.sop_data.get("credentials", {})
        return {}

    def get_retry_config(self) -> Dict[str, Any]:
        """
        Get retry configuration from metadata.

        Returns:
            Retry config with max_retries and retry_delay
        """
        metadata = self.get_metadata()
        return metadata.get("retry_config", {"max_retries": 2, "retry_delay": 1.0})

    def get_params(self) -> Dict[str, Any]:
        """
        Get runtime parameters.

        Returns:
            Runtime parameters dictionary
        """
        return self.params

    def set_params(self, params: Dict[str, Any]) -> None:
        """
        Set or update runtime parameters.

        Args:
            params: Runtime parameters dictionary
        """
        self.params = params or {}
