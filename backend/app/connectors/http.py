# error_code_from_response copied from python-brain @ 4bb5af8 shared/http.py
import httpx

_ERROR_CODE_LIMIT = 120


def error_code_from_response(response: httpx.Response) -> str | None:
    """Extract a short vendor error code from a 4xx/5xx body, never the body.

    Vendors disagree on the envelope, so try the shapes we actually receive and
    fall back to the HTTP reason phrase. The result is capped and still passes
    through the redaction processor, so a code that embeds a token is scrubbed.
    """

    def _capped(value: object) -> str | None:
        text = str(value).strip()
        return text[:_ERROR_CODE_LIMIT] if text else None

    # Only controlled code/type fields are read. `message` and `detail` are
    # free-form body text — they carry customer data and opaque credentials, and
    # capping them at 120 chars is a smaller leak, not a redaction.
    try:
        data = response.json()
    except Exception:
        return _capped(response.reason_phrase)

    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                if error.get(key):
                    return _capped(error[key])
        for key in ("error_code", "errorCode", "code", "type"):
            if data.get(key):
                return _capped(data[key])

    return _capped(response.reason_phrase)
