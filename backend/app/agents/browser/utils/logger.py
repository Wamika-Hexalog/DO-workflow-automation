"""
Structured logging utility for the Browser Auto agent.

Renders color-coded, emoji-enhanced banners to the terminal for live CLI
visibility, and — via the central logging config — mirrors the SAME events to
the per-service log files as clean structured JSON (no ANSI/emoji).

How the dual-render works: every method builds its banner string and emits a
single record on the ``agents.browser.ux`` logger, passing the banner in
``extra={"_pretty": ...}`` plus the structured fields. The console formatter
(``ConsoleDualFormatter``) prints ``_pretty`` verbatim; the JSON file
formatters drop ``_pretty`` (it starts with ``_``) and keep the fields.

ANSI colours are gated on ``sys.stderr.isatty()`` so redirected/piped output
isn't littered with raw escape codes.
"""

import json
import logging
import sys
from typing import Optional

_ux_log = logging.getLogger("agents.browser.ux")
_USE_COLOR = sys.stderr.isatty()

# Caller vocabulary → the job_state enum in shared/log_fields.py. The agent core
# reports "error"; the enum spells that outcome "failed". Mapping here keeps the
# emitted field inside the enum without forcing every caller to change words.
_JOB_STATE_ALIASES: dict[str, str] = {"error": "failed"}

# The enum, duplicated rather than imported: this module is the browser agent's
# console logger and importing shared.log_fields would couple it to the contract
# module for one frozenset. Kept in sync by tests/test_log_fields.py.
_JOB_STATES = frozenset({"started", "completed", "failed", "skipped", "degraded"})


def _job_state(status: str) -> str:
    """Coerce a caller's word into the job_state enum, fail-closed.

    A plain ``aliases.get(status, status)`` passed any unrecognised string
    straight through, so a future caller could emit a non-enum ``job_state``
    without failing CI — the static test cannot see through a function call and
    the runtime test only covers known inputs. Anything unrecognised is reported
    as ``failed``, which is also what ``sop_complete`` already does with it: its
    banner treats every non-``completed`` value as a warning.
    """
    if status in _JOB_STATES:
        return status
    return _JOB_STATE_ALIASES.get(status, "failed")


def _emit(level: int, event: str, pretty: str, **fields: object) -> None:
    """Emit one UX record: banner to console (`_pretty`), fields to JSON files."""
    extra: dict[str, object] = {"_pretty": pretty}
    extra.update({k: v for k, v in fields.items() if v is not None})
    _ux_log.log(level, event, extra=extra)


class LogColor:
    """ANSI color codes for terminal output (empty when not a TTY)."""

    RESET = "\033[0m" if _USE_COLOR else ""
    BOLD = "\033[1m" if _USE_COLOR else ""
    DIM = "\033[2m" if _USE_COLOR else ""

    # Colors
    RED = "\033[91m" if _USE_COLOR else ""
    GREEN = "\033[92m" if _USE_COLOR else ""
    YELLOW = "\033[93m" if _USE_COLOR else ""
    BLUE = "\033[94m" if _USE_COLOR else ""
    MAGENTA = "\033[95m" if _USE_COLOR else ""
    CYAN = "\033[96m" if _USE_COLOR else ""
    WHITE = "\033[97m" if _USE_COLOR else ""
    GRAY = "\033[90m" if _USE_COLOR else ""


class AgentLogger:
    """Structured logger for agent operations (banner on console, JSON in files)."""

    @staticmethod
    def model_switch(model_name: str, reason: Optional[str] = None):
        """Log model selection."""
        if "computer-use" in model_name.lower():
            model_str = f"{LogColor.MAGENTA}🖥️  MODEL: Computer Use{LogColor.RESET} {LogColor.DIM}(vision-powered, coordinate-based){LogColor.RESET}"
            model_label = "computer_use"
        elif "flash" in model_name.lower():
            model_str = f"{LogColor.CYAN}⚡ MODEL: Flash{LogColor.RESET} {LogColor.DIM}(fast, text-based){LogColor.RESET}"
            model_label = "flash"
        else:
            model_str = f"{LogColor.BLUE}🤖 MODEL: {model_name}{LogColor.RESET}"
            model_label = model_name

        pretty = f"{model_str} {LogColor.DIM}→ {reason}{LogColor.RESET}" if reason else model_str
        _emit(logging.INFO, "llm.model_switch", pretty, model=model_label, reason=reason)

    @staticmethod
    def step(step_num: int, total_steps: int, step_description: str):
        """Log current SOP step."""
        pretty = (
            f"\n{LogColor.GRAY}{'─' * 60}{LogColor.RESET}\n"
            f"{LogColor.BOLD}{LogColor.CYAN}📍 STEP {step_num}/{total_steps}:{LogColor.RESET} {step_description}"
        )
        _emit(
            logging.INFO,
            "sop.step",
            pretty,
            step=step_num,
            total=total_steps,
            desc=step_description,
        )

    @staticmethod
    def reasoning(text: str, max_length: int = 120):
        """Log LLM reasoning (truncated on console, full in files)."""
        if not text:
            return
        truncated = text[:max_length] + "..." if len(text) > max_length else text
        pretty = f"\n{LogColor.YELLOW}💭 REASONING:{LogColor.RESET} {truncated}"
        _emit(logging.INFO, "llm.reasoning", pretty, reasoning=text)

    @staticmethod
    def action(action_type: str, description: str, is_primitive: bool = False):
        """Log action being executed."""
        if action_type == "goto":
            emoji, color, label = "🌐", LogColor.BLUE, "NAVIGATE"
        elif action_type == "act":
            emoji, color, label = "🎯", LogColor.GREEN, "EXECUTE"
        elif action_type == "observe":
            emoji, color, label = "👁️", LogColor.CYAN, "OBSERVE"
        elif action_type == "end":
            emoji, color, label = "✅", LogColor.GREEN, "COMPLETE"
        else:
            emoji, color, label = "⚙️", LogColor.WHITE, action_type.upper()

        pretty = (
            f"\n{color}{emoji} ACTION:{LogColor.RESET} {label}\n"
            f"  {LogColor.DIM}└─{LogColor.RESET} {description}"
        )
        _emit(
            logging.INFO,
            "sop.action",
            pretty,
            action=action_type,
            desc=description,
            primitive=is_primitive,
        )

    @staticmethod
    def result(status: str, message: Optional[str] = None):
        """Log action result."""
        if status == "success":
            emoji, color, level = "✓", LogColor.GREEN, logging.INFO
        elif status == "error":
            emoji, color, level = "✗", LogColor.RED, logging.ERROR
        else:
            emoji, color, level = "•", LogColor.YELLOW, logging.INFO

        pretty = f"\n{color}{emoji} RESULT:{LogColor.RESET} {status.upper()}"
        if message:
            pretty += f" {LogColor.DIM}• {message}{LogColor.RESET}"
        # sop_result, not job_state: this is one action's outcome inside a SOP,
        # a different concept from the run outcome that sop.complete reports.
        _emit(level, "sop.result", pretty, sop_result=status, detail=message)

    @staticmethod
    def retry(reason: str):
        """Log retry/escalation event."""
        pretty = f"\n{LogColor.YELLOW}🔄 RETRY:{LogColor.RESET} {reason}"
        _emit(logging.WARNING, "sop.retry", pretty, reason=reason)

    @staticmethod
    def analyzing_step():
        """Log that we're analyzing the current step."""
        pretty = f"{LogColor.DIM}ℹ️  Analyzing step complexity...{LogColor.RESET}"
        _emit(logging.INFO, "sop.analyzing", pretty)

    @staticmethod
    def function_call(func_name: str, args: dict):
        """Log Computer Use function call."""
        args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2])  # first 2 args
        if len(args) > 2:
            args_str += "..."
        pretty = f"{LogColor.DIM}  🔧 Function: {func_name}({args_str}){LogColor.RESET}"
        _emit(logging.INFO, "llm.function_call", pretty, func=func_name, call_args=args)

    @staticmethod
    def sop_start(sop: str):
        """Log SOP execution start."""
        lines = [
            line.strip() for line in sop.split("\n") if line.strip() and not line.startswith("#")
        ]
        parts = [
            f"\n{LogColor.BOLD}{LogColor.GREEN}{'═' * 60}{LogColor.RESET}",
            f"{LogColor.BOLD}{LogColor.GREEN}🚀 STARTING SOP EXECUTION{LogColor.RESET}",
            f"{LogColor.BOLD}{LogColor.GREEN}{'═' * 60}{LogColor.RESET}",
            f"{LogColor.CYAN}📋 Steps:{LogColor.RESET}",
        ]
        for i, line in enumerate(lines[:5], 1):  # first 5 steps
            parts.append(f"  {i}. {line[:80]}")
        if len(lines) > 5:
            parts.append(f"  ... and {len(lines) - 5} more")
        pretty = "\n".join(parts) + "\n"
        _emit(logging.INFO, "sop.start", pretty, steps=len(lines))

    @staticmethod
    def sop_complete(
        status: str,
        steps_completed: int,
        total_steps: int,
        extracted_data: Optional[str] = None,
    ):
        """Log SOP completion."""
        parts = [f"\n{LogColor.BOLD}{LogColor.GREEN}{'═' * 60}{LogColor.RESET}"]
        if status == "completed":
            parts.append(
                f"{LogColor.BOLD}{LogColor.GREEN}✅ SOP COMPLETED{LogColor.RESET} {LogColor.DIM}({steps_completed}/{total_steps} steps){LogColor.RESET}"
            )
            level = logging.INFO
        else:
            parts.append(
                f"{LogColor.BOLD}{LogColor.YELLOW}⚠️  SOP {status.upper()}{LogColor.RESET} {LogColor.DIM}({steps_completed}/{total_steps} steps){LogColor.RESET}"
            )
            level = logging.WARNING

        extracted_keys: Optional[list[str]] = None
        if extracted_data:
            parts.append(f"\n{LogColor.CYAN}📊 Extracted Data:{LogColor.RESET}")
            rendered, parsed = _render_extracted(extracted_data)
            parts.append(rendered)
            if isinstance(parsed, dict):
                extracted_keys = list(parsed.keys())

        parts.append(f"{LogColor.BOLD}{LogColor.GREEN}{'═' * 60}{LogColor.RESET}\n")
        # Store only the field keys in the structured record (the full payload is
        # persisted to MongoDB automation_runs); avoids heavy/sensitive log lines.
        # job_state is a closed enum (shared/log_fields.py JOB_STATES) so that
        # outcome queries can group on it. Callers say "error"; the enum spells
        # that "failed", so normalise here rather than widening the enum.
        _emit(
            level,
            "sop.complete",
            "\n".join(parts),
            job_state=_job_state(status),
            completed=steps_completed,
            total=total_steps,
            extracted_keys=extracted_keys,
        )

    @staticmethod
    def error(message: str, handled: bool = False):
        """Log error message.

        ``handled`` says whether the caller recovered — it feeds the real
        error-rate query, which excludes recoveries. It defaults to False so an
        unclassified failure is counted rather than hidden.
        """
        pretty = f"{LogColor.RED}❌ ERROR:{LogColor.RESET} {message}"
        _emit(logging.ERROR, "agent.error", pretty, detail=message, handled=handled)

    @staticmethod
    def info(message: str):
        """Log info message."""
        pretty = f"{LogColor.BLUE}ℹ️  INFO:{LogColor.RESET} {message}"
        _emit(logging.INFO, "agent.info", pretty, detail=message)

    @staticmethod
    def screenshot(has_screenshot: bool):
        """Log screenshot capture."""
        # Removed - too verbose, screenshot is expected

    @staticmethod
    def tokens(
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
        source: str = "",
    ):
        """Log token usage and cost for an LLM call."""
        source_label = f" {LogColor.DIM}[{source}]{LogColor.RESET}" if source else ""
        pretty = (
            f"{LogColor.GRAY}  💰 Tokens:{LogColor.RESET} "
            f"{prompt_tokens:,} in / {completion_tokens:,} out = {total_tokens:,} total • "
            f"${cost_usd:.6f}{source_label}"
        )
        _emit(
            logging.INFO,
            "llm.tokens",
            pretty,
            prompt=prompt_tokens,
            completion=completion_tokens,
            total=total_tokens,
            cost_usd=cost_usd,
            source=source or None,
        )


def _render_extracted(extracted_data) -> tuple[str, object]:
    """Pretty-print extracted data for the console banner; return (text, parsed)."""
    parsed: object = None
    if isinstance(extracted_data, dict):
        try:
            if "extraction" in extracted_data and isinstance(extracted_data["extraction"], str):
                try:
                    parsed = json.loads(extracted_data["extraction"])
                    return json.dumps(parsed, indent=2, ensure_ascii=False), parsed
                except json.JSONDecodeError:
                    return extracted_data["extraction"], None
            parsed = extracted_data
            return json.dumps(extracted_data, indent=2, ensure_ascii=False), parsed
        except Exception:
            return str(extracted_data), None
    if isinstance(extracted_data, str):
        try:
            parsed = json.loads(extracted_data)
            return json.dumps(parsed, indent=2, ensure_ascii=False), parsed
        except json.JSONDecodeError:
            return extracted_data, None
    return str(extracted_data), None
