"""The control panel at /control plays CMA CGM. It answers what the local portals
receive, with a click or by autoplay, and shows the emails CMA CGM would send.
It reloads itself whenever something changes, so it can sit beside the browser
the agent drives."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from . import cma, pages
from .pages import esc
from .store import Store

router = APIRouter(prefix="/control")

# Each kind of request: its heading, what answering it means, and whether it can be turned down.
SECTIONS = (
    ("invoice_requests", "ODeX: DO invoice requests", "Release the invoice", False),
    ("rtgs_requests", "CMA CGM RTGS: payment confirmations", "Find the payment and email the receipt", False),
    ("deposits", "ODeX: security deposits", "Approve the deposit", True),
    ("do_requests", "ODeX: DO requests", "Issue the eDO", True),
)

_RELOAD_SCRIPT = """
const shown = document.body.dataset.version;
setInterval(async () => {
  try {
    const now = await (await fetch('/control/version')).text();
    if (now !== shown) location.reload();
  } catch (e) { /* the portals are restarting */ }
}, 1500);
"""


def _store(request: Request) -> Store:
    return request.app.state.store


def _back(message: str) -> RedirectResponse:
    return RedirectResponse(f"/control?said={quote(message)}", status_code=303)


def _file(file_id: str | None, data: dict[str, Any]) -> str:
    info = data["files"].get(file_id or "")
    return f'<a href="/control/files/{esc(file_id)}">{esc(info["name"])}</a>' if info else ""


def _details(kind: str, item: dict[str, Any], data: dict[str, Any]) -> str:
    """What the request says, the way CMA CGM's staff would look it over."""
    if kind == "invoice_requests":
        lines = [
            ", ".join(esc(item.get(key) or "") for key in ("liner", "bl_location", "type", "delivery_type")),
            f"Job {esc(item.get('job_no') or '-')}",
            " ".join(f"{esc(a['type'])}: {_file(a['file'], data)}" for a in item.get("attachments", [])),
        ]
        if item.get("invoice_no"):
            lines.append(f"{esc(item['invoice_no'])}: Rs. {pages.money(item['amount'])} {_file(item.get('invoice_file'), data)}")
    elif kind == "rtgs_requests":
        lines = [
            f"{esc(item['company_name'])}: {esc(item['contact_name'])}, {esc(item['contact_number'])}",
            f"Receipt to {esc(', '.join(item['emails']))}",
            f"UTR {esc(item['utr'])}, {esc(item['currency'])} {pages.money(item['amount'])}, "
            f"transferred {esc(item['transfer_date'])} from A/C {esc(item['bank_account'])}",
        ]
        lines += [
            ("Security deposit" if r["security_deposit"] else f"Invoice {esc(r['invoice_no'])}")
            + f" on {esc(r['bl_no'])}: {pages.money(r['amount'])}"
            for r in item["invoices"]
        ]
        if item.get("advance_amount"):
            lines.append(f"Advance: {pages.money(item['advance_amount'])}")
        lines += [_file(f, data) for f in item.get("receipts", [])]
    elif kind == "deposits":
        refund = esc(item["refund_to"]) + (f": {esc(item['third_party_name'])}" if item["third_party_name"] else "")
        lines = [
            f"Rs. {pages.money(item['amount'])}, UTR {esc(item['utr'])}, {esc(item['bank_name'])}, paid {esc(item['payment_date'])}",
            f"Refund to {refund}",
            " ".join(filter(None, [_file(item.get("receipt_file"), data), _file(item.get("noc_file"), data)])),
            _file(item.get("slip_file"), data),
        ]
    else:
        lines = [
            ", ".join(
                esc(item.get(key) or "")
                for key in ("liner", "location", "cargo_type", "destuff_type", "remarks") if item.get(key)
            ),
            f"Factory: {esc(item['factory_address'])} {esc(item['factory_pin'])}" if item.get("factory_address") else "",
            " ".join(f"{esc(d['doc_type'])}: {_file(d['file'], data)}" for d in item["documents"]),
            _file(item.get("edo_file"), data),
        ]
    if item.get("reason"):
        lines.append(f'<span class="error">{esc(item["reason"])}</span>')
    return "<br>".join(line for line in lines if line)


def _actions(kind: str, item: dict[str, Any], answer: str, can_reject: bool) -> str:
    if item["status"] != cma.WAITING[kind]:
        return ""
    target = f"{esc(kind)}/{esc(item['id'])}"
    buttons = (
        f'<form method="post" action="/control/answer/{target}">'
        f'<button class="btn small primary" data-answer="{esc(item["ref"])}">{esc(answer)}</button></form>'
    )
    if can_reject:
        buttons += (
            f'<form method="post" action="/control/reject/{target}" style="margin-top:6px">'
            '<input name="reason" placeholder="Why (optional)" style="font:inherit;padding:3px 6px;width:150px"> '
            '<button class="btn small">Reject</button></form>'
        )
    return buttons


@router.get("")
def control_panel(request: Request, said: str = "") -> Response:
    with _store(request).read() as data:
        version = data["version"]
        autoplay = dict(data["autoplay"])
        sections = []
        for kind, title, answer, can_reject in SECTIONS:
            rows = [
                f'<tr data-ref="{esc(item["ref"])}" data-status="{esc(item["status"])}">'
                f'<td>{esc(item["ref"])}<div class="hint">{esc(item["created_at"].replace("T", " "))}</div></td>'
                f'<td>{esc(item["bl_no"])}</td><td>{pages.status(item["status"])}</td>'
                f"<td>{_details(kind, item, data)}</td><td>{_actions(kind, item, answer, can_reject)}</td></tr>"
                for item in reversed(data[kind])
            ]
            table = pages.table(kind.replace("_", "-"), ["Request", "B/L no.", "Status", "Details", "CMA CGM"], rows, "Nothing yet.")
            sections.append(f"<h2>{esc(title)}</h2>{table}")
        emails = [
            f"<tr><td>{esc(mail['at'].replace('T', ' '))}</td><td>{esc(mail['to'])}</td>"
            f"<td><b>{esc(mail['subject'])}</b><div class=\"hint\">{esc(mail['body'])}</div></td>"
            f"<td>{'<br>'.join(_file(f, data) for f in mail['attachments'])}</td></tr>"
            for mail in data["outbox"]
        ]
    flash = f'<div class="outcome accepted" id="said">{esc(said)}</div>' if said else ""
    state = "on" if autoplay["on"] else "off"
    body = f"""<h1>Control panel</h1>
<p class="path">Plays CMA CGM: answers what the local portals receive, by hand or by autoplay. Open it beside
<a href="/odex" target="_blank">ODeX</a> and <a href="/cma-rtgs" target="_blank">the RTGS page</a>.</p>
{flash}
<div class="card"><form method="post" action="/control/autoplay" class="grid" id="autoplay-form">
<div class="wide"><b>Autoplay is {state}.</b> <span class="muted">When on, each request is answered by itself
once it has waited the delay: the invoice is verified, the receipts emailed, the deposit approved, the eDO issued.</span></div>
{pages.field("delay_seconds", "Delay (seconds)", type="number", value=str(autoplay["delay_seconds"]), extra='min="1" max="600"')}
<div class="actions"><button class="btn primary" name="on" value="{'0' if autoplay['on'] else '1'}" id="toggle-autoplay">
Turn autoplay {'off' if autoplay['on'] else 'on'}</button>
<button class="btn" name="on" value="{'1' if autoplay['on'] else '0'}">Save delay</button></div>
</form></div>
{"".join(sections)}
<h2>Emails CMA CGM would send</h2>
<p class="hint">Nothing is sent: in real life these arrive in the mailbox the Graph subscription watches.</p>
{pages.table("outbox", ["Sent", "To", "Subject", "Attachments"], emails, "No emails yet.")}
<h2>Start again</h2>
<form method="post" action="/control/reset" onsubmit="return confirm('Clear every request, email and file in the local portals?')">
<button class="btn" id="reset">Reset the local portals</button></form>"""
    # The page reloads when the state's version moves past the one it shows.
    return pages.page("Control panel", body, site="control", script=_RELOAD_SCRIPT, body_attrs=f'data-version="{version}"')


# What the app shows of each request. The RTGS bank account and contacts stay here.
_RECORD_KEYS = {
    "invoice_requests": ("invoice_no", "amount", "invoice_file"),
    "rtgs_requests": ("utr", "amount", "transfer_date", "receipts"),
    "deposits": ("utr", "amount", "bank_name", "payment_date", "refund_to", "slip_file"),
    "do_requests": ("edo_no", "edo_file"),
}


@router.get("/records")
def records(request: Request, bl: list[str] = Query(default=[])) -> dict[str, Any]:
    """Each request on the given B/L numbers, newest first, for the app's shipment page."""
    wanted = {b.strip().upper() for b in bl if b.strip()}
    out: dict[str, Any] = {}
    with _store(request).read() as data:
        for kind, keys in _RECORD_KEYS.items():
            out[kind] = [
                {
                    "ref": item["ref"], "bl_no": item["bl_no"], "status": item["status"],
                    "waiting": item["status"] == cma.WAITING[kind], "created_at": item["created_at"],
                    "reason": item.get("reason", ""),
                    **{k: item.get(k) for k in keys},
                    **({"invoices": item["invoices"]} if kind == "rtgs_requests" else {}),
                }
                for item in reversed(data[kind])
                if str(item.get("bl_no", "")).upper() in wanted
                or any(str(r.get("bl_no", "")).upper() in wanted for r in item.get("invoices", []))
            ]
        files = {f for kind in out.values() for item in kind for f in _file_ids(item)}
        out["files"] = {f: data["files"][f]["name"] for f in files if f in data["files"]}
    return out


def _file_ids(item: dict[str, Any]) -> list[str]:
    ids = [item.get(k) for k in ("invoice_file", "slip_file", "edo_file")] + list(item.get("receipts") or [])
    return [i for i in ids if i]


@router.get("/version")
def version(request: Request) -> Response:
    with _store(request).read() as data:
        return PlainTextResponse(str(data["version"]))


@router.post("/answer/{kind}/{item_id}")
def answer(kind: str, item_id: str, request: Request) -> Response:
    if kind not in cma.ANSWERS:
        return Response("No such kind of request.", status_code=404)
    try:
        return _back(cma.ANSWERS[kind](_store(request), item_id))
    except cma.NotWaiting as exc:
        return _back(str(exc))


@router.post("/reject/{kind}/{item_id}")
async def reject(kind: str, item_id: str, request: Request) -> Response:
    if kind not in ("deposits", "do_requests"):
        return Response("That can't be rejected here.", status_code=404)
    form = await request.form()
    reason = form.get("reason")
    try:
        return _back(cma.reject(_store(request), kind, item_id, reason if isinstance(reason, str) else ""))
    except cma.NotWaiting as exc:
        return _back(str(exc))


@router.post("/autoplay")
async def set_autoplay(request: Request) -> Response:
    form = await request.form()
    try:
        delay = min(max(int(str(form.get("delay_seconds") or "8")), 1), 600)
    except ValueError:
        delay = 8
    on = form.get("on") == "1"
    with _store(request).change() as data:
        data["autoplay"] = {"on": on, "delay_seconds": delay}
    return _back(f"Autoplay is {'on' if on else 'off'}, answering after {delay} seconds.")


@router.post("/reset")
def reset(request: Request) -> Response:
    _store(request).reset()
    return _back("The local portals are empty again.")


@router.get("/files/{file_id}")
def download(file_id: str, request: Request) -> Response:
    found = _store(request).file(file_id)
    if found is None:
        return Response("No such file.", status_code=404)
    name, path = found
    return FileResponse(path, media_type="application/pdf", filename=name, content_disposition_type="inline")
