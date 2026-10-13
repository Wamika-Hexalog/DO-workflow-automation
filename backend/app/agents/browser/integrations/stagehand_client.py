"""
Stagehand client integration for the Browser Auto agent.

This module contains the Stagehand v3 browser automation client with dual-mode support:
- Local Mode: Fast local Chrome (no Browserbase browser rate limits)
- Browserbase Mode: Remote browser with session replay capability
"""

import os
import asyncio
import contextlib
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import httpx
import structlog
from stagehand import Stagehand
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from langsmith import traceable
from ..utils import ScreenshotCapture

logger = structlog.get_logger(__name__)

# SDK version for Stagehand v3 API calls
SDK_VERSION = "3.5.0"

# Releasing a Browserbase session is worth retrying: a rejected or dropped
# release keeps its concurrency slot until the project's own timeout (up to an
# hour), which starves every other browser lane.
_RELEASE_ATTEMPTS = 3
_RELEASE_RETRY_DELAY_SECONDS = 2.0
_RELEASE_HTTP_TIMEOUT_SECONDS = 10.0
# How long a cancelled close() waits for the shielded release before propagating.
_RELEASE_GRACE_SECONDS = 10.0

# Strong references to in-flight release tasks. Without this a release still
# running when a cancelled close() gives up on it is only referenced by that
# frame; once the frame unwinds the task becomes GC-eligible and dies with
# "Task was destroyed but it is pending!" — stranding the session it was about to
# hand back. Tasks discard themselves on completion.
_PENDING_RELEASES: set[asyncio.Task[None]] = set()

# Same rooting problem, same solution, for a driver stop that outlived its bound.
_PENDING_DRIVER_STOPS: set[asyncio.Task[None]] = set()


def _log_late_driver_stop(task: "asyncio.Task[None]") -> None:
    """Consume the result of a driver stop that outlived its bound.

    Attached only when the stop timed out and teardown moved on. Without it the
    task's exception is never retrieved, so asyncio reports it at GC — detached
    from the run that caused it and attributable to nothing.
    """
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.warning(
            "stagehand.playwright.stop_failed_late", component="stagehand", error=str(error)
        )


# Substrings Playwright uses when an action's target page/window is torn down
# mid-action. Used by close-tolerant actions (see `closes_window`): some LogiSys
# lookup popups select-and-close on double-click, so the page vanishes while the
# action is still running even though the selection succeeded.
_PAGE_CLOSED_SIGNATURES = (
    "has been closed",
    "target closed",
    "target page, context or browser has been closed",
)


def _is_page_closed_error(exc: Exception) -> bool:
    """True if the exception is Playwright reporting the target page/window closed."""
    msg = str(exc).lower()
    return any(sig in msg for sig in _PAGE_CLOSED_SIGNATURES)


# ── Blocking-modal guard (F1: Softlink ReactModal payment nag) ───────────────
# LogiSys' dashboard now renders a full-viewport Softlink "Payment Reminder"
# ReactModal that intercepts pointer events over the menu, so the first menu
# click 3-strikes and the run terminates. force_click can't get through (the
# real mouse event hit-tests onto the overlay); JS-hiding the overlay does.
# 6h dedup on unique normalized modal text so ~dozens of runs/day yield one
# alert per distinct message; mirrors FAILURE_NOTE_DEDUPE_WINDOW (services/tasks.py).
_MODAL_ALERT_DEDUP: dict[str, datetime] = {}
_MODAL_ALERT_DEDUP_WINDOW = timedelta(hours=6)


def _is_known_payment_nag(text: str) -> bool:
    """True only for the known, benign Softlink 'payment overdue' nag (F1) — the one
    case we auto-dismiss and continue. Requires the specific overdue-payment wording
    (a bare 'Softlink' brand match is deliberately NOT enough — an unrelated
    Softlink-branded modal must be treated as unknown and left in place / escalated)."""
    t = (text or "").lower()
    return "payment" in t and "overdue" in t


# Regex for the modal's own dismiss control (its "Continue"/"OK"/"Proceed" button —
# live: the Softlink nag renders `<input type="button" value="Continue">`). The JS
# trims the label before testing, so anchored ok/close need no whitespace class.
_MODAL_DISMISS_LABEL = "continue|proceed|^ok$|^close$"

# DETECT captures text + geometry WITHOUT mutating, and reports whether the overlay
# carries its OWN dismiss button (Continue/OK/…). Python then decides: the known
# benign payment nag is dismissed via that button (graceful, the intended UX) and
# the run proceeds; an unknown full-viewport ReactModal is LEFT IN PLACE (never
# touched — captured, alerted critically, allowed to fail the click). Per-overlay
# reads are try-wrapped so one detached node cannot void the whole capture.
_DETECT_BLOCKING_MODAL_JS = (
    """
() => {
  const DISMISS = /"""
    + _MODAL_DISMISS_LABEL
    + """/i;
  const out = { found: 0, texts: [], has_controls: false,
                has_dismiss_button: false, capture_errors: 0 };
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const ov of document.querySelectorAll('div.ReactModal__Overlay')) {
    try {
      const cs = getComputedStyle(ov);
      if (cs.display === 'none' || cs.visibility === 'hidden' ||
          parseFloat(cs.opacity || '1') < 0.01) continue;
      const r = ov.getBoundingClientRect();
      if (r.width * r.height < 0.3 * vw * vh) continue;   // small/non-blocking: leave alone
      const content = ov.querySelector('.ReactModal__Content') || ov;
      out.texts.push((content.innerText || '').trim().slice(0, 1500));
      const ctrls = ov.querySelectorAll(
        'button, input[type=button], input[type=submit], [role="button"], input, select, textarea');
      if (ctrls.length) out.has_controls = true;
      for (const c of ctrls) {
        if (DISMISS.test((c.innerText || c.value || '').trim())) { out.has_dismiss_button = true; break; }
      }
      out.found += 1;
    } catch (e) {
      out.capture_errors += 1;   // one detached node must not void the whole capture
    }
  }
  return out;
}
"""
)

# Click the overlay's own dismiss button (Continue/OK/…) — a REAL element click on
# a control that is topmost at its own centre (verified live: probe 9fc8b818, the
# Continue input removes the whole modal). It DISMISSES, never opens a popup, so it
# is within the team's no-JS-click-popup-openers rule. Returns {clicked, gone}.
_CLICK_DISMISS_MODAL_JS = (
    """
() => {
  const DISMISS = /"""
    + _MODAL_DISMISS_LABEL
    + """/i;
  const vw = window.innerWidth, vh = window.innerHeight;
  let clicked = false;
  for (const ov of document.querySelectorAll('div.ReactModal__Overlay')) {
    try {
      const cs = getComputedStyle(ov);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      const r = ov.getBoundingClientRect();
      if (r.width * r.height < 0.3 * vw * vh) continue;
      const btn = [...ov.querySelectorAll(
        'button, input[type=button], input[type=submit], [role="button"]')]
        .find((c) => DISMISS.test((c.innerText || c.value || '').trim()));
      if (btn) { btn.click(); clicked = true; }
    } catch (e) { /* skip */ }
  }
  // Report whether any blocking overlay is still visible after the dismiss click.
  let gone = true;
  for (const ov of document.querySelectorAll('div.ReactModal__Overlay')) {
    const cs = getComputedStyle(ov);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = ov.getBoundingClientRect();
    if (r.width * r.height >= 0.3 * vw * vh) { gone = false; break; }
  }
  return { clicked, gone };
}
"""
)

# Fallback only — hide the overlay if it has no usable dismiss button, or the
# dismiss click failed to clear it. display:none removes it from the layout so the
# menu underneath is clickable. Never used on the click-to-dismiss happy path.
_HIDE_BLOCKING_MODAL_JS = """
() => {
  let hidden = 0;
  const vw = window.innerWidth, vh = window.innerHeight;
  for (const ov of document.querySelectorAll('div.ReactModal__Overlay')) {
    try {
      const cs = getComputedStyle(ov);
      if (cs.display === 'none' || cs.visibility === 'hidden' ||
          parseFloat(cs.opacity || '1') < 0.01) continue;
      const r = ov.getBoundingClientRect();
      if (r.width * r.height < 0.3 * vw * vh) continue;
      ov.style.setProperty('display', 'none', 'important');
      hidden += 1;
    } catch (e) { /* skip a detached node */ }
  }
  if (hidden) {
    try { document.body.classList.remove('ReactModal__Body--open'); } catch (e) {}
  }
  return hidden;
}
"""


class StagehandClient:
    """Client for Stagehand v3 browser automation with dual-mode support."""

    # Class-level defaults so teardown never depends on __init__ having run. Tests
    # and recovery paths build this client with __new__ and set only the attributes
    # they need; an AttributeError inside close() would abort teardown at the first
    # stage and land in the outer handler, skipping the session release — turning a
    # missing attribute into the leak this file exists to prevent.
    teardown_timeout_s: float = 10.0
    _teardown_timeouts = 0
    _teardown_failures = 0

    def __init__(self, headless: bool = False, llm_client=None, use_browserbase: bool = None):
        """
        Initialize the Stagehand v3 client.

        Args:
            headless: Whether to run the browser in headless mode
            llm_client: LLMClient instance for direct LLM calls with screenshots
            use_browserbase: Override for browser mode. If None, reads from USE_BROWSERBASE env var.
                           False = Local Chrome (fast, no browser rate limits)
                           True = Browserbase (session replay, production)
        """
        self.headless = headless
        self.llm_client = llm_client

        # Determine mode: env var or parameter (default to local for fast testing)
        if use_browserbase is None:
            self.use_browserbase = os.getenv("USE_BROWSERBASE", "false").lower() == "true"
        else:
            self.use_browserbase = use_browserbase

        # v3: Stagehand is now a pure API client
        # Note: Stagehand v3 requires model_api_key to be explicitly provided
        google_api_key = os.getenv("GOOGLE_API_KEY")
        browserbase_api_key = os.getenv("BROWSERBASE_API_KEY")
        browserbase_project_id = os.getenv("BROWSERBASE_PROJECT_ID")

        if not google_api_key:
            logger.warning("stagehand.env.missing", component="stagehand", var="GOOGLE_API_KEY")
        if not browserbase_api_key:
            logger.warning(
                "stagehand.env.missing", component="stagehand", var="BROWSERBASE_API_KEY"
            )
        if not browserbase_project_id:
            logger.warning(
                "stagehand.env.missing", component="stagehand", var="BROWSERBASE_PROJECT_ID"
            )

        self.api_client = Stagehand(
            browserbase_api_key=browserbase_api_key,
            browserbase_project_id=browserbase_project_id,
            model_api_key=google_api_key,
        )

        # Separate Playwright state (BYOB pattern)
        self.session_id = None
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._initialized = False
        self.windows = {}  # Track multiple windows by name
        self.dismissed_modals: list[dict] = []  # per-session forensic record of dismissals
        self._alert_tasks: set = set()  # holds fire-and-forget alert tasks (anti-GC)

        # Peak-hour headroom: scale the engine's internal navigation timeouts.
        # LogiSys nav (new-window open, click actionability, ASP.NET overlay) runs
        # far slower during the 2-3pm IST peak; the off-peak-sized fixed timeouts
        # then expire mid-navigation. See Settings.sop_timeout_scale.
        # settings.sop_timeout_scale is already validated/clamped to a finite
        # [1.0, 10.0] float; guard the import path so the browser never fails to
        # start over a config hiccup.
        try:
            from .._compat import settings

            self.timeout_scale = float(settings.sop_timeout_scale)
        except Exception:
            self.timeout_scale = 2.0
        if not (self.timeout_scale >= 1.0):  # also catches NaN
            self.timeout_scale = 2.0

        # Time bound for each local teardown await. See Settings.browser_teardown_timeout_s.
        # Guarded like timeout_scale above: a config hiccup must not stop the browser
        # from starting.
        try:
            from .._compat import settings

            self.teardown_timeout_s = float(settings.browser_teardown_timeout_s)
        except Exception:
            self.teardown_timeout_s = 10.0
        if not (self.teardown_timeout_s > 0):  # also catches NaN
            self.teardown_timeout_s = 10.0

        # Teardown stages that did not finish cleanly, rebuilt on each close(). Two
        # counters rather than one list because the closing line has to say WHICH
        # kind of degradation happened — reporting a raised close as a timeout sends
        # whoever reads it looking for a hang that never occurred.
        self._teardown_timeouts = 0
        self._teardown_failures = 0

    def _scaled(self, ms: int) -> int:
        """Scale a base navigation timeout for peak-hour headroom (>= base)."""
        return int(ms * self.timeout_scale)

    @property
    def _teardown_degraded_reason(self) -> str | None:
        """Which kind of teardown degradation to report, or None if it was clean."""
        if self._teardown_timeouts and self._teardown_failures:
            return "teardown_timeout_and_failure"
        if self._teardown_timeouts:
            return "teardown_timeout"
        if self._teardown_failures:
            return "teardown_failure"
        return None

    async def _close_step(
        self,
        coro: Any,
        *,
        timeout_slug: str,
        error_slug: str,
        **fields: Any,
    ) -> bool:
        """Await one local teardown action under a time bound. Returns True if it ran.

        Every caller is on the shutdown path, where the SOP has already produced its
        results, so an await that never returns cannot change what the run produces —
        only how long it holds the browser. Bound it, name the outcome, move on.

        Cancelling the inner coroutine is safe for the Playwright calls that use this:
        a cancelled protocol callback drops its own future and the dispatcher discards
        the late reply (`_connection.py` dispatch returns early on a cancelled
        callback), so the connection stays usable for the stages behind this one.
        ``playwright.stop()`` does NOT qualify — see its call site in close().

        The bound is only as hard as the coroutine is cancellation-cooperative. A
        coroutine that swallows CancelledError and returns makes wait_for return
        normally, late — so measure the elapsed time and report an overrun rather
        than calling that a clean close. Playwright's calls do cooperate; this exists
        so the claim in the log is true even when something does not.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            await asyncio.wait_for(coro, timeout=self.teardown_timeout_s)
        except asyncio.TimeoutError:
            self._teardown_timeouts += 1
            logger.warning(
                timeout_slug,
                component="stagehand",
                timeout_s=self.teardown_timeout_s,
                **fields,
            )
            return False
        except Exception as e:
            self._teardown_failures += 1
            logger.warning(error_slug, component="stagehand", error=str(e), **fields)
            return False

        elapsed = loop.time() - started
        if elapsed > self.teardown_timeout_s * 1.5:
            # Returned well outside the budget: the coroutine swallowed the
            # cancellation wait_for sent and kept working. It DID close, so this is
            # not a failure to close — it is a bound we could not enforce, and the
            # log has to say so rather than claim a clean close.
            #
            # The margin is deliberate. A coroutine that suppresses cancellation and
            # returns straight away lands at ~the budget and does no real harm; the
            # case worth naming is the one that keeps running past it.
            self._teardown_failures += 1
            logger.warning(
                "stagehand.teardown.bound_overrun",
                component="stagehand",
                timeout_s=self.teardown_timeout_s,
                elapsed_s=round(elapsed, 3),
                **fields,
            )
        return True

    @traceable(name="stagehand_init", run_type="tool")
    async def initialize(self) -> None:
        """Initialize the Stagehand v3 client and browser (local or Browserbase)."""
        if self._initialized:
            return

        # Get Google API key
        google_api_key = os.getenv("GOOGLE_API_KEY")
        if not google_api_key:
            logger.warning(
                "stagehand.init.no_google_api_key",
                component="stagehand",
                detail="Stagehand may fall back to default LLM",
            )

        try:
            # Start Playwright
            self.playwright = await async_playwright().start()

            if self.use_browserbase:
                # Create the Browserbase session in a region close to the target site.
                # LogiSys is India-hosted; Browserbase's default region (us-west-2)
                # adds ~20-30s of cross-continent latency PER page load (measured:
                # 23s DCL / 33s load), which blew past the SOP timeouts and was the
                # real cause of the recurring "Browserbase" failures. ap-southeast-1
                # (Singapore) loads LogiSys in ~2s. Stagehand's own session.start does
                # NOT honour the region (it always lands in us-west-2), so create the
                # session directly via the Browserbase API and connect Playwright to
                # the region-correct connectUrl it returns. Override via BROWSERBASE_REGION.
                region = os.getenv("BROWSERBASE_REGION", "ap-southeast-1")
                browserbase_api_key = os.getenv("BROWSERBASE_API_KEY")
                browserbase_project_id = os.getenv("BROWSERBASE_PROJECT_ID")
                async with httpx.AsyncClient(timeout=30.0, verify=False) as _bb_http:
                    _resp = await _bb_http.post(
                        "https://api.browserbase.com/v1/sessions",
                        headers={
                            "X-BB-API-Key": browserbase_api_key,
                            "Content-Type": "application/json",
                        },
                        json={"projectId": browserbase_project_id, "region": region},
                    )
                    _resp.raise_for_status()
                    _session = _resp.json()
                self.session_id = _session["id"]
                # The connectUrl carries the correct region routing; a manually-built
                # connect URL 404s for any non-default region.
                cdp_url = _session.get("connectUrl")
                # ===== BROWSERBASE MODE =====
                logger.info(
                    "stagehand.session.start",
                    component="stagehand",
                    mode="browserbase",
                    session_id=self.session_id,
                    region=region,
                )
                logger.info(
                    "stagehand.session.replay",
                    component="stagehand",
                    replay_url=f"https://www.browserbase.com/sessions/{self.session_id}",
                )

                if not cdp_url:
                    cdp_url = (
                        f"wss://connect.browserbase.com"
                        f"?apiKey={browserbase_api_key}"
                        f"&sessionId={self.session_id}"
                    )

                self.browser = await self.playwright.chromium.connect_over_cdp(cdp_url)
                self.context = self.browser.contexts[0]
                self.page = (
                    self.context.pages[0] if self.context.pages else await self.context.new_page()
                )

                # Set viewport size to match local mode (prevents zoom/visibility issues)
                await self.page.set_viewport_size({"width": 1920, "height": 1080})

                # Tell Browserbase to capture downloads into session storage so they
                # can be retrieved via the downloads API after triggering a download.
                # Without this CDP call Browserbase does not sync downloaded files.
                try:
                    cdp = await self.context.new_cdp_session(self.page)
                    await cdp.send(
                        "Browser.setDownloadBehavior",
                        {
                            "behavior": "allow",
                            "downloadPath": "downloads",
                            "eventsEnabled": True,
                        },
                    )
                    logger.info("stagehand.download_capture.configured", component="stagehand")
                except Exception as cdp_err:
                    logger.warning(
                        "stagehand.download_capture.failed",
                        component="stagehand",
                        error=str(cdp_err),
                    )

                logger.info("stagehand.playwright.connected", component="stagehand")

            else:
                # ===== LOCAL MODE =====
                logger.info(
                    "stagehand.session.start",
                    component="stagehand",
                    mode="local",
                    session_id=self.session_id,
                )

                # Launch local Chrome browser
                self.browser = await self.playwright.chromium.launch(
                    headless=self.headless, args=["--disable-blink-features=AutomationControlled"]
                )

                # Create context and page
                self.context = await self.browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    accept_downloads=True,
                )
                self.page = await self.context.new_page()

                logger.info("stagehand.playwright.local_chrome", component="stagehand")

            # Setup dialog handler (both modes)
            await self._setup_dialog_handler(self.page)

            self._initialized = True
            mode = "Browserbase" if self.use_browserbase else "Local"
            logger.info(
                "stagehand.session.started",
                component="stagehand",
                mode=mode,
                headless=self.headless,
            )

        except Exception as e:
            logger.error("stagehand.init.failed", component="stagehand", error=str(e))
            raise

    async def _release_browserbase_session(self, session_id: str) -> None:
        """Hand a Browserbase session back to the pool. Best-effort; never raises.

        Until this lands, the session keeps its concurrency slot until the
        project's own timeout expires — up to an hour on a project configured
        with a long defaultTimeout. Takes the id as an argument rather than
        reading self, so the caller can claim it atomically first.
        """
        for attempt in range(_RELEASE_ATTEMPTS):
            try:
                async with httpx.AsyncClient(
                    timeout=_RELEASE_HTTP_TIMEOUT_SECONDS, verify=False
                ) as _bb_http:
                    resp = await _bb_http.post(
                        f"https://api.browserbase.com/v1/sessions/{session_id}",
                        headers={
                            "X-BB-API-Key": os.getenv("BROWSERBASE_API_KEY") or "",
                            "Content-Type": "application/json",
                        },
                        json={
                            "projectId": os.getenv("BROWSERBASE_PROJECT_ID") or "",
                            "status": "REQUEST_RELEASE",
                        },
                    )
                # A rejected release still holds the slot — never log it as ended.
                resp.raise_for_status()
                logger.info("stagehand.session.ended", component="stagehand", session_id=session_id)
                return
            except Exception as e:
                last = attempt == _RELEASE_ATTEMPTS - 1
                logger.warning(
                    "stagehand.session.end_warning",
                    component="stagehand",
                    session_id=session_id,
                    attempt=attempt + 1,
                    give_up=last,
                    error=str(e),
                )
                if last:
                    return
                with contextlib.suppress(Exception):
                    await asyncio.sleep(_RELEASE_RETRY_DELAY_SECONDS)

    def _claim_session_for_release(self) -> str | None:
        """Take ownership of the session id, or None if there is nothing to release.

        Clearing the attribute in the same synchronous step that reads it means
        two concurrent close() calls cannot both fire a release for one session.
        """
        if not (self.session_id and self.use_browserbase):
            return None
        session_id, self.session_id = self.session_id, None
        return session_id

    async def close(self) -> None:
        """Close the browser session and cleanup all resources (v3)."""
        # No-op only when nothing was ever allocated. Both `_initialized` and
        # `session_id` are set late in initialize() — the Browserbase POST at :283
        # can 429 with the local Playwright driver already started at :267, and
        # gating on those two alone leaked that driver on every 429.
        if not (
            self._initialized or self.session_id or self.playwright or self.browser or self.page
        ):
            return

        # Claimed before the first await: cancellation from here on must still
        # release, and a concurrent close() must not double-fire.
        session_id = self._claim_session_for_release()

        # Teardown used to emit nothing until its first close RETURNED, so an await
        # that hung here left an hour of unbroken silence in the logs. Mark the entry,
        # and reset the per-close degradation counters while we are here.
        self._teardown_timeouts = 0
        self._teardown_failures = 0
        logger.info(
            "stagehand.session.close_started",
            component="stagehand",
            window_count=len(self.windows),
        )

        try:
            # Give any in-flight modal alerts a brief chance to finish, then drop
            # them — best-effort, and they must not keep the loop alive. Inside the
            # protected region: CancelledError raised here used to skip teardown
            # and the release entirely.
            if self._alert_tasks:
                pending = list(self._alert_tasks)
                try:
                    await asyncio.wait(pending, timeout=5)
                except Exception as e:
                    logger.debug(
                        "stagehand.alert_tasks.drain_warning", component="stagehand", error=str(e)
                    )
                for t in pending:
                    if not t.done():
                        t.cancel()

            # Close all tracked windows
            await self._close_all_windows()

            # Every local stage below is TIME bounded as well as exception guarded.
            # The two are not interchangeable: a `try/except` cannot advance past an
            # await that never returns, which is exactly how a wedged popup held this
            # function for 2923.8s. The Browserbase release in the `finally` is
            # deliberately excluded — it owns its own retry and grace budget.
            if self.page:
                await self._close_step(
                    self.page.close(),
                    timeout_slug="stagehand.page.close_timeout",
                    error_slug="stagehand.page.close_failed",
                )

            if self.context and not self.use_browserbase:
                # Only close context in local mode (Browserbase owns the context)
                await self._close_step(
                    self.context.close(),
                    timeout_slug="stagehand.context.close_timeout",
                    error_slug="stagehand.context.close_failed",
                )

            if self.browser:
                await self._close_step(
                    self.browser.close(),
                    timeout_slug="stagehand.browser.close_timeout",
                    error_slug="stagehand.browser.close_failed",
                )

            if self.playwright:
                # NOT _close_step: wait_for cancels on timeout, and stop_async() is
                # `request_stop(); await wait_until_stopped(); cleanup()`. Cancelling
                # it cancels the transport's _stopped_future — whose background task
                # then calls set_result() on it — and skips cleanup() entirely. So
                # bound the WAIT instead of the work: asyncio.wait leaves the task
                # running when it expires.
                stop_task = asyncio.ensure_future(self.playwright.stop())
                # Rooted module-side, exactly like _PENDING_RELEASES: a stop that
                # outlives this frame is otherwise referenced only by it, and dies
                # with "Task was destroyed but it is pending!" once it unwinds.
                _PENDING_DRIVER_STOPS.add(stop_task)
                stop_task.add_done_callback(_PENDING_DRIVER_STOPS.discard)
                done, _pending = await asyncio.wait({stop_task}, timeout=self.teardown_timeout_s)
                if done:
                    # A task that finished by raising is in `done`, so this branch is
                    # the one that runs — and the exception must be retrieved here or
                    # asyncio reports it as never retrieved at GC and the teardown
                    # reads as clean.
                    stop_error = stop_task.exception()
                    if stop_error is not None:
                        self._teardown_failures += 1
                        logger.warning(
                            "stagehand.playwright.stop_failed",
                            component="stagehand",
                            error=str(stop_error),
                        )
                else:
                    self._teardown_timeouts += 1
                    logger.warning(
                        "stagehand.playwright.stop_timeout",
                        component="stagehand",
                        timeout_s=self.teardown_timeout_s,
                    )
                    # It outlives this frame, so nothing here will ever await it.
                    # Consume a late exception at completion — otherwise the same
                    # "never retrieved" noise arrives minutes later, detached from
                    # the run that caused it.
                    stop_task.add_done_callback(_log_late_driver_stop)

            # A teardown that timed out or raised anywhere is not a clean teardown.
            # Saying which on this line is what makes "how often, and how" answerable
            # — the incident looked healthy precisely because this line could not
            # tell the difference.
            degraded_reason = self._teardown_degraded_reason
            if degraded_reason:
                logger.warning(
                    "stagehand.session.closed",
                    component="stagehand",
                    degraded=True,
                    degraded_reason=degraded_reason,
                    timeouts=self._teardown_timeouts,
                    failures=self._teardown_failures,
                )
            else:
                logger.info("stagehand.session.closed", component="stagehand")

        except Exception as e:
            logger.warning("stagehand.cleanup.error", component="stagehand", error=str(e))
        finally:
            try:
                # In the finally so no failure above can strand the remote session,
                # and shielded so a cancelled run still hands the slot back —
                # cancellation is precisely when the session would otherwise sit
                # out its full timeout. CancelledError still propagates.
                if session_id:
                    release = asyncio.ensure_future(self._release_browserbase_session(session_id))
                    # Rooted before the first await: if a cancelled close() gives
                    # up waiting, the task must outlive this frame and still run.
                    _PENDING_RELEASES.add(release)
                    release.add_done_callback(_PENDING_RELEASES.discard)
                    try:
                        # Bounded on every path, cancelled or not: teardown must
                        # not sit through the release's full retry budget. Timing
                        # out cancels the shield wrapper, never the rooted task,
                        # so the release finishes in the background regardless.
                        await asyncio.wait_for(
                            asyncio.shield(release), timeout=_RELEASE_GRACE_SECONDS
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "stagehand.session.release_slow",
                            component="stagehand",
                            session_id=session_id,
                            waited_seconds=_RELEASE_GRACE_SECONDS,
                        )
            finally:
                self._initialized = False
                self.page = None
                self.context = None
                self.browser = None
                self.playwright = None

    @traceable(name="stagehand_goto", run_type="tool")
    async def navigate_to(self, url: str, wait_for: str = "load") -> Dict[str, Any]:
        """
        Navigate to a URL.

        Args:
            url: The URL to navigate to
            wait_for: Page load state to wait for (domcontentloaded, load, networkidle)

        Returns:
            Result dictionary with status and details

        Raises:
            ValueError: If URL format is invalid
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        # Validate URL format
        if not url or not isinstance(url, str):
            raise ValueError(f"Invalid URL: {url}")

        url = url.strip()
        if not (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("file://")
            or url.startswith("about:")
        ):
            raise ValueError(
                f"URL must start with http://, https://, file://, or about: - got: {url}"
            )

        # Validate wait_for state
        valid_states = ["domcontentloaded", "load", "networkidle"]
        if wait_for not in valid_states:
            logger.warning(
                "stagehand.navigate.invalid_wait_for", component="stagehand", wait_for=wait_for
            )
            wait_for = "load"

        try:
            await self.page.goto(url, wait_until=wait_for)
            logger.info("stagehand.navigate.done", component="stagehand", wait_for=wait_for)
            return {"status": "success", "action_type": "goto", "url": url, "wait_for": wait_for}
        except Exception as e:
            logger.error(
                "stagehand.navigate.failed", component="stagehand", error=str(e), handled=False
            )
            return {"status": "error", "message": str(e), "action_type": "goto", "url": url}

    @traceable(name="stagehand_act", run_type="tool")
    async def perform_action(self, action_text: str, context: str = None) -> Dict[str, Any]:
        """
        Perform a browser action using Stagehand v3 API.

        Args:
            action_text: The action to perform (e.g., "Click on the search box")
            context: Optional window context name (e.g., "dashboard")

        Returns:
            Result dictionary with status and details
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            # Switch to target window if specified
            if context:
                await self._switch_to_window_async(context)

            # v3: Call Stagehand API with session_id
            result = self.api_client.sessions.act(
                id=self.session_id,
                input=action_text,
            )

            logger.info("stagehand.action.done", component="stagehand", action=action_text[:50])
            return {
                "status": "success",
                "action_type": "act",
                "action": action_text,
                "result": result.data.result if hasattr(result.data, "result") else None,
            }
        except Exception as e:
            logger.error(
                "stagehand.action.failed", component="stagehand", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "act",
                "action": action_text,
            }

    async def take_screenshot(self) -> Optional[bytes]:
        """
        Take a screenshot of the current page.

        Returns:
            Screenshot bytes or None if failed
        """
        if not self._initialized or not self.page:
            logger.warning("stagehand.screenshot.no_page", component="stagehand")
            return None

        try:
            screenshot_capture = ScreenshotCapture(self.page)
            return await screenshot_capture.capture_screenshot()
        except Exception as e:
            logger.error(
                "stagehand.screenshot.failed", component="stagehand", error=str(e), handled=False
            )
            return None

    @traceable(name="stagehand_observe", run_type="tool")
    async def observe_elements(
        self, instruction: str, context: str = None, use_computer_use: bool = False
    ) -> Dict[str, Any]:
        """
        Observe and find elements on the page using Stagehand v3 API.

        Args:
            instruction: Description of what elements to find
            context: Optional window context name (e.g., "dashboard")
            use_computer_use: If True, use Computer Use model (Note: May not be supported in v3)

        Returns:
            Result dictionary with found elements
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            # Switch to target window if specified
            if context:
                await self._switch_to_window_async(context)

            # v3: Call Stagehand API with session_id
            result = self.api_client.sessions.observe(
                id=self.session_id,
                instruction=instruction,
            )

            elements = result.data.result if hasattr(result.data, "result") else []
            logger.info("stagehand.observe.done", component="stagehand", count=len(elements))

            # Convert elements to serializable format
            serializable_elements = []
            for elem in elements:
                if hasattr(elem, "to_dict"):
                    serializable_elements.append(elem.to_dict())
                elif hasattr(elem, "__dict__"):
                    serializable_elements.append({k: str(v) for k, v in elem.__dict__.items()})
                else:
                    serializable_elements.append(str(elem))

            return {
                "status": "success",
                "action_type": "observe",
                "instruction": instruction,
                "elements_found": len(elements),
                "elements": serializable_elements,
            }
        except Exception as e:
            logger.error(
                "stagehand.observe.failed", component="stagehand", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "observe",
                "instruction": instruction,
            }

    @traceable(name="stagehand_extract", run_type="tool")
    async def extract_data(
        self,
        instruction: str,
        schema: Optional[Dict[str, Any]] = None,
        context: str = None,
        use_computer_use: bool = False,
    ) -> Dict[str, Any]:
        """
        Extract structured data from the page using Stagehand v3 API.

        Args:
            instruction: Description of what data to extract
            schema: JSON schema defining the expected extraction structure (REQUIRED for v3)
            context: Optional window context name (e.g., "dashboard")
            use_computer_use: If True, use Computer Use model (fallback to Flash)

        Returns:
            Result dictionary with extracted data
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        if not schema:
            logger.warning(
                "stagehand.extract.no_schema",
                component="stagehand",
                detail="Stagehand v3 requires explicit schemas",
            )
            # Return error for now - we'll update the SOPs to provide schemas
            return {
                "status": "error",
                "message": "Schema is required for Stagehand v3 extract operations",
                "action_type": "extract",
                "instruction": instruction,
            }

        try:
            # Switch to target window if specified
            if context:
                await self._switch_to_window_async(context)
                logger.info(
                    "stagehand.extract.window_context", component="stagehand", window_name=context
                )
            else:
                logger.info("stagehand.extract.main_window", component="stagehand")

            # Log extraction start
            logger.info("stagehand.extract.start", component="stagehand")
            if len(instruction) > 100:
                logger.info(
                    "stagehand.extract.instruction",
                    component="stagehand",
                    instruction=instruction[:100],
                )
            else:
                logger.info(
                    "stagehand.extract.instruction", component="stagehand", instruction=instruction
                )

            # Take screenshot for debugging (from correct window)
            try:
                if context and context in self.windows:
                    screenshot_bytes = await self.windows[context].screenshot()
                else:
                    screenshot_bytes = await self.page.screenshot()
                screenshot_size_kb = len(screenshot_bytes) / 1024
                logger.info(
                    "stagehand.extract.screenshot_captured",
                    component="stagehand",
                    size_kb=screenshot_size_kb,
                )

                # Save screenshot to file with date-wise organization
                from datetime import datetime
                from pathlib import Path

                # Create date-based artifacts directory (e.g., artifacts/2025-12-05/)
                today = datetime.now().strftime("%Y-%m-%d")
                screenshot_dir = Path(os.getcwd()) / "artifacts" / today
                screenshot_dir.mkdir(parents=True, exist_ok=True)

                # Generate filename with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                context_name = f"_{context}" if context else ""
                screenshot_filename = f"screenshot_{timestamp}{context_name}.png"
                screenshot_path = screenshot_dir / screenshot_filename

                # Save screenshot
                screenshot_path.write_bytes(screenshot_bytes)
                logger.info(
                    "stagehand.extract.screenshot_saved",
                    component="stagehand",
                    screenshot_path=str(screenshot_path),
                )

            except Exception as screenshot_error:
                logger.warning(
                    "stagehand.extract.screenshot_failed",
                    component="stagehand",
                    error=str(screenshot_error),
                )

            # v3: Call Stagehand API with session_id and schema
            result = self.api_client.sessions.extract(
                id=self.session_id,
                instruction=instruction,
                schema=schema,  # JSON schema is required
            )

            # Extract data from response
            extracted_data = result.data.result if hasattr(result.data, "result") else {}

            # Convert result to serializable format
            if hasattr(extracted_data, "model_dump"):
                extracted_data = extracted_data.model_dump()
            elif hasattr(extracted_data, "dict"):
                extracted_data = extracted_data.dict()
            elif hasattr(extracted_data, "__dict__"):
                extracted_data = extracted_data.__dict__
            elif not isinstance(extracted_data, dict):
                extracted_data = {"raw_extraction": str(extracted_data)}

            # Validate extraction
            is_empty = False
            if isinstance(extracted_data, dict):
                # Check if extraction is empty or has minimal data
                if not extracted_data or len(str(extracted_data)) < 10:
                    is_empty = True
                    logger.warning("stagehand.extract.empty", component="stagehand")

            if not is_empty:
                logger.info("stagehand.extract.done", component="stagehand")
                # Log a preview of the extraction
                preview = str(extracted_data)[:300]
                logger.info("stagehand.extract.preview", component="stagehand", preview=preview)

            return {
                "status": "success",
                "action_type": "extract",
                "instruction": instruction,
                "extraction": extracted_data,
                "is_empty": is_empty,
            }
        except Exception as e:
            logger.error(
                "stagehand.extract.failed", component="stagehand", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "extract",
                "instruction": instruction,
            }

    async def wait(self, seconds: float) -> None:
        """
        Wait for a specified number of seconds.

        Args:
            seconds: Number of seconds to wait
        """
        await asyncio.sleep(seconds)

    async def _setup_dialog_handler(self, page) -> None:
        """
        Set up dialog handler for a Playwright page.
        Handles alert(), confirm(), and prompt() dialogs.

        Args:
            page: Playwright page object to attach handler to
        """

        async def handle_dialog(dialog):
            dialog_type = dialog.type  # 'alert', 'confirm', 'prompt', 'beforeunload'
            message = dialog.message

            logger.info(
                "stagehand.dialog.detected",
                component="stagehand",
                dialog_type=dialog_type,
                message=message[:100],
            )

            # Auto-accept all dialogs by default.
            # Wrapped in try/except because Browserbase (or Playwright internals) may
            # auto-dismiss the dialog before our async handler runs, causing accept()
            # to throw. Without this guard the unhandled exception leaves the dialog
            # open, blocking all subsequent page interaction.
            try:
                await dialog.accept()
                logger.info(
                    "stagehand.dialog.accepted", component="stagehand", dialog_type=dialog_type
                )
            except Exception as e:
                logger.warning(
                    "stagehand.dialog.accept_failed",
                    component="stagehand",
                    dialog_type=dialog_type,
                    error=str(e),
                )
                try:
                    await dialog.dismiss()
                    logger.info(
                        "stagehand.dialog.dismissed_fallback",
                        component="stagehand",
                        dialog_type=dialog_type,
                    )
                except Exception as e2:
                    logger.warning(
                        "stagehand.dialog.dismiss_failed",
                        component="stagehand",
                        dialog_type=dialog_type,
                        error=str(e2),
                    )

        # Register the dialog handler
        page.on("dialog", handle_dialog)
        logger.debug("stagehand.dialog.handler_registered", component="stagehand")

    async def _close_all_windows(self) -> None:
        """Close all tracked windows, each under its own time bound.

        One window that will not close must not strand the ones behind it: the
        LogiSys checklist PDF popup never acknowledged its close and held this loop
        until the session expired. The bound is therefore per window and the cost is
        linear in the number tracked — a single budget for the whole loop would
        reproduce the stranding it exists to prevent.

        Inspection stays inside the per-window guard as well: a raise from
        is_closed() must not escape into close()'s outer handler, which would skip
        every remaining stage.
        """
        try:
            for window_name, window in list(self.windows.items()):
                try:
                    if not window or window.is_closed():
                        continue
                except Exception as e:
                    self._teardown_failures += 1
                    logger.warning(
                        "stagehand.window.inspect_failed",
                        component="stagehand",
                        window_name=window_name,
                        error=str(e),
                    )
                    continue
                closed = await self._close_step(
                    window.close(),
                    timeout_slug="stagehand.window.close_timeout",
                    error_slug="stagehand.window.close_failed",
                    window_name=window_name,
                )
                if closed:
                    logger.info(
                        "stagehand.window.closed", component="stagehand", window_name=window_name
                    )
        finally:
            self.windows.clear()

    # =========================================================================
    # PLAYWRIGHT METHODS (0 LLM calls - Direct browser automation)
    # =========================================================================

    @traceable(name="playwright_fill", run_type="tool")
    async def fill_field(
        self, selector: str, value: str, context: str = None, iframe: str = None
    ) -> Dict[str, Any]:
        """
        Fill an input field using Playwright (0 LLM calls).

        Args:
            selector: CSS selector for the input field
            value: Text to fill
            context: Optional window context name (e.g., "dashboard")
            iframe: Optional CSS selector for iframe containing the element

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            page = self.get_page_context(context)

            # If filling inside an iframe, use frame locator
            if iframe:
                logger.info("playwright.fill_iframe", component="playwright", iframe=iframe)
                frame_locator = page.frame_locator(iframe)
                element_locator = frame_locator.locator(selector)
                await element_locator.fill(value, timeout=10000)
                return {
                    "status": "success",
                    "action_type": "fill",
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                }
            else:
                # Regular fill
                await page.fill(selector, value, timeout=10000)
                return {
                    "status": "success",
                    "action_type": "fill",
                    "selector": selector,
                    "context": context or "main",
                }
        except Exception as e:
            logger.error(
                "playwright.fill.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "fill",
                "selector": selector,
            }

    async def type_field(
        self, selector: str, value: str, context: str = None, iframe: str = None, delay: int = 100
    ) -> Dict[str, Any]:
        """
        Type into an input field slowly using Playwright (0 LLM calls).
        Types character-by-character with delay - useful for triggering autosuggest.

        Args:
            selector: CSS selector for the input field
            value: Text to type
            context: Optional window context name (e.g., "dashboard")
            iframe: Optional CSS selector for iframe containing the element
            delay: Delay in milliseconds between keystrokes (default: 100ms)

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            page = self.get_page_context(context)

            # If typing inside an iframe, use frame locator
            if iframe:
                logger.info(
                    "playwright.type_iframe",
                    component="playwright",
                    iframe=iframe,
                    delay_ms=delay,
                )
                frame_locator = page.frame_locator(iframe)
                element_locator = frame_locator.locator(selector)
                # Force click to bypass actionability checks in iframes (especially in Browserbase)
                await element_locator.click(timeout=10000, force=True)  # Focus first
                await element_locator.type(value, delay=delay, timeout=30000)
                return {
                    "status": "success",
                    "action_type": "type",
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                    "delay": delay,
                }
            else:
                # Regular type
                logger.info("playwright.type", component="playwright", delay_ms=delay)
                await page.click(selector, timeout=5000)  # Focus first
                await page.type(selector, value, delay=delay, timeout=30000)
                return {
                    "status": "success",
                    "action_type": "type",
                    "selector": selector,
                    "context": context or "main",
                    "delay": delay,
                }
        except Exception as e:
            logger.error(
                "playwright.type.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "type",
                "selector": selector,
            }

    @traceable(name="playwright_upload_file", run_type="tool")
    async def upload_file(
        self, selector: str, file_path: str, context: str = None, iframe: str = None
    ) -> Dict[str, Any]:
        """
        Upload a file using Playwright's set_input_files (0 LLM calls).

        Args:
            selector: CSS selector for the file input element
            file_path: Absolute path to the file to upload
            context: Optional window context name (e.g., "csv_upload_popup")
            iframe: Optional CSS selector for iframe containing the element

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            page = self.get_page_context(context)
            file_name = os.path.basename(file_path)

            # If uploading inside an iframe, use frame locator
            if iframe:
                logger.info("playwright.upload_iframe", component="playwright", iframe=iframe)
                frame_locator = page.frame_locator(iframe)
                await frame_locator.locator(selector).set_input_files(file_path, timeout=10000)
                return {
                    "status": "success",
                    "action_type": "upload_file",
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                    "file_name": file_name,
                    "file_path": file_path,
                }
            else:
                # Regular upload
                logger.info("playwright.upload", component="playwright", file_name=file_name)
                await page.locator(selector).set_input_files(file_path, timeout=10000)
                return {
                    "status": "success",
                    "action_type": "upload_file",
                    "selector": selector,
                    "context": context or "main",
                    "file_name": file_name,
                    "file_path": file_path,
                }
        except Exception as e:
            logger.error(
                "playwright.upload.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "upload_file",
                "selector": selector,
            }

    async def _wait_for_overlay_clear(self, page, timeout_ms: int = 30000) -> None:
        """Wait for the ASP.NET UpdatePanel loading overlay to disappear.

        `#updateProgressContainer` is a full-screen (1920x1080) iframe LogiSys
        shows during partial postbacks; while visible it intercepts pointer
        events and makes clicks time out. If it's absent the wait resolves
        immediately. Best-effort: never raises.
        """
        try:
            el = await page.query_selector("#updateProgressContainer")
            if el is None:
                return
            await page.wait_for_selector(
                "#updateProgressContainer", state="hidden", timeout=self._scaled(timeout_ms)
            )
            logger.info("playwright.overlay.cleared", component="playwright")
        except Exception as e:
            logger.debug("playwright.overlay.wait_noop", component="playwright", error=str(e))

    async def _dismiss_blocking_modal(self, page, context: Optional[str] = None) -> Optional[dict]:
        """Detect a blocking ReactModal overlay (e.g. the Softlink payment-reminder nag,
        F1) before a click. For the KNOWN benign nag, dismiss it the intended way —
        click its own Continue/OK button (a real element click; verified live to remove
        the modal cleanly) — falling back to JS-hide only if it has no dismiss button
        or the click didn't clear it. An UNKNOWN full-viewport modal is LEFT IN PLACE
        (never clicked, never hidden), captured + alerted critically, so the click fails
        loudly rather than masking a must-act modal. Best-effort; never raises; a strict
        no-op (one bounded evaluate) when no such overlay exists."""
        try:
            # Bounded: a hung/navigating page must not stall every click (strict no-op).
            res = await asyncio.wait_for(page.evaluate(_DETECT_BLOCKING_MODAL_JS), timeout=5)
        except Exception as e:
            # Timeout / navigating page / destroyed context — same best-effort contract
            # as _wait_for_overlay_clear: let the click itself decide.
            logger.debug("playwright.modal_guard.noop", component="playwright", error=str(e))
            return None
        if not res or not res.get("found"):
            return None  # ── clean path exits here (one bounded evaluate, no waiting) ──

        text = " | ".join(t for t in res.get("texts", []) if t) or "(no text captured)"
        has_controls = bool(res.get("has_controls"))
        has_dismiss_button = bool(res.get("has_dismiss_button"))
        capture_errors = int(res.get("capture_errors", 0))
        is_known_nag = _is_known_payment_nag(text)

        # Only the KNOWN benign nag is acted on. Unknown modals are never touched:
        # leave in place, alert critical=True (→ HitlRequest), let the click fail loudly.
        method = "left_in_place"
        if is_known_nag:
            if has_dismiss_button:
                try:
                    r = await asyncio.wait_for(page.evaluate(_CLICK_DISMISS_MODAL_JS), timeout=5)
                    method = (
                        "continue_click" if (r and r.get("gone")) else "continue_click_incomplete"
                    )
                except Exception as e:
                    logger.debug(
                        "playwright.modal_guard.dismiss_noop", component="playwright", error=str(e)
                    )
            # Fallback: no dismiss button, or the click didn't clear the overlay.
            if method != "continue_click":
                try:
                    hidden = await asyncio.wait_for(
                        page.evaluate(_HIDE_BLOCKING_MODAL_JS), timeout=5
                    )
                    if hidden:
                        method = "hidden"
                except Exception as e:
                    logger.debug(
                        "playwright.modal_guard.hide_noop", component="playwright", error=str(e)
                    )
        critical = not is_known_nag

        record = {
            "text": text,
            "has_controls": has_controls,
            "has_dismiss_button": has_dismiss_button,
            "known_nag": is_known_nag,
            "method": method,
            "capture_errors": capture_errors,
            "url": page.url,
            "context": context or "main",
            "at": datetime.now(timezone.utc).isoformat(),
        }
        self.dismissed_modals.append(record)
        logger.warning(  # un-deduped, every time — per-run forensic record
            "playwright.blocking_modal.detected",
            component="playwright",
            found=res["found"],
            method=method,
            has_controls=has_controls,
            has_dismiss_button=has_dismiss_button,
            known_nag=is_known_nag,
            capture_errors=capture_errors,
            window=context or "main",
            url=page.url,
            modal_text=text,
        )

        key = hashlib.sha256(text.encode()).hexdigest()  # dedup key only (not a signature)
        now = datetime.now(timezone.utc)
        # Prune expired entries so the module-level cache can't grow unbounded.
        for k in [
            k for k, ts in _MODAL_ALERT_DEDUP.items() if now - ts >= _MODAL_ALERT_DEDUP_WINDOW
        ]:
            _MODAL_ALERT_DEDUP.pop(k, None)
        last = _MODAL_ALERT_DEDUP.get(key)
        if last is None or now - last >= _MODAL_ALERT_DEDUP_WINDOW:
            _MODAL_ALERT_DEDUP[key] = now  # claim the window BEFORE scheduling the send
            self._fire_modal_alert(text, method, is_known_nag, critical, page.url, context)
        return res

    def _fire_modal_alert(
        self,
        text: str,
        method: str,
        is_known_nag: bool,
        critical: bool,
        url: str,
        context: Optional[str] = None,
    ) -> None:
        """Fire-and-forget tech alert — NEVER blocks the click. A down Outlook/Slack
        cannot delay or fail the click; failures are swallowed + logged. Deliberately
        NOT wrapped in a timeout: send_tech_alert persists the critical HitlRequest
        AFTER the email/Slack sends, so a timeout here could cancel that durable record
        while the 6h dedup window was already claimed. Fire-and-forget already
        guarantees the click is never blocked, however slow the send is."""
        # Resolve the replay URL in this (method) scope — referencing self.* inside
        # the nested closure confuses the type checker. session_id is only set in
        # Browserbase mode, so its presence alone implies a replay URL exists.
        replay = (
            f" | replay: https://www.browserbase.com/sessions/{self.session_id}"
            if self.session_id
            else ""
        )
        _verb = {
            "continue_click": "Dismissed (clicked its Continue button)",
            "continue_click_incomplete": "Clicked Continue but overlay lingered — then hid",
            "hidden": "Dismissed (JS-hid — no usable dismiss button)",
            "left_in_place": "LEFT IN PLACE (unknown modal, not touched)",
        }.get(method, method)

        async def _send() -> None:
            try:
                from .._compat import send_tech_alert

                prefix = (
                    "" if is_known_nag else "UNKNOWN blocking modal (not the known payment nag) — "
                )
                await send_tech_alert(
                    {
                        "pipeline": "browser",
                        "step": "blocking-modal-guard",
                        "error": (
                            f"{prefix}{_verb} — blocking modal on {url} "
                            f"(window={context or 'main'}){replay}: «{text}»"
                        ),
                        "critical": critical,
                    }
                )
            except Exception as e:
                logger.error(
                    "playwright.modal_guard.alert_failed",
                    component="playwright",
                    error=str(e),
                    handled=False,
                )

        task = asyncio.create_task(_send())
        self._alert_tasks.add(task)  # keep a ref so it isn't GC'd
        task.add_done_callback(self._alert_tasks.discard)

    @traceable(name="playwright_click", run_type="tool")
    async def click_element(
        self,
        selector: str,
        context: str = None,
        force_click: bool = False,
        wait_for_new_window: bool = False,
        new_window_name: str = None,
        iframe: str = None,
        prep_script: str = None,
    ) -> Dict[str, Any]:
        """
        Click an element using Playwright (0 LLM calls).

        Args:
            selector: CSS selector for the element
            context: Optional window context name
            force_click: Force click even if element is not actionable
            wait_for_new_window: Wait for a new window to open after clicking
            new_window_name: Name to assign to the new window
            iframe: Optional CSS selector for iframe containing the element
            prep_script: Optional JS re-asserted on the page right before the
                click (after the overlay-wait). Used to re-show hover-dependent
                menu items that collapse while the ASP.NET overlay is clearing.

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            page = self.get_page_context(context)

            # Hardened click preamble (applies to every click path):
            #  1. wait for the ASP.NET UpdatePanel loading overlay to clear — it is
            #     a full-screen iframe that otherwise intercepts pointer events.
            #  2. dismiss a blocking ReactModal overlay (F1: Softlink payment nag)
            #     that otherwise intercepts pointer events over the menu — best-effort,
            #     never silent (logs + alerts), no-op when no such overlay is present.
            #  3. re-assert prep_script (e.g. re-show a hover menu item that
            #     collapsed while the overlay was clearing) right before clicking.
            await self._wait_for_overlay_clear(page)
            await self._dismiss_blocking_modal(page, context)
            if prep_script:
                try:
                    result = await page.evaluate(prep_script)
                    logger.info(
                        "playwright.prep_script.reasserted",
                        component="playwright",
                        result=result,
                    )
                except Exception as e:
                    logger.warning(
                        "playwright.prep_script.failed", component="playwright", error=str(e)
                    )

            # If clicking inside an iframe AND waiting for new window
            if iframe and wait_for_new_window:
                logger.info(
                    "playwright.click_iframe_new_window",
                    component="playwright",
                    iframe=iframe,
                )

                # Validate context state for new window handling
                if not self.context:
                    raise RuntimeError("Browser context is not available")

                # Click inside iframe and capture the popup via the browser's page
                # event. The onclick fires window.open()+postback, which makes
                # page.click() time out even though the window opens — so a click
                # timeout is NOT fatal as long as expect_page() captures the popup.
                frame_locator = page.frame_locator(iframe)
                element_locator = frame_locator.locator(selector)
                # Single click with one continuous, peak-hour-scaled expect_page
                # window. NOT retried: re-clicking a window.open/postback control
                # after the listener closed can fire a duplicate popup/postback
                # (double-submit). The scaled window is the fix — the reproduced
                # slow open (~18s) fits comfortably inside it.
                try:
                    async with self.context.expect_page(
                        timeout=self._scaled(15000)
                    ) as new_page_info:
                        try:
                            await element_locator.click(
                                timeout=self._scaled(10000),
                                no_wait_after=True,
                                force=force_click,
                            )
                            logger.info("playwright.click_iframe.executed", component="playwright")
                        except PlaywrightTimeoutError:
                            logger.info(
                                "playwright.click_iframe.timeout_relying_on_event",
                                component="playwright",
                            )
                    new_window = await new_page_info.value
                    logger.info("playwright.new_window.captured", component="playwright")
                except PlaywrightTimeoutError:
                    logger.error("playwright.new_window.timeout", component="playwright")
                    raise RuntimeError("New window did not open within timeout")

                # Window captured = success. Register it BEFORE the load-state wait
                # so a slow popup can't lose it, and treat the load-state as
                # best-effort (popups can be slow to fire domcontentloaded on
                # Browserbase; the SOP's next step does the real load wait).
                window_name = new_window_name or "popup"
                self.windows[window_name] = new_window
                try:
                    await new_window.wait_for_load_state(
                        "domcontentloaded", timeout=self._scaled(10000)
                    )
                except PlaywrightTimeoutError:
                    logger.info(
                        "playwright.new_window.load_state_slow",
                        component="playwright",
                        window_name=window_name,
                    )
                # Best-effort: the window is already registered, so a hiccup bringing
                # it to front / sizing it (e.g. an odd PDF popup) must not drop it.
                try:
                    await new_window.bring_to_front()
                    await new_window.set_viewport_size({"width": 1920, "height": 1080})
                    await self._setup_dialog_handler(new_window)
                except Exception as setup_err:
                    logger.info(
                        "playwright.new_window.setup_best_effort",
                        component="playwright",
                        window_name=window_name,
                        error=str(setup_err),
                    )

                logger.info(
                    "playwright.new_window.opened",
                    component="playwright",
                    window_name=window_name,
                )

                return {
                    "status": "success",
                    "action_type": "click",
                    "selector": selector,
                    "iframe": iframe,
                    "new_window": window_name,
                    "context": context or "main",
                }

            # If clicking inside an iframe (without new window)
            elif iframe:
                logger.info("playwright.click_iframe", component="playwright", iframe=iframe)
                frame_locator = page.frame_locator(iframe)
                element_locator = frame_locator.locator(selector)

                # no_wait_after: postback/onclick controls must not block the click
                # call on a navigation wait.
                click_options = {"timeout": self._scaled(10000), "no_wait_after": True}
                if force_click:
                    click_options["force"] = True

                await element_locator.click(**click_options)
                return {
                    "status": "success",
                    "action_type": "click",
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                }

            # Handle new window scenario (without iframe)
            elif wait_for_new_window:
                new_window = await self._click_and_wait_for_window(
                    page, selector, force_click, new_window_name
                )
                return {
                    "status": "success",
                    "action_type": "click",
                    "selector": selector,
                    "new_window": new_window_name or "popup",
                    "context": context or "main",
                }
            else:
                # Regular click. no_wait_after: ASP.NET postback controls (e.g.
                # the Save button's fireElementEvent) otherwise hang the click on
                # a navigation wait until timeout.
                click_options = {"timeout": self._scaled(10000), "no_wait_after": True}
                if force_click:
                    click_options["force"] = True

                await page.click(selector, **click_options)
                return {
                    "status": "success",
                    "action_type": "click",
                    "selector": selector,
                    "context": context or "main",
                }
        except Exception as e:
            logger.error(
                "playwright.click.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "click",
                "selector": selector,
            }

    async def dblclick_element(
        self,
        selector: str,
        context: str = None,
        wait_for_new_window: bool = False,
        new_window_name: str = None,
        iframe: str = None,
        closes_window: bool = False,
    ) -> Dict[str, Any]:
        """Double-click an element via Playwright's native dblclick (counts as a user gesture)."""
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized.")

        try:
            page = self.get_page_context(context)

            if closes_window:
                # The double-click selects a row and closes the popup as a side effect
                # (e.g. LogiSys lookup popups post the selection back to the parent form,
                # then close). Playwright loses its target page mid-action and may raise
                # "Target page … has been closed" EVEN THOUGH the selection succeeded.
                # Success is asserted only on OBSERVED evidence — the target page actually
                # closing — never on the exception text, since a broad close-signature
                # match could otherwise mask a genuine teardown. no_wait_after avoids the
                # post-action navigation wait that the close trips on.
                target = page.frame_locator(iframe).locator(selector) if iframe else None
                close_error = None
                try:
                    if target is not None:
                        await target.dblclick(timeout=10000, no_wait_after=True)
                    else:
                        await page.dblclick(selector, timeout=10000, no_wait_after=True)
                except Exception as e:
                    if not _is_page_closed_error(e):
                        raise
                    # Looks like the popup closed; confirm via is_closed() before trusting it.
                    close_error = e

                # Confirm the popup actually closed — the proof the selection took effect.
                # (Covers both the clean-return path and a close still propagating.)
                window_closed = False
                for _ in range(15):
                    if page.is_closed():
                        window_closed = True
                        break
                    await asyncio.sleep(0.2)

                if not window_closed:
                    # Declared closes_window but the window is still open → the select did
                    # not take effect. Report a real failure instead of a false success.
                    message = (
                        f"dblclick on '{selector}' declared closes_window but "
                        f"'{context}' did not close"
                    )
                    if close_error is not None:
                        message += f"; close-like error not backed by a closed page: {close_error}"
                    logger.warning(
                        "playwright.dblclick.closes_window_still_open",
                        component="playwright",
                        context=context,
                        selector=selector,
                    )
                    return {
                        "status": "error",
                        "message": message,
                        "action_type": "dblclick",
                        "selector": selector,
                    }

                # Reclaim the dead window so later steps / retries don't resolve a
                # closed context.
                if context:
                    self.windows.pop(context, None)

                logger.info(
                    "playwright.dblclick.closed_window",
                    component="playwright",
                    context=context,
                )
                return {
                    "status": "success",
                    "action_type": "dblclick",
                    "selector": selector,
                    "closed_window": context,
                }

            if iframe and wait_for_new_window:
                # Capture page count before the action
                ctx_pages = self.context.pages
                if asyncio.iscoroutine(ctx_pages):
                    ctx_pages = await ctx_pages
                elif callable(ctx_pages):
                    ctx_pages = ctx_pages()
                    if asyncio.iscoroutine(ctx_pages):
                        ctx_pages = await ctx_pages
                initial_pages = len(ctx_pages)
                logger.info(
                    "playwright.dblclick.initial_page_count",
                    component="playwright",
                    count=initial_pages,
                )

                # ASP.NET grid row detaches from DOM on first click (postback), breaking element-based dblclick.
                # Resolve coords once; page.mouse.dblclick fires raw events immune to DOM mutations.
                # bounding_box() on frame_locator returns page-viewport coords — don't add the iframe offset.
                elem_box = await page.frame_locator(iframe).locator(selector).bounding_box()
                if elem_box is None:
                    return {
                        "status": "error",
                        "message": f"Element '{selector}' not visible in iframe",
                        "action_type": "dblclick",
                        "selector": selector,
                    }
                click_x = elem_box["x"] + elem_box["width"] / 2
                click_y = elem_box["y"] + elem_box["height"] / 2
                logger.info(
                    "playwright.dblclick_iframe.coords",
                    component="playwright",
                    x=click_x,
                    y=click_y,
                )
                await page.mouse.dblclick(click_x, click_y)
                logger.info("playwright.dblclick_iframe.executed", component="playwright")

                window_name = await self._wait_for_and_register_window(
                    initial_pages, new_window_name
                )
                return {
                    "status": "success",
                    "action_type": "dblclick",
                    "selector": selector,
                    "new_window": window_name,
                }

            elif iframe:
                frame_locator = page.frame_locator(iframe)
                await frame_locator.locator(selector).dblclick(timeout=self._scaled(10000))
                return {"status": "success", "action_type": "dblclick", "selector": selector}

            elif wait_for_new_window:
                ctx_pages = self.context.pages
                if asyncio.iscoroutine(ctx_pages):
                    ctx_pages = await ctx_pages
                elif callable(ctx_pages):
                    ctx_pages = ctx_pages()
                    if asyncio.iscoroutine(ctx_pages):
                        ctx_pages = await ctx_pages
                initial_pages = len(ctx_pages)

                await page.dblclick(selector, timeout=self._scaled(10000))
                window_name = await self._wait_for_and_register_window(
                    initial_pages, new_window_name
                )
                return {
                    "status": "success",
                    "action_type": "dblclick",
                    "selector": selector,
                    "new_window": window_name,
                }

            else:
                await page.dblclick(selector, timeout=self._scaled(10000))
                return {"status": "success", "action_type": "dblclick", "selector": selector}

        except Exception as e:
            logger.error(
                "playwright.dblclick.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "dblclick",
                "selector": selector,
            }

    @traceable(name="playwright_wait_for_selector", run_type="tool")
    @traceable(name="playwright_press", run_type="tool")
    async def press_key(
        self, key: str, selector: str = None, context: str = None, iframe: str = None
    ) -> Dict[str, Any]:
        """
        Press a keyboard key using Playwright (0 LLM calls).

        Args:
            key: Key to press (e.g., "Enter", "Tab", "Escape", "a", "A")
            selector: Optional CSS selector for element to press key on
            context: Optional window context name
            iframe: Optional CSS selector for iframe containing the element

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            page = self.get_page_context(context)

            # If pressing key inside an iframe
            if iframe:
                logger.info("playwright.press_iframe", component="playwright", iframe=iframe)
                frame_locator = page.frame_locator(iframe)

                if selector:
                    # Press key on specific element inside iframe
                    element_locator = frame_locator.locator(selector)
                    await element_locator.press(key, timeout=10000)
                else:
                    # Press key on iframe itself (page-level)
                    frame = await frame_locator.owner()
                    await frame.press(key)

                return {
                    "status": "success",
                    "action_type": "press",
                    "key": key,
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                }
            else:
                # Regular press (outside iframe)
                if selector:
                    # Press key on specific element
                    await page.locator(selector).press(key, timeout=10000)
                else:
                    # Press key on page
                    await page.keyboard.press(key)

                return {
                    "status": "success",
                    "action_type": "press",
                    "key": key,
                    "selector": selector,
                    "context": context or "main",
                }
        except Exception as e:
            logger.error(
                "playwright.press.failed", component="playwright", error=str(e), handled=False
            )
            return {
                "status": "error",
                "message": str(e),
                "action_type": "press",
                "key": key,
            }

    @traceable(name="playwright_wait_for_selector", run_type="tool")
    async def wait_for_element(
        self,
        selector: str,
        timeout: int = 10000,
        context: str = None,
        iframe: str = None,
        state: str = "visible",
    ) -> Dict[str, Any]:
        """
        Wait for an element to appear using Playwright (0 LLM calls).

        Args:
            selector: CSS selector to wait for
            timeout: Timeout in milliseconds
            context: Optional window context name
            iframe: Optional CSS selector for iframe containing the element

        Returns:
            Result dictionary with status
        """
        if not self._initialized:
            raise RuntimeError("Stagehand client not initialized. Call initialize() first.")

        try:
            # Scale the YAML step timeout for peak-hour reliability: LogiSys popups
            # (e.g. the job-details Save button) can take well over the literal 15s
            # to render around the 2-3pm IST peak, and this wait was previously the
            # one nav timeout SOP_TIMEOUT_SCALE did not reach. Set SOP_TIMEOUT_SCALE=1.0
            # to restore the literal value.
            timeout = self._scaled(timeout)
            page = self.get_page_context(context)

            # If waiting for element inside an iframe, use frame locator
            if iframe:
                logger.info("playwright.wait_for_iframe", component="playwright", iframe=iframe)
                frame_locator = page.frame_locator(iframe)
                element_locator = frame_locator.locator(selector)
                await element_locator.wait_for(state=state, timeout=timeout)
                return {
                    "status": "success",
                    "action_type": "wait_for_selector",
                    "selector": selector,
                    "iframe": iframe,
                    "context": context or "main",
                }
            else:
                # Regular wait
                await page.wait_for_selector(selector, state=state, timeout=timeout)
                return {
                    "status": "success",
                    "action_type": "wait_for_selector",
                    "selector": selector,
                    "context": context or "main",
                }
        except Exception as e:
            # A wait timeout is the classic symptom of a blocking LogiSys modal
            # sitting over the target (e.g. the Softlink payment nag). Check once so
            # the outcome is accurate, not a bare timeout: dismiss the KNOWN nag and
            # retry the wait once; surface an UNKNOWN modal's text; else honest timeout.
            err_msg = str(e)
            try:
                page = self.get_page_context(context)
                modal = await self._dismiss_blocking_modal(page, context)
            except Exception:
                modal = None
            if modal:
                last = self.dismissed_modals[-1] if self.dismissed_modals else {}
                if last.get("method") in ("continue_click", "hidden"):
                    try:
                        page = self.get_page_context(context)
                        if iframe:
                            await (
                                page.frame_locator(iframe)
                                .locator(selector)
                                .wait_for(state=state, timeout=timeout)
                            )
                        else:
                            await page.wait_for_selector(selector, state=state, timeout=timeout)
                        logger.info(
                            "playwright.wait_for_selector.recovered_after_modal",
                            component="playwright",
                            selector=selector,
                        )
                        return {
                            "status": "success",
                            "action_type": "wait_for_selector",
                            "selector": selector,
                            "context": context or "main",
                        }
                    except Exception as retry_exc:
                        err_msg = str(retry_exc)
                else:
                    detail = (
                        "a known payment nag is present but could not be dismissed"
                        if last.get("known_nag")
                        else "a blocking modal is present and was not recognised"
                    )
                    err_msg = f"{err_msg} — {detail}: «{(last.get('text') or '')[:200]}»"
            logger.error(
                "playwright.wait_for_selector.failed",
                component="playwright",
                error=err_msg,
                handled=False,
            )
            return {
                "status": "error",
                "message": err_msg,
                "action_type": "wait_for_selector",
                "selector": selector,
            }

    async def _click_and_wait_for_window(
        self, page, selector: str, force_click: bool, new_window_name: str
    ):
        """
        Click an element and wait for a new window to open.

        Returns:
            The new window page object

        Raises:
            RuntimeError: If context is not available
        """
        # Validate context state
        if not self.context:
            raise RuntimeError("Browser context is not available")

        # Capture the popup via the browser's page event rather than relying on
        # page.click() returning. These controls fire window.open()+ASP.NET
        # postback in onclick, so page.click() frequently times out at
        # "performing click action" EVEN THOUGH the window opened. expect_page()
        # captures it regardless; a click timeout is only fatal if no page opened.
        # no_wait_after avoids blocking on the current page's navigation; force
        # ensures the onclick actually dispatches (without it the click may never
        # fire on these controls).
        # Single click with one continuous, peak-hour-scaled expect_page window.
        # NOT retried: these controls fire window.open()+ASP.NET postback in onclick,
        # so re-clicking after the listener closed risks a duplicate popup/postback
        # (double-submit). Scaling the window is the fix — reproduced live 2026-07-17:
        # the Submit -> dashboard popup timed out under throttle at the old fixed 15s
        # but opened at ~18-23s, comfortably inside the scaled window.
        try:
            async with self.context.expect_page(timeout=self._scaled(15000)) as new_page_info:
                try:
                    await page.click(
                        selector, timeout=self._scaled(10000), no_wait_after=True, force=force_click
                    )
                    logger.info("playwright.click.executed", component="playwright")
                except PlaywrightTimeoutError:
                    logger.info("playwright.click.timeout_relying_on_event", component="playwright")
            new_window = await new_page_info.value
            logger.info("playwright.new_window.captured", component="playwright")
        except PlaywrightTimeoutError:
            logger.error("playwright.new_window.timeout", component="playwright")
            raise RuntimeError("New window did not open within timeout period")

        # The window is already captured — that IS the success condition. Register
        # it BEFORE waiting on load state so a slow-loading popup can't lose it.
        window_name = new_window_name or "popup"
        self.windows[window_name] = new_window

        # Best-effort load-state wait. PDF/print popups (and slow remote loads) may
        # not fire domcontentloaded within 10s; the SOP's next step does the real
        # load wait, so a slow load here must NOT fail the click. (The previous
        # `except asyncio.TimeoutError` never caught Playwright's TimeoutError, so
        # this 10s wait was silently fatal on slow popups like the checklist Print.)
        try:
            await new_window.wait_for_load_state("domcontentloaded", timeout=self._scaled(10000))
        except PlaywrightTimeoutError:
            logger.info(
                "playwright.new_window.load_state_slow",
                component="playwright",
                window_name=window_name,
            )

        # Best-effort: the window is already registered, so a hiccup bringing it to
        # front / sizing it (e.g. an odd PDF popup) must not drop the captured window.
        try:
            await new_window.bring_to_front()
            await new_window.set_viewport_size({"width": 1920, "height": 1080})
            await self._setup_dialog_handler(new_window)
        except Exception as setup_err:
            logger.info(
                "playwright.new_window.setup_best_effort",
                component="playwright",
                window_name=window_name,
                error=str(setup_err),
            )

        logger.info("playwright.new_window.opened", component="playwright", window_name=window_name)
        logger.info("playwright.new_window.url", component="playwright", url=new_window.url)

        return new_window

    async def _wait_for_and_register_window(
        self, initial_pages: int, new_window_name: str = None
    ) -> str:
        """Poll for a new browser page to appear and register it by name.

        Used by the dblclick `wait_for_new_window` paths (both callers are
        dblclick — the plain and the iframe/mouse-coords variant).

        The ceiling is peak-hour-scaled like the click path's expect_page.
        PR #162 scaled every fixed nav timeout it could find but MISSED this
        one, because it is a hand-rolled poll rather than a Playwright timeout
        argument — so opening a LogiSys job by double-clicking its grid row kept
        its off-peak 15s ceiling. Measured 2026-07-20 ~15:40-16:10 IST: the fill
        SOP's job-row open needed a retry in 4 of 6 runs, while a good open took
        6.0s. The fill SOP does not set `self_healing: true`, so in production
        that terminates the run instead of retrying.
        """
        if not self.context:
            raise RuntimeError("Browser context is not available")

        max_wait_time = self._scaled(15000) / 1000
        poll_interval = 0.5
        elapsed_time = 0
        new_window = None

        while elapsed_time < max_wait_time:
            await asyncio.sleep(poll_interval)
            elapsed_time += poll_interval

            current_pages_list = self.context.pages
            if asyncio.iscoroutine(current_pages_list):
                current_pages_list = await current_pages_list
            elif callable(current_pages_list):
                current_pages_list = current_pages_list()
                if asyncio.iscoroutine(current_pages_list):
                    current_pages_list = await current_pages_list

            if len(current_pages_list) > initial_pages:
                new_window = current_pages_list[-1]
                logger.info(
                    "playwright.new_window.detected",
                    component="playwright",
                    elapsed_s=elapsed_time,
                )
                break

        if not new_window:
            logger.error(
                "playwright.new_window.none", component="playwright", max_wait_s=max_wait_time
            )
            raise RuntimeError(
                "No blocking popup detected. New window did not open within timeout period"
            )

        # The window is detected = success. Register it BEFORE the load-state wait
        # so a slow popup can't lose it, and treat the load-state as best-effort:
        # the job/print popup can be slow to fire domcontentloaded on Browserbase,
        # the SOP's next step does the real load wait, and (critically) the previous
        # fatal 10s wait made each retry open ANOTHER popup — looping the run.
        window_name = new_window_name or "popup"
        self.windows[window_name] = new_window
        try:
            await new_window.wait_for_load_state("domcontentloaded", timeout=self._scaled(10000))
        except PlaywrightTimeoutError:
            logger.info(
                "playwright.new_window.load_state_slow",
                component="playwright",
                window_name=window_name,
            )
        # Best-effort: the window is already registered, so a hiccup bringing it to
        # front / sizing it (e.g. an odd PDF popup) must not drop the captured window.
        try:
            await new_window.bring_to_front()
            await new_window.set_viewport_size({"width": 1920, "height": 1080})
            await self._setup_dialog_handler(new_window)
        except Exception as setup_err:
            logger.info(
                "playwright.new_window.setup_best_effort",
                component="playwright",
                window_name=window_name,
                error=str(setup_err),
            )

        logger.info(
            "playwright.new_window.registered", component="playwright", window_name=window_name
        )
        logger.info("playwright.new_window.url", component="playwright", url=new_window.url)
        return window_name

    def get_page_context(self, context: str = None):
        """
        Get the appropriate page context (main page or named window).
        Used for Playwright operations (fill, click, wait).

        Args:
            context: Window name (e.g., "dashboard") or None for main page

        Returns:
            Playwright Page object

        Raises:
            RuntimeError: If requested context doesn't exist or page is None
        """
        if context:
            if context not in self.windows:
                raise RuntimeError(
                    f"Window context '{context}' not found. Available: {list(self.windows.keys())}"
                )

            window = self.windows[context]
            # Validate window is still valid
            try:
                if window.is_closed():
                    raise RuntimeError(f"Window context '{context}' has been closed")
            except Exception as e:
                raise RuntimeError(f"Window context '{context}' is no longer valid: {e}")

            return window

        # Return main page (native Playwright page in v3)
        if not self.page:
            raise RuntimeError("Main page is not available. Client may not be initialized.")

        return self.page

    async def _switch_to_window_async(self, window_name: str):
        """
        Switch Stagehand's active page to a different window.
        Brings the window to front so Stagehand operates on it.

        Args:
            window_name: Name of the window to switch to
        """
        if window_name not in self.windows:
            logger.warning(
                "stagehand.window.not_found", component="stagehand", window_name=window_name
            )
            return

        window = self.windows[window_name]

        # Bring window to front - this makes it the active page for Stagehand
        try:
            await window.bring_to_front()
            logger.info("stagehand.window.switched", component="stagehand", window_name=window_name)
        except Exception as e:
            logger.warning(
                "stagehand.window.bring_to_front_failed", component="stagehand", error=str(e)
            )

    @property
    def is_initialized(self) -> bool:
        """Check if the client is initialized."""
        return self._initialized

    def get_playwright_page(self):
        """
        Get the underlying Playwright page object for direct Playwright operations.

        Returns:
            The main Playwright page object

        Raises:
            RuntimeError: If page is not available
        """
        if not self.page:
            raise RuntimeError("Playwright page is not available. Client may not be initialized.")
        return self.page

    @property
    def current_page(self):
        """Get the current page object."""
        return self.page
