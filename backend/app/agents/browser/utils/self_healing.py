"""
Self-healing utility for automatic popup detection and removal.

This module uses LLM vision capabilities to detect and close popups, ads,
cookie banners, and other overlays that may interfere with automation.
"""

import base64
from typing import Dict, Any, Optional

import structlog
from playwright.async_api import Page

logger = structlog.get_logger(__name__)


class SelfHealing:
    """Detects and closes popups/ads/overlays using LLM vision."""

    def __init__(self, llm_client):
        """
        Initialize self-healing with LLM client.

        Args:
            llm_client: LLMClient instance for popup detection
        """
        self.llm_client = llm_client
        self.logger = logger

    async def detect_and_close_popup(
        self, page: Page, screenshot_base64: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze page for popups and attempt to close them.

        Args:
            page: Playwright page object
            screenshot_base64: Optional base64 encoded screenshot (will take one if not provided)

        Returns:
            {
                "has_popup": bool,
                "popup_type": str,  # "ad", "cookie", "newsletter", "modal", "overlay", "none"
                "close_attempted": bool,
                "close_successful": bool,
                "action_taken": str,  # Description of action taken
                "confidence": str  # "high", "medium", "low"
            }
        """
        try:
            # Take screenshot if not provided
            if not screenshot_base64:
                screenshot_bytes = await page.screenshot(type="png")
                screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
                logger.debug("self_healing.screenshot_captured", component="self_healing")

            # Build prompt for popup detection
            prompt = self._build_popup_detection_prompt()

            # Call LLM with Computer Use model for vision analysis
            logger.info("self_healing.analyzing", component="self_healing")
            response = await self.llm_client.make_decision(
                prompt=prompt,
                screenshot_base64=screenshot_base64,
                use_computer_use=True,  # Force Computer Use model for vision
            )

            if response.get("status") != "success":
                logger.warning(
                    "self_healing.llm_failed",
                    component="self_healing",
                    error=response.get("error"),
                )
                return self._no_popup_result("llm_error")

            # Parse LLM response - get raw text and parse JSON ourselves
            raw_response = response.get("raw_response", "")

            # Try to parse JSON from raw response
            import json
            import re

            try:
                # Try direct JSON parse first
                popup_data = json.loads(raw_response)
            except Exception:
                # If that fails, try to extract JSON from markdown code blocks
                json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
                if json_match:
                    popup_data = json.loads(json_match.group(1))
                else:
                    # Try to find any JSON object in the response
                    json_match = re.search(r'\{.*"has_popup".*\}', raw_response, re.DOTALL)
                    if json_match:
                        popup_data = json.loads(json_match.group(0))
                    else:
                        logger.warning(
                            "self_healing.parse_failed",
                            component="self_healing",
                            raw_response=raw_response[:100],
                        )
                        return self._no_popup_result("parse_error")

            has_popup = popup_data.get("has_popup", False)
            popup_type = popup_data.get("popup_type", "none")
            blocking_content = popup_data.get("blocking_content", False)
            close_method = popup_data.get("close_method", "none")
            close_selector = popup_data.get("close_selector")
            close_coordinates = popup_data.get("close_coordinates")
            confidence = popup_data.get("confidence", "low")

            logger.info(
                "self_healing.popup_detected",
                component="self_healing",
                has_popup=has_popup,
                popup_type=popup_type,
                confidence=confidence,
            )

            # If no popup or not blocking, return early
            if not has_popup or not blocking_content:
                logger.info("self_healing.no_blocking_popup", component="self_healing")
                return self._no_popup_result("no_popup")

            # Attempt to close the popup
            logger.info(
                "self_healing.closing_popup",
                component="self_healing",
                popup_type=popup_type,
            )
            close_successful = await self._close_popup(
                page, close_method, close_selector, close_coordinates
            )

            if close_successful:
                logger.info(
                    "self_healing.popup_closed",
                    component="self_healing",
                    popup_type=popup_type,
                )
                action_taken = f"Closed {popup_type} using {close_method}"
            else:
                logger.warning(
                    "self_healing.popup_close_failed",
                    component="self_healing",
                    popup_type=popup_type,
                )
                action_taken = f"Attempted to close {popup_type} but failed"

            return {
                "has_popup": has_popup,
                "popup_type": popup_type,
                "close_attempted": True,
                "close_successful": close_successful,
                "action_taken": action_taken,
                "confidence": confidence,
            }

        except Exception as e:
            logger.error(
                "self_healing.detection_error",
                component="self_healing",
                error=str(e),
                handled=False,
            )
            return {
                "has_popup": False,
                "popup_type": "error",
                "close_attempted": False,
                "close_successful": False,
                "action_taken": f"Error: {str(e)}",
                "confidence": "low",
            }

    def _build_popup_detection_prompt(self) -> str:
        """Build the prompt for popup detection - MUST match PopupAnalysis Pydantic schema."""
        return """Analyze this screenshot of a web page. Determine if there are any popups, overlays, modals, advertisements, cookie banners, or newsletter signups that are blocking or covering the main content.

Look for:
- Ad overlays or popups
- Cookie consent banners
- Newsletter subscription popups
- Modal dialogs
- Privacy notices
- Age verification screens
- Any other overlay that blocks content

Return a JSON response with this EXACT structure (all fields REQUIRED):
{
  "has_popup": true or false,
  "popup_type": "ad" | "cookie" | "newsletter" | "modal" | "overlay" | "none",
  "blocking_content": true or false,
  "close_method": "click_button" | "click_outside" | "press_escape" | "none",
  "close_selector": "css selector here" or null,
  "confidence": 0.95,  // MUST BE A NUMBER between 0.0 and 1.0
  "risk_level": "low" | "medium" | "high",
  "reasoning": "Brief 2-3 sentence explanation of: (1) why this is/isn't a popup, (2) the closing strategy, (3) any risks"
}

Confidence scoring (MUST be a number):
- 0.9-1.0: Clear popup with obvious close button
- 0.7-0.9: Likely popup, close mechanism identified
- 0.5-0.7: Uncertain if popup, or close method unclear
- 0.0-0.5: Very uncertain, may not be a popup

Risk level:
- LOW: Safe to close, clear close button, won't break page
- MEDIUM: Some risk, may need multiple attempts
- HIGH: High risk, might accidentally navigate away or skip required step

Common close button selectors:
- .close, .close-button, .modal-close
- [aria-label="Close"], [aria-label="Dismiss"]
- button.dismiss, button.decline
- .cookie-banner .accept, .cookie-banner .decline
- .pymntButton input[type="button"], #pymtReq input[value="Continue"] (payment reminders)

Only return the JSON, no other text."""

    async def _close_popup(
        self,
        page: Page,
        close_method: str,
        close_selector: Optional[str],
        close_coordinates: Optional[Dict[str, int]] = None,
    ) -> bool:
        """
        Attempt to close a popup using various strategies.

        Args:
            page: Playwright page object
            close_method: Method to use for closing
            close_selector: CSS selector for close button
            close_coordinates: x,y coordinates for clicking

        Returns:
            True if popup was closed successfully, False otherwise
        """
        try:
            # Strategy 1: Click close button by selector
            if close_selector and close_method == "click_button":
                try:
                    # Wait briefly for element and click
                    await page.click(close_selector, timeout=3000)
                    await page.wait_for_timeout(1000)  # Wait for popup to close
                    logger.debug(
                        "self_healing.close_button_clicked",
                        component="self_healing",
                        close_selector=close_selector,
                    )
                    return True
                except Exception as e:
                    logger.debug(
                        "self_healing.selector_click_failed",
                        component="self_healing",
                        error=str(e),
                    )

            # Strategy 2: Click at coordinates
            if close_coordinates and "x" in close_coordinates and "y" in close_coordinates:
                try:
                    await page.mouse.click(close_coordinates["x"], close_coordinates["y"])
                    await page.wait_for_timeout(1000)
                    logger.debug(
                        "self_healing.coordinates_clicked",
                        component="self_healing",
                        close_coordinates=close_coordinates,
                    )
                    return True
                except Exception as e:
                    logger.debug(
                        "self_healing.coordinate_click_failed",
                        component="self_healing",
                        error=str(e),
                    )

            # Strategy 3: Press Escape key
            if close_method == "press_escape":
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)
                    logger.debug("self_healing.escape_pressed", component="self_healing")
                    return True
                except Exception as e:
                    logger.debug(
                        "self_healing.escape_failed",
                        component="self_healing",
                        error=str(e),
                    )

            # Strategy 4: Click outside overlay (top-left corner)
            if close_method == "click_outside":
                try:
                    await page.mouse.click(10, 10)
                    await page.wait_for_timeout(500)
                    logger.debug("self_healing.clicked_outside", component="self_healing")
                    return True
                except Exception as e:
                    logger.debug(
                        "self_healing.click_outside_failed",
                        component="self_healing",
                        error=str(e),
                    )

            # Strategy 5: Try common close button selectors as fallback
            common_selectors = [
                ".close",
                ".close-button",
                ".modal-close",
                "[aria-label='Close']",
                "[aria-label='Dismiss']",
                "button.dismiss",
                "button.decline",
                "button.reject",
                ".cookie-banner button.accept",
                ".cookie-banner button.decline",
            ]

            for selector in common_selectors:
                try:
                    await page.click(selector, timeout=1000)
                    await page.wait_for_timeout(500)
                    logger.debug(
                        "self_healing.fallback_selector_worked",
                        component="self_healing",
                        selector=selector,
                    )
                    return True
                except Exception:
                    continue

            logger.warning("self_healing.all_strategies_failed", component="self_healing")
            return False

        except Exception as e:
            logger.error(
                "self_healing.close_error",
                component="self_healing",
                error=str(e),
                handled=False,
            )
            return False

    def _no_popup_result(self, reason: str = "no_popup") -> Dict[str, Any]:
        """Return a standard 'no popup' result."""
        return {
            "has_popup": False,
            "popup_type": "none",
            "close_attempted": False,
            "close_successful": False,
            "action_taken": f"No action needed ({reason})",
            "confidence": "high",
        }
