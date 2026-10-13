"""Secret scrubbing as a structlog processor — never at a call site.

Why a processor
---------------
The tree used to scrub by *truncating* at individual call sites, and that failed
in both directions at once: it destroyed the identifiers we needed (a 30-char
slice of an Outlook message id is useless for tracing) while preserving the
secret prefixes we didn't want (``api_key[:10]`` is still ten characters of a
live key, on 30-day retention). Truncation is not redaction.

One processor means no author has to remember. Three layers, cheapest first:

1. **Key-based** — a field whose *name* says it holds a credential never has its
   value rendered, whatever that value is.
2. **Value-based** — the actual configured secrets are scrubbed wherever they
   appear, including places no call site knows about: a 4xx response body, an
   ``exc_info`` traceback, an f-string message. This is the layer that catches
   leaks nobody wrote deliberately.
3. **Pattern-based** — bearer tokens, JWTs and signed query strings, which are
   secrets we may never have had in settings at all (a signed GCS URL, a token
   minted by a vendor mid-call).

Where it must sit
-----------------
In **both** ``ProcessorFormatter`` processor lists (console and JSON), *not* in
``shared_processors``.

``shared_processors`` is also the head of ``foreign_pre_chain``, where it runs
**before** ``ExtraAdder`` — so a redactor placed there never sees stdlib
``extra={...}`` at all, and ``extra`` is exactly where the 4xx response body
lives. Half the records would ship unredacted, which is the failure this module
exists to prevent. ``ProcessorFormatter.format`` runs ``foreign_pre_chain`` for
foreign records only, then its own ``processors`` for **both** origins, so the
common stage is the only correct placement. Order within it:

    add_service → redact_secrets → drop_internal → renderer

Known gap, stated rather than implied
-------------------------------------
``ConsoleDualFormatter.format`` short-circuits on ``_pretty`` and returns the
banner without running the processor chain. Banners are therefore redacted by an
explicit call in that method (see ``redact_text``), not by this processor. The
log *files* never contain ``_pretty`` at all — ``drop_internal`` removes it — so
file coverage does not depend on that path.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Final, MutableMapping

REDACTED: Final[str] = "[redacted]"

# A field whose name contains one of these never has its value rendered.
# Matched against the lowercased key, as a substring, so `outlook_client_secret`
# and `x_api_key_header` both hit.
SECRET_KEY_MARKERS: Final[tuple[str, ...]] = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "token",
    "authorization",
    "credential",
    "signature",
    "webhook_url",
    "cookie",
    "private_key",
    "passwd",
)

# Metadata ABOUT a credential is not the credential, and blanket key matching
# would destroy it: `token_len` contains "token", so a naive rule redacts the one
# field that exists precisely so the secret does not have to be logged. PR 0b
# replaces `token_prefix=token[:40]` with exactly that kind of field, and this
# module would have silently undone it.
#
# These keys fall through to ordinary value handling instead of being blanked, so
# an int or bool survives while a string is still scanned by the value and
# pattern layers.
SAFE_METADATA_SUFFIXES: Final[tuple[str, ...]] = (
    "_len",
    "_length",
    "_count",
    "_present",
    "_set",
    "_valid",
    "_ok",
    "_configured",
    "_enabled",
    "_missing",
    "_hash",
    "_fingerprint",
    "_expires_at",
    "_age_s",
)
SAFE_METADATA_PREFIXES: Final[tuple[str, ...]] = ("has_", "is_", "any_", "num_", "n_")

# Exact keys that contain a secret marker and are plainly a measurement. An LLM
# ``*_tokens`` field is a COUNT, not a credential: blanking it loses the only
# record of what a call cost, and — because the count is an int and REDACTED is
# a string — it also breaks the ``INT_FIELDS`` type contract, which is the
# ParseFloat failure log_fields.py exists to prevent.
#
# Listed by exact name rather than as a ``_tokens`` suffix on purpose: a field
# that genuinely holds credentials (``refresh_tokens``) must not be exempted
# alongside them. A new counter is redacted until it is added here, which is a
# visible, safe failure.
SAFE_METADATA_EXACT: Final[frozenset[str]] = frozenset(
    {
        "llm_prompt_tokens",
        "llm_completion_tokens",
        "llm_total_tokens",
    }
)

# Settings fields whose *values* get scrubbed wherever they appear. Deliberately
# narrower than SECRET_KEY_MARKERS: `webhook_base_url` matches "webhook" but is a
# public routing URL that appears in ordinary lines, and scrubbing it would cost
# real debuggability for no gain. It does not match "webhook_url".
_SECRET_SETTING_MARKERS: Final[tuple[str, ...]] = (
    "password",
    "secret",
    "api_key",
    "token",
    "webhook_url",
)

# Below this length a "secret" is more likely to collide with ordinary prose than
# to be worth scrubbing — redacting every occurrence of a 4-character value would
# corrupt unrelated lines and make the logs less trustworthy, not more.
_MIN_SECRET_LEN: Final[int] = 8

# Depth cap: log values are sometimes nested payloads, and a cyclic or very deep
# structure must not turn logging into the slowest thing in the request.
_MAX_DEPTH: Final[int] = 6

_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # Bearer / Basic auth headers, however they were stringified.
    (re.compile(r"\b(Bearer|Basic)\s+[\w\-._~+/=]{8,}", re.IGNORECASE), r"\1 " + REDACTED),
    # JWTs — three base64url segments. Caught even when the key name is innocent.
    (re.compile(r"\beyJ[\w\-]*\.[\w\-]{4,}\.[\w\-]{4,}"), REDACTED),
    # Secrets in a query string. Two alternations on purpose:
    #
    #   * SUFFIX match for words that are never innocent at the end of a
    #     parameter name. This is what catches Microsoft Graph's
    #     `?validationToken=…`, which an exact-name list misses — and that one is
    #     a live credential the app takes care to log only the length of.
    #     Matching the suffix rather than any substring is what keeps
    #     `?token_count=`, `?tokenizer=`, `?signature_algorithm=`,
    #     `?credential_type=`, `?passwordless=` and `?secretariat=` intact.
    #   * exact match for the short, collision-prone names, so `?monkey=`,
    #     `?sortkey=` and `?keyword=` are not mangled.
    #
    # `code` is deliberately absent: it is an OAuth secret elsewhere, but in this
    # domain it is overwhelmingly an airport/port code, and redacting those would
    # make the logs worse.
    (
        re.compile(
            r"([?&](?:[^=&\s]*(?:token|secret|password|signature|credential|jwt|otp)"
            r"|key|sig|auth|api[_-]?key|apikey|access[_-]?key|secret[_-]?key"
            r"|session[_-]?id)=)[^&\s\"']+",
            re.IGNORECASE,
        ),
        r"\1" + REDACTED,
    ),
    # Credentials embedded in a URL: https://user:pass@host/…
    (
        re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s:@\"']+:[^/\s@\"']+@"),
        r"\1" + REDACTED + "@",
    ),
    # Serialised credential key/value, covering both a JSON body and a header
    # line: {"client_secret":"…"}, X-API-Key: …, password=…
    # This is the layer that catches an *opaque* secret — one minted by a vendor
    # mid-call, which is not in settings and so invisible to the value layer.
    (
        re.compile(
            r"((?:[\"']?)\b(?:client_secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"password|passwd|secret|token|authorization)(?:[\"']?)\s*[:=]\s*)"
            r"[\"']?[^\"',;&\s}\)]{6,}",
            re.IGNORECASE,
        ),
        r"\1" + REDACTED,
    ),
)

_secret_values_cache: frozenset[str] | None = None


def _secret_values() -> frozenset[str]:
    """The configured secret values, resolved once and memoised.

    Imported lazily: this module is pulled in by ``logging_config``, which is
    called at the very top of app startup, and settings must not be forced to
    load earlier than they already do. A settings failure here must never break
    logging, so any exception degrades to "no value-based layer" rather than
    taking the process down — the key and pattern layers still apply.
    """
    global _secret_values_cache
    if _secret_values_cache is not None:
        return _secret_values_cache

    values: set[str] = set()
    # do-workflow: secrets come from the environment (python-brain read its
    # settings object), e.g. GOOGLE_API_KEY or an SOP's ODEX_PASSWORD.
    for name, value in os.environ.items():
        if not any(marker in name.lower() for marker in _SECRET_SETTING_MARKERS):
            continue
        if isinstance(value, str) and len(value) >= _MIN_SECRET_LEN:
            values.add(value)

    # Longest first, so a secret that contains another is scrubbed whole.
    _secret_values_cache = frozenset(values)
    return _secret_values_cache


def reset_secret_cache() -> None:
    """Drop the memoised secret set. For tests that patch settings."""
    global _secret_values_cache, _sorted_secrets_cache
    _secret_values_cache = None
    _sorted_secrets_cache = None


_sorted_secrets_cache: tuple[str, ...] | None = None


def _sorted_secrets() -> tuple[str, ...]:
    """Longest first, so a secret containing another is scrubbed whole.

    Memoised separately from the value set: this runs on every string of every
    record, and the formatter chain executes once per matching handler (2–5×
    here), so re-sorting per call multiplies straight into the hot path.
    """
    global _sorted_secrets_cache
    if _sorted_secrets_cache is None:
        _sorted_secrets_cache = tuple(sorted(_secret_values(), key=len, reverse=True))
    return _sorted_secrets_cache


def redact_text(text: str) -> str:
    """Scrub a single string through the value and pattern layers."""
    for secret in _sorted_secrets():
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# Values of these types are rendered as-is and cannot hide a secret.
_SAFE_SCALARS: Final[tuple[type, ...]] = (bool, int, float, type(None))


def _redact_value(key: str, value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        # Returning the subtree here would hand back whatever is below the cap
        # unscrubbed — the cap is a safety limit, so it has to fail closed.
        return REDACTED

    lowered = key.lower().replace("-", "_")  # so `X-API-Key` matches `api_key`
    describes_secret_safely = (
        lowered in SAFE_METADATA_EXACT
        or lowered.endswith(SAFE_METADATA_SUFFIXES)
        or lowered.startswith(SAFE_METADATA_PREFIXES)
    )
    if not describes_secret_safely and any(marker in lowered for marker in SECRET_KEY_MARKERS):
        # The key alone condemns the value — do not inspect it, do not keep a
        # prefix of it. A prefix is what got us here.
        return REDACTED

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, _SAFE_SCALARS):
        return value
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        redacted = [_redact_value(key, v, depth + 1) for v in value]
        return type(value)(redacted) if isinstance(value, (tuple, set)) else redacted

    # Anything else — a Playwright Page, an exception, a model object. The JSON
    # renderer stringifies these with default=str *after* this processor, so
    # leaving them alone means their repr reaches the file unscrubbed. Stringify
    # here instead and scrub that.
    try:
        return redact_text(str(value))
    except Exception:  # pragma: no cover - a __str__ that raises must not kill logging
        return f"<unrenderable {type(value).__name__}>"


class RedactArgsFilter(logging.Filter):
    """Scrub secrets out of a stdlib record's %-format args, in place.

    For loggers that keep their own handler and so never reach the processor
    chain. The case this exists for is ``uvicorn.access``: it is deliberately
    left on uvicorn's handler (no exception text, enormous volume, and a line
    format a downstream collector depends on), but the request line it formats
    comes from ``get_path_with_query_string`` and therefore **includes the query
    string**. Microsoft Graph's subscription handshake arrives as
    ``?validationToken=<live credential>`` — the very token ``webhooks.py`` is
    careful to log only the length of. Without this, the transport-level access
    log undoes that care and ships the token verbatim to stdout.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(redact_text(a) if isinstance(a, str) else a for a in args)
        elif isinstance(args, dict):
            # `logger.info("%(path)s", {"path": ...})` — stdlib treats a single
            # mapping argument as the format namespace. Not what uvicorn emits,
            # but this filter is attached to a logger, not to one call site.
            record.args = {k: redact_text(v) if isinstance(v, str) else v for k, v in args.items()}
        elif isinstance(args, str):
            record.args = redact_text(args)
        return True


def redact_secrets(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: scrub every key and value in the record.

    Registered in both formatter ``processors`` lists — see the module docstring
    for why ``shared_processors`` is the wrong place.
    """
    for key in list(event_dict):
        # `_record` is the stdlib LogRecord itself, and `drop_internal` removes it
        # immediately after this processor. Recursing into it would walk the whole
        # object graph for nothing.
        if key.startswith("_"):
            continue
        event_dict[key] = _redact_value(key, event_dict[key])
    return event_dict
