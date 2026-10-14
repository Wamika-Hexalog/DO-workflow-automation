"""The local ODeX's pages that the team's step-by-step guide shows ("Delivery Order
Request-First step 1.docx"). Each is the guide's screenshot, with live elements over
only the buttons and fields the process uses; the rest is just the picture.

    guide step  page                    live over the picture
    1-2         services (home)         ePayment's location and its arrow; Counter Process's arrow
    3, 6        ePayment's main page    Requests → Liner Requests; the EDO DETAILS search
    4           Liner Requests          Request New Invoice; our requests, over the picture's rows
    5           New Invoice Request     liner, BL no., BL location, type, type of delivery, job no.,
                                        the first attachment and its remarks; Submit and Back
    8           BL Status               the BL, its request and invoice, over the picture's
    9-10        View Invoice Request    the invoice's number, date, amount and status, and its PDF

The screenshots show a real ODeX account, so they stay out of git: `make assets` takes
them from the guide into assets/private/odex/. Without them each page shows a grey
stand-in of the same shape, and every live element still works, so the SOPs and the
tests run anywhere. Live elements sit at the screenshot's own pixel coordinates (see
Screen.at): a new screenshot needs its coordinates measured again.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from . import forms, pages
from .odex import current_user, store_of, to_login
from .pages import esc
from .store import next_ref, now

router = APIRouter(prefix="/odex")

HOME = "/odex/home"
EPAYMENT = "/odex/epayment"
LINER_REQUESTS = "/odex/epayment/liner-requests"
BL_STATUS = "/odex/epayment/bl-status"
LOCATION_COOKIE = "odex_location"

# The locations ODeX's services offer, as the guide's step 2 shows them. ICD
# Tughlakabad is under NCR.
LOCATIONS = (
    "NCR", "Mundra", "Nhava Sheva", "Ahmedabad", "ICD Baroda", "ICD VAPI (VALVADA)", "Kakinada", "Kandla",
    "Kattupalli", "Paradip", "Chennai", "Kolkata",
)
LINER_NAMES = (
    "CMA CGM AGENCIES INDIA PRIVATE LIMITED", "COSCO SHIPPING LINES (INDIA) PRIVATE LIMITED",
    "HAPAG-LLOYD INDIA PRIVATE LIMITED", "MAERSK LINE INDIA PRIVATE LIMITED", "MSC AGENCY (INDIA) PRIVATE LIMITED",
)
BL_TYPES = ("Import", "Export")
DELIVERY_TYPES = ("Factory Destuff", "CFS Destuff", "DPD", "LCL")
ATTACHMENT_TYPES = ("Seaway BL", "Original BL", "Telex Release", "Invoice", "Other")


@dataclass(frozen=True)
class Screen:
    file: str  # in assets/private/odex/
    width: int
    height: int
    font: float = 15  # the size of the picture's own form text, in its pixels

    def at(self, x0: int, y0: int, x1: int, y1: int) -> str:
        """Places an element over the screenshot's own pixels (x0, y0) to (x1, y1),
        in percent of the picture, so it stays in place as the picture scales."""
        w, h = self.width, self.height
        return f"left:{x0 / w:.4%};top:{y0 / h:.4%};width:{(x1 - x0) / w:.4%};height:{(y1 - y0) / h:.4%}"


SERVICES = Screen("service.png", 1430, 683, 15)
DASHBOARD = Screen("dashboard.png", 1429, 708, 17)
REQUESTS_LIST = Screen("liner-requests.png", 1430, 623, 13.5)
NEW_INVOICE = Screen("new-invoice.png", 1918, 850, 17.5)
STATUS = Screen("bl-status.png", 1430, 688, 13)
VIEW_INVOICE = Screen("view-invoice.png", 1897, 955, 17.5)
SCREENS = {s.file: s for s in (SERVICES, DASHBOARD, REQUESTS_LIST, NEW_INVOICE, STATUS, VIEW_INVOICE)}

_STYLE = """
main { max-width:none; padding:0; }
.shot { position:relative; container-type:inline-size; width:100%; margin:0 auto; background:#fff; }
.shot-picture { display:block; width:100%; height:auto; }
.shot-missing { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; padding:24px;
                text-align:center; color:#59636e; font-size:14px;
                background:repeating-linear-gradient(45deg,#eef1f4 0 14px,#e6eaee 14px 28px); }
.live { position:absolute; box-sizing:border-box; margin:0; font-family:Arial,Helvetica,sans-serif; color:#555;
        font-size:calc(100cqw * var(--f) / var(--w)); }
.live.hot { background:transparent; border:0; padding:0; cursor:pointer; border-radius:4px; }
.live.hot:hover, .live.hot:focus-visible, .live.hot.clicked { outline:3px solid rgba(255,153,0,.9); outline-offset:1px; }
.live.hot.clicked { background:rgba(255,153,0,.2); }
.live.field { background:#fff; border:0; border-bottom:1px solid #b5b5b5; padding:0 .4em; }
.live.field:focus { outline:2px solid rgba(41,166,224,.6); }
.live.field.bl { color:#1a6fb5; font-weight:700; letter-spacing:.02em; }
.live.cover { background:#fff; overflow:hidden; white-space:nowrap; display:flex; flex-direction:column; justify-content:center; }
.live.cover.line { border-bottom:1px solid #b5b5b5; padding:0 .4em; }
.live.cover a { color:#1a6fb5; text-decoration:none; }
.live.cover b { color:#333; }
.live.grey { background:#d6d6d6; border-radius:50%; }
.live.greyline { background:#d6d6d6; }
.dropdown { position:absolute; z-index:5; background:#fff; border-radius:0 0 6px 6px; box-shadow:0 6px 16px rgba(0,0,0,.18);
            padding:calc(100cqw * 16 / var(--w)) 0; }
.dropdown a { display:block; padding:0 calc(100cqw * 34 / var(--w)); line-height:2.4; color:#555; text-decoration:none; }
.dropdown a:hover, .dropdown a.on { background:#29a6e0; color:#fff; }
.dropdown span { display:block; padding:0 calc(100cqw * 34 / var(--w)); line-height:2.4; color:#999; }
.cell { position:absolute; font-family:Arial,Helvetica,sans-serif; color:#666; font-size:calc(100cqw * var(--f) / var(--w));
        line-height:1.15; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.toast { position:fixed; top:48px; left:50%; transform:translateX(-50%); z-index:50; max-width:min(720px,92vw);
         margin:0; box-shadow:0 8px 24px rgba(0,0,0,.18); }
"""

# Shows a click on the picture's buttons, for whoever watches the agent.
_CLICKS = """
document.querySelectorAll('.live.hot').forEach((el) => el.addEventListener('click', () => {
  el.classList.add('clicked');
  setTimeout(() => el.classList.remove('clicked'), 900);
}));
"""


def screens_dir(request: Request) -> Path:
    return request.app.state.screens_dir


def _shot(request: Request, screen: Screen, live: str, **attrs: str) -> str:
    """The screenshot, or a stand-in of its shape, with the live elements over it."""
    if (screens_dir(request) / screen.file).is_file():
        picture = f'<img class="shot-picture" src="/odex/screens/{screen.file}" alt="" draggable="false">'
    else:
        picture = (
            '<div class="shot-missing">The ODeX screenshot for this page isn\'t set up: run make assets with the '
            "team's ODeX guide in Docs/Docs/. The live parts work without it.</div>"
        )
    extra = "".join(f' data-{key.replace("_", "-")}="{esc(value)}"' for key, value in attrs.items())
    return (
        f'<div class="shot" id="screen-{screen.file.removesuffix(".png")}"{extra} '
        f'style="--w:{screen.width};--f:{screen.font};max-width:{screen.width}px;aspect-ratio:{screen.width}/{screen.height}">'
        f"{picture}{live}</div>"
    )


def _page(title: str, body: str, *, script: str = "", status_code: int = 200) -> Response:
    return pages.page(title, body, site="odex", header=False, style=_STYLE, script=_CLICKS + script, status_code=status_code)


def _options(values: tuple[str, ...], chosen: str, placeholder: str = "Select") -> str:
    items = [f'<option value="">{esc(placeholder)}</option>'] if placeholder else []
    items += [f'<option value="{esc(v)}"{" selected" if v == chosen else ""}>{esc(v)}</option>' for v in values]
    return "".join(items)


def _toast(accepted: bool, message: str, *, reference: str = "", problems: list[str] | None = None) -> str:
    return pages.outcome(accepted, message, reference=reference, problems=problems or []).replace(
        'class="outcome', 'class="toast outcome', 1
    )


def _day_month(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%d %b")


def _location(request: Request) -> str:
    chosen = request.cookies.get(LOCATION_COOKIE, "")
    return chosen if chosen in LOCATIONS else "NCR"


def _latest_request(data: dict[str, Any], bl_no: str) -> dict[str, Any] | None:
    found = [r for r in data["invoice_requests"] if r["bl_no"] == bl_no]
    return dict(found[-1]) if found else None


@router.get("/screens/{name}")
def screen_picture(name: str, request: Request) -> Response:
    path = screens_dir(request) / name
    if name not in SCREENS or not path.is_file():
        return Response("No such screen.", status_code=404)
    return FileResponse(path, media_type="image/png")


# Step 1-2: the services ------------------------------------------------------------


@router.get("/home")
def services(request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    s = SERVICES
    live = f"""<form id="epayment-form" action="{EPAYMENT}" method="get">
<select id="epayment-location" name="location" class="live field" style="{s.at(412, 243, 589, 273)}">{_options(LOCATIONS, _location(request), "")}</select>
<button id="epayment-go" class="live hot" type="submit" title="ePayment" style="{s.at(603, 242, 635, 274)}"></button>
</form>
<a id="counter-go" class="live hot" title="Counter Process" href="/odex/counter-process/deposit-refund" style="{s.at(263, 242, 295, 274)}"></a>"""
    return _page("Service", _shot(request, s, live))


# Steps 3 and 6: ePayment's main page -------------------------------------------------------


def _edo_search(screen: Screen, field: tuple[int, int, int, int], button: tuple[int, int, int, int], bl_no: str = "") -> str:
    return (
        f'<form id="edo-form" action="{BL_STATUS}" method="get">'
        f'<input id="edo-search" name="bl_no" class="live field" autocomplete="off" value="{esc(bl_no)}" '
        f'placeholder="ENTER BL NO. / CONTAINER NO. / JOB NO. / VIA NO-CUST CODE" style="{screen.at(*field)}">'
        f'<button id="edo-details" class="live hot" type="submit" title="EDO DETAILS" style="{screen.at(*button)}"></button>'
        "</form>"
    )


_MENU_SCRIPT = """
const requestsMenu = document.getElementById('requests-menu');
document.getElementById('menu-requests').addEventListener('click', (event) => {
  event.preventDefault();
  requestsMenu.hidden = !requestsMenu.hidden;
});
"""


@router.get("/epayment")
def epayment(request: Request, location: str = "") -> Response:
    if current_user(request) is None:
        return to_login(request)
    if location and location not in LOCATIONS:
        return RedirectResponse(HOME, status_code=303)
    s = DASHBOARD
    live = f"""<button id="menu-requests" class="live hot" title="Requests" style="{s.at(262, 0, 358, 47)}"></button>
<div id="requests-menu" class="dropdown live" hidden style="{s.at(204, 48, 441, 225)}">
<a id="menu-liner-requests" href="{LINER_REQUESTS}">Liner Requests</a>
<span>CFS Requests</span><span>DPD Requests</span><span>Terminal Request</span></div>
{_edo_search(s, (376, 218, 890, 263), (891, 222, 1037, 258))}
<a id="home" class="live hot" title="Main menu" href="{HOME}" style="{s.at(1343, 7, 1380, 42)}"></a>"""
    response = _page("ePayment", _shot(request, s, live), script=_MENU_SCRIPT)
    if location:
        response.set_cookie(LOCATION_COOKIE, location, samesite="lax", path="/odex")
    return response


# Step 4: Requests → Liner Requests --------------------------------------------------


# The list's columns on the picture: where each starts.
_COLUMNS = (("liner", 36), ("booking", 213), ("bl_no", 340), ("bl_location", 453), ("type", 583), ("requested", 700),
            ("user", 836), ("job_no", 957), ("status", 1070), ("info", 1192))
_COLUMN_WIDTH = {"liner": 172, "requested": 132, "info": 140}


@router.get("/epayment/liner-requests")
def liner_requests(request: Request, submitted: str = "") -> Response:
    if current_user(request) is None:
        return to_login(request)
    s = REQUESTS_LIST
    with store_of(request).read() as data:
        items = [dict(r) for r in reversed(data["invoice_requests"])]
    shown = items[:2]  # the rows the picture has room for
    cells = []
    for i, item in enumerate(shown):
        top = 146 + 34 * i
        values = {
            "liner": item.get("liner", ""), "booking": "", "bl_no": item["bl_no"],
            "bl_location": item.get("bl_location", ""), "type": item.get("type", "Import"),
            "requested": datetime.fromisoformat(item["created_at"]).strftime("%d-%m-%y %H:%M:%S"),
            "user": item.get("by", "").upper(), "job_no": item.get("job_no", ""), "status": item["status"],
            "info": item["status"],
        }
        for key, x in _COLUMNS:
            width = _COLUMN_WIDTH.get(key, 110)
            liner = key == "liner"
            text = esc(values[key])
            style = s.at(x, top, x + width, top + (30 if liner else 18))
            wrap = ";white-space:normal" if liner else ";margin-top:calc(100cqw * 6 / var(--w))"
            cells.append(f'<span class="cell" data-col="{key}" style="{style}{wrap}">{text}</span>')
    rows = (
        f'<div id="liner-request-rows" class="live cover" style="{s.at(15, 143, 1416, 214)}"></div>{"".join(cells)}'
        if items else
        f'<div id="liner-request-rows" class="live cover" style="{s.at(15, 143, 1416, 214)};align-items:center">'
        "No invoice requests yet.</div>"
    )
    counted = f"Showing 1 to {len(shown)} of {len(items)} entries" if items else "Showing 0 to 0 of 0 entries"
    toast = ""
    if submitted:
        done = next((r for r in items if r["id"] == submitted), None)
        if done:
            toast = _toast(
                True, f"Invoice request {done['ref']} for BL {done['bl_no']} submitted successfully.", reference=done["ref"]
            )
    live = f"""<a id="request-new-invoice" class="live hot" title="Request New Invoice" href="{LINER_REQUESTS}/new" style="{s.at(1236, 66, 1414, 94)}"></a>
{rows}
<div class="live cover" style="{s.at(345, 227, 560, 248)};color:#333">{counted}</div>"""
    return _page("Search Invoice Request", toast + _shot(request, s, live))


# Step 5: Request New Invoice -------------------------------------------------------


def _new_invoice_page(request: Request, values: dict[str, str], problems: list[str] | None = None) -> Response:
    s = NEW_INVOICE
    toast = _toast(False, "The request was not submitted.", problems=problems) if problems else ""
    pick = values.get
    live = f"""<form id="new-invoice-form" action="{LINER_REQUESTS}/new" method="post" enctype="multipart/form-data">
<select id="liner-name" name="liner_name" class="live field" style="{s.at(186, 160, 494, 196)}">{_options(LINER_NAMES, pick("liner_name", ""))}</select>
<input id="bl-no" name="bl_no" class="live field bl" autocomplete="off" value="{esc(pick("bl_no", ""))}" style="{s.at(640, 160, 926, 196)}">
<select id="bl-location" name="bl_location" class="live field" style="{s.at(1112, 160, 1400, 196)}">{_options(LOCATIONS, pick("bl_location", _location(request)))}</select>
<select id="bl-type" name="bl_type" class="live field" style="{s.at(1610, 158, 1852, 196)}">{_options(BL_TYPES, pick("bl_type", "Import"))}</select>
<select id="delivery-type" name="delivery_type" class="live field" style="{s.at(186, 207, 430, 245)}">{_options(DELIVERY_TYPES, pick("delivery_type", ""))}</select>
<input id="job-no" name="job_no" class="live field" placeholder="Job No" autocomplete="off" value="{esc(pick("job_no", ""))}" style="{s.at(1586, 256, 1874, 293)}">
<select id="attachment-type-1" name="attachment_type_1" class="live field" style="{s.at(32, 616, 570, 654)}">{_options(ATTACHMENT_TYPES, pick("attachment_type_1", ""))}</select>
<div class="live cover" style="{s.at(586, 616, 1130, 654)};justify-content:center"><input id="attachment-file-1" name="attachment_file_1" type="file" accept="application/pdf,.pdf"></div>
<input id="attachment-remarks-1" name="attachment_remarks_1" class="live field" autocomplete="off" value="{esc(pick("attachment_remarks_1", ""))}" style="{s.at(1142, 620, 1681, 659)}">
<button id="submit-invoice-request" class="live hot" type="submit" title="Submit" style="{s.at(794, 793, 889, 829)}"></button>
</form>
<a id="back" class="live hot" title="Back" href="{LINER_REQUESTS}" style="{s.at(895, 793, 970, 829)}"></a>"""
    return _page("New Invoice Request", toast + _shot(request, s, live), status_code=400 if problems else 200)


@router.get("/epayment/liner-requests/new")
def new_invoice_request(request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    return _new_invoice_page(request, {})


@router.post("/epayment/liner-requests/new")
async def create_invoice_request(request: Request) -> Response:
    user = current_user(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    keys = ("liner_name", "bl_no", "bl_location", "bl_type", "delivery_type", "job_no", "attachment_type_1",
            "attachment_remarks_1")
    values = {key: forms.text(form, key) for key in keys}
    values["bl_no"] = forms.code(values["bl_no"])
    problems = []
    if values["liner_name"] not in LINER_NAMES:
        problems.append("Select the liner name.")
    if not forms.BL.match(values["bl_no"]):
        problems.append("Enter the BL no.")
    if values["bl_location"] not in LOCATIONS:
        problems.append("Select the BL location.")
    if values["bl_type"] not in BL_TYPES:
        problems.append("Select the type.")
    if values["delivery_type"] not in DELIVERY_TYPES:
        problems.append("Select the type of delivery.")
    if values["attachment_type_1"] not in ATTACHMENT_TYPES:
        problems.append("Select the attachment type: the BL is a required document.")
    attached = await forms.attachment(form, "attachment_file_1", "BL attachment", problems, required=True)
    store = store_of(request)
    try:
        if problems:
            raise forms.Rejected(*problems)
        with store.change() as data:
            for earlier in data["invoice_requests"]:
                if earlier["bl_no"] == values["bl_no"] and earlier["status"] != "Rejected":
                    raise forms.Rejected(
                        f"An invoice request for BL {values['bl_no']} is already there: {earlier['ref']} ({earlier['status']})."
                    )
            item_id = secrets.token_hex(6)
            data["invoice_requests"].append({
                "id": item_id, "ref": next_ref(data, "BL", 5), "liner": values["liner_name"], "bl_no": values["bl_no"],
                "bl_location": values["bl_location"], "type": values["bl_type"],
                "delivery_type": values["delivery_type"], "job_no": values["job_no"],
                "attachments": [{
                    "type": values["attachment_type_1"], "name": attached[0],
                    "file": store.add_file(data, attached[1], attached[0]), "remarks": values["attachment_remarks_1"],
                }] if attached else [],
                "status": "Pending", "created_at": now(), "by": user,
            })
    except forms.Rejected as rejected:
        return _new_invoice_page(request, values, rejected.problems)
    return RedirectResponse(f"{LINER_REQUESTS}?submitted={item_id}", status_code=303)


# Steps 6-8: EDO DETAILS → BL Status ---------------------------------------------------------


@router.get("/epayment/bl-status")
def bl_status(request: Request, bl_no: str = "") -> Response:
    if current_user(request) is None:
        return to_login(request)
    bl_no = forms.code(bl_no)
    with store_of(request).read() as data:
        found = _latest_request(data, bl_no) if bl_no else None
    s = STATUS
    live = [
        _edo_search(s, (985, 55, 1249, 86), (1250, 55, 1388, 86), bl_no),
        f'<div id="bl-status-bl" class="live cover" style="{s.at(92, 97, 194, 118)};color:#555">{esc(bl_no)}</div>',
    ]
    invoice = found.get("invoice_no") if found else None
    if found:
        live.append(
            f'<div class="live cover" style="{s.at(70, 206, 248, 248)};padding-left:.6em">'
            f'<span><a id="bl-request" href="{EPAYMENT}/invoice-requests/{esc(found["id"])}" data-ref="{esc(found["ref"])}">'
            f'{esc(found["ref"])}</a> <a>({_day_month(found["created_at"])})</a></span><b>{esc(found["status"])}</b></div>'
        )
    else:
        live.append(
            f'<div class="live cover" style="{s.at(70, 206, 248, 248)};padding-left:.6em;white-space:normal">'
            "No request for this BL yet.</div>"
        )
    if invoice:
        live.append(
            f'<div class="live cover" style="{s.at(302, 206, 480, 248)};padding-left:.6em">'
            f'<a id="bl-invoice">{esc(invoice)} ({_day_month(found["invoice_date"])})</a><b>Final , Open</b></div>'
        )
    else:
        # The invoice isn't out: blank its card, and grey its step on the progress line.
        progress_from = 158 if found else 33
        live += [
            f'<div class="live cover" style="{s.at(302, 206, 480, 248)}"></div>',
            f'<div class="live cover" style="{s.at(310, 384, 414, 406)}"></div>',
            f'<div class="live greyline" style="{s.at(progress_from, 133, 500, 140)}"></div>',
            f'<div class="live grey" style="{s.at(357, 119, 391, 153)}"></div>',
        ]
        if not found:
            live.append(f'<div class="live grey" style="{s.at(124, 119, 158, 153)}"></div>')
    live.append(
        f'<a id="bl-new-request" class="live hot" title="New Request" href="{LINER_REQUESTS}/new" style="{s.at(80, 385, 162, 405)}"></a>'
    )
    return _page("BL Status", _shot(request, s, "\n".join(live), bl_no=bl_no))


# Steps 9-10: View Invoice Request, and the invoice's PDF -------------------------------------


@router.get("/epayment/invoice-requests/{item_id}")
def view_invoice_request(item_id: str, request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    with store_of(request).read() as data:
        item = next((dict(r) for r in data["invoice_requests"] if r["id"] == item_id), None)
        invoice_name = data["files"].get(item.get("invoice_file") or "", {}).get("name", "") if item else ""
    if item is None:
        return Response("No such request.", status_code=404)
    s = VIEW_INVOICE
    released = bool(item.get("invoice_no"))
    day = datetime.fromisoformat(item["invoice_date"]).strftime("%d-%m-%Y") if released else ""
    amount = f"{item['amount']:.2f}" if released else ""
    status = "OPEN" if released else item["status"].upper()
    attachment = (
        f'<a id="invoice-attachment" class="live cover" href="/odex/files/{esc(item["invoice_file"])}" '
        f'style="{s.at(500, 636, 940, 665)};padding-left:.3em">{esc(invoice_name)}</a>'
        if released else
        f'<div class="live cover" style="{s.at(500, 636, 940, 665)};padding-left:.3em;color:#999">'
        "The liner hasn't released the invoice yet.</div>"
    )
    live = f"""<div id="invoice-no" class="live cover line" style="{s.at(236, 258, 620, 287)}">{esc(item.get("invoice_no", ""))}</div>
<div id="invoice-date" class="live cover line" style="{s.at(236, 302, 620, 331)}">{esc(day)}</div>
<div id="invoice-amount" class="live cover line" style="{s.at(1450, 258, 1832, 288)}">{esc(amount)}</div>
<div id="invoice-status" class="live cover line" style="{s.at(843, 402, 1225, 431)}">{esc(status)}</div>
{attachment}
<div id="view-bl-no" class="live cover line" style="{s.at(645, 815, 925, 841)};color:#1a6fb5;font-weight:700">{esc(item["bl_no"])}</div>"""
    return _page("View Invoice Request", _shot(request, s, live, request_ref=item["ref"], request_status=item["status"]))
