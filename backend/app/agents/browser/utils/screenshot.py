"""
Screenshot utilities for the Browser Auto agent.

This module contains utilities for capturing, processing, and encoding screenshots.
"""

import base64
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class ScreenshotUtils:
    """Utilities for screenshot operations."""

    @staticmethod
    def encode_to_base64(screenshot_bytes: bytes) -> str:
        """
        Encode screenshot bytes to base64 string.

        Args:
            screenshot_bytes: Raw screenshot bytes

        Returns:
            Base64 encoded string
        """
        try:
            return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception as e:
            logger.error("screenshot.encode_failed", component="screenshot", error=str(e))
            raise


class ScreenshotCapture:
    """Class for capturing screenshots from browser pages."""

    def __init__(self, page=None):
        """
        Initialize the screenshot capture.

        Args:
            page: Browser page object
        """
        self.page = page

    async def capture_screenshot(self) -> Optional[bytes]:
        """
        Capture a screenshot of the current page.

        Returns:
            Screenshot bytes or None if failed
        """
        if not self.page:
            logger.warning("screenshot.no_page", component="screenshot")
            return None

        try:
            logger.info("screenshot.capturing", component="screenshot")
            # v3: self.page is already the native Playwright page
            screenshot_bytes = await self.page.screenshot()
            logger.info("screenshot.captured", component="screenshot")
            return screenshot_bytes
        except Exception as e:
            logger.error(
                "screenshot.capture_failed", component="screenshot", error=str(e), handled=False
            )
            return None
