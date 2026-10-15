"""The local ODeX's pages that the team's step-by-step guides show: the invoice
("Delivery Order Request-First step 1.docx"), the DO request ("DO final steps
corrected.docx") and the security deposit ("Security deposit number 1.docx"). Each is
the guide's screenshot, with live elements over only the buttons and fields the process
uses; the rest is just the picture.

    guide step  page                    live over the picture
    (login)     login                   User ID, password, captcha and its refresh, Sign in and
                                        Reset; then the company and Confirm Sign In
    1-2         services (home)         ePayment's location and its arrow; Counter Process's
                                        location and its arrow
    3, 6        ePayment's main page    Requests → Liner Requests; Documents → DO Request; the EDO
                                        DETAILS search
    4           Liner Requests          Request New Invoice; our requests, over the picture's rows
    5           New Invoice Request     liner, BL no., BL location, type, type of delivery, job no.,
                                        the first attachment and its remarks; Submit and Back
    8           BL Status               the BL, its request and invoice, over the picture's
    9-10        View Invoice Request    the invoice's number, date, amount and status, and its PDF
    DO 3        DO Request              the advertisement and its close; NEW DO REQUEST; the BL
                                        search; our requests, over the picture's rows
    DO 4-5      DO Request (new)        the BL information, Add Row with its document and file,
                                        SAVE and BACK
    deposit 1   Counter Process         Security Deposit → Security Deposit & Refund
    deposit 2   Search Deposit/Refund   the BL and status search, NEW - DEPOSIT and View; our
                                        requests, over the picture's rows
    deposit 3   Security Deposit And    the requestor, payment and refund details, and the NOC;
                Refund Request          what ODeX fills in from the BL; SUBMIT and BACK below
                                        the picture, which doesn't show them
    deposit 4   the request, viewed     every value ODeX keeps, with its Request No. and Status

The screenshots show a real ODeX account, so they stay out of git: `make assets` takes
them from the guides into assets/private/odex/. The guides have no login page, so its
screenshot is the real login page as anyone sees it (make odex-login-screen). Without
them each page shows a grey stand-in of the same shape, and every live element still
works, so the SOPs and the tests run anywhere. Live elements sit at the screenshot's
own pixel coordinates (see Screen.at): a new screenshot needs its coordinates measured
again.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import forms, pages
from .odex import COOKIE, current_user, signed_in, store_of, to_login
from .pages import esc
from .settings import ACCOUNT_NAME, odex_login
from .store import next_ref, now

router = APIRouter(prefix="/odex")

HOME = "/odex/home"
EPAYMENT = "/odex/epayment"
LINER_REQUESTS = "/odex/epayment/liner-requests"
BL_STATUS = "/odex/epayment/bl-status"
DO_REQUESTS = "/odex/epayment/do-request"
COUNTER_PROCESS = "/odex/counter-process"
DEPOSITS = "/odex/counter-process/deposit-refund"
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
# The DO request's drop-downs. The guide shows only its own picks (General, Factory,
# Yes); the other options are a guess.
CARGO_TYPES = ("General", "Hazardous", "Reefer", "ODC")
DESTUFF_TYPES = ("Factory", "CFS", "DPD")
YES_NO = ("Yes", "No")
# The DO request's document list, as the guide shows it. Add Row offers the same
# names and one more: the team uploads the whole DO folder as a single PDF.
DO_DOCUMENTS = (
    "Copy of Cheque and Bond receipt", "Check List", "Bank Certification for Signature Verification", "AF Form",
    "eDO LOI", "Factory Bond Letter Of Indemnity",
)
SINGLE_PDF = "All document in a single pdf"
ROW_DOCUMENTS = (SINGLE_PDF, *DO_DOCUMENTS)
# The security deposit's choices. The guide shows only its own picks (Non-ODeX-RTGS,
# ThirdParty and the CHA, the NOC); the other options are a guess.
DEPOSIT_PAYMENT_MODES = ("Non-ODeX-RTGS", "Non-ODeX-DD/PO", "ODeX-Payment")
REFUND_TO = ("Consignee", "ThirdParty")
NOC_DOCUMENT = "NOC (on the letter head of MBL consignee)"
DEPOSIT_DOCUMENTS = (NOC_DOCUMENT, "Others")
DEPOSIT_STATUSES = ("Draft", "Deposit Requested", "Deposit Approved", "Refund Requested", "Rejected")
# Whom ODeX can refund a deposit to, and the PAN it fills in for each (demo numbers).
REFUND_PARTIES = {"JAY SHRI RAMDARBAR LOGISTICS PRIVATE LIMITED": "AAACJ0000A", ACCOUNT_NAME: "AAACD0000A"}
# What ODeX fills in once the BL is entered, from the IGM: the office's final place of
# delivery, and CMA CGM's deposit and damage liability per container (the guide's 45'
# container). The local ODeX has no IGM, so its consignee and containers are "As per IGM".
FPOD = {"NCR": "INTKD6-TUGHLAKABAD"}
DEPOSIT_PER_CONTAINER = 40000
DAMAGE_LIABILITY = 20000
AS_PER_IGM = "As per IGM"


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


LOGIN = Screen("login.png", 1430, 760, 15)
SERVICES = Screen("service.png", 1430, 683, 15)
DASHBOARD = Screen("dashboard.png", 1429, 708, 17)
REQUESTS_LIST = Screen("liner-requests.png", 1430, 623, 13.5)
NEW_INVOICE = Screen("new-invoice.png", 1918, 850, 17.5)
STATUS = Screen("bl-status.png", 1430, 688, 13)
VIEW_INVOICE = Screen("view-invoice.png", 1897, 955, 17.5)
DO_REQUEST_LIST = Screen("do-requests.png", 1430, 605, 11.5)
DO_REQUEST_FORM = Screen("do-request.png", 1918, 933, 12)
COUNTER = Screen("counter-process.png", 1430, 679, 13)
DEPOSIT_LIST = Screen("deposit-requests.png", 1918, 627, 12.5)
DEPOSIT_FORM = Screen("new-deposit.png", 1912, 859, 12.5)
DEPOSIT_VIEW = Screen("view-deposit.png", 1870, 957, 12.5)
SCREENS = {
    s.file: s
    for s in (
        LOGIN, SERVICES, DASHBOARD, REQUESTS_LIST, NEW_INVOICE, STATUS, VIEW_INVOICE, DO_REQUEST_LIST, DO_REQUEST_FORM,
        COUNTER, DEPOSIT_LIST, DEPOSIT_FORM, DEPOSIT_VIEW,
    )
}

# The real login page's parts, by their ids there, and where they sit on its
# screenshot. The live ones take the same ids and places, so the SOPs' login steps
# are the real page's. `make odex-login-screen` checks nothing has moved.
LOGIN_FIELDS = {
    "#loginId": (91, 380, 349, 420),
    "#password": (91, 430, 349, 470),
    "#valicode": (92, 481, 242, 511),  # the captcha's picture, a canvas
    "#toggle": (321, 489, 351, 519),  # its refresh icon
    "#valiIpt": (102, 517, 330, 543),
    "#signIn": (108, 560, 211, 599),
    "#reset": (228, 560, 331, 599),
}
LOGIN_CAPTCHA = (91, 480, 346, 550)  # the captcha's grey box
LOGIN_MESSAGE = (91, 370, 349, 390)  # #invDivMsg, just above the User ID
LOGIN_HEADER = (70, 304, 370, 370)  # "SIGN IN NOW"
LOGIN_BOX = (71, 370, 369, 613)  # the white box under it, down to the links
# The companies the login offers after the password, and how long ODeX waits for one.
COMPANIES = (ACCOUNT_NAME,)
PENDING_MINUTES = 10
# What the login page says when it won't let you in: the real page's words, but for
# a wrong password, whose message hasn't been seen.
LOGIN_ERRORS = {
    "user": "Please Enter User ID",
    "password": "Please Enter Password",
    "wrong-login": "Invalid User ID or Password",
    "company": "Please select Company",
}
OTHER_SESSION = "You are already logged in another session. Do you wish to close that session and proceed further?"

_STYLE = """
main { max-width:none; padding:0; }
.shot { position:relative; container-type:inline-size; width:100%; margin:0 auto; background:#fff;
        --px:calc(100cqw / var(--w)); }
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


def _has_picture(request: Request, screen: Screen) -> bool:
    return (screens_dir(request) / screen.file).is_file()


def _shot(request: Request, screen: Screen, live: str, **attrs: str) -> str:
    """The screenshot, or a stand-in of its shape, with the live elements over it."""
    if _has_picture(request, screen):
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


def _page(title: str, body: str, *, script: str = "", style: str = "", status_code: int = 200) -> Response:
    return pages.page(
        title, body, site="odex", header=False, style=_STYLE + style, script=_CLICKS + script, status_code=status_code
    )


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


# The login ---------------------------------------------------------------------

# The real login page's look, from its stylesheet. Its box is 90% opaque, which is
# where the greys come from. --px is one pixel of the screenshot.
_LOGIN_STYLE = """
.underlined { background-color:#fff; background-repeat:no-repeat;
  background-image:linear-gradient(#b1b1b1,#b1b1b1),linear-gradient(#b1b1b1,#b1b1b1),linear-gradient(#b1b1b1,#b1b1b1);
  background-size:100% max(1px,var(--px)),max(1px,var(--px)) calc(3 * var(--px)),max(1px,var(--px)) calc(3 * var(--px));
  background-position:left bottom,left bottom,right bottom; }
.login-input { position:absolute; box-sizing:border-box; margin:0; border:0; border-radius:0; outline:0;
  font-family:Roboto,Arial,Helvetica,sans-serif; font-size:calc(12 * var(--px)); color:#474747;
  padding:calc(5 * var(--px)) calc(8 * var(--px)) calc(6 * var(--px)); }
.login-input::placeholder { color:#a3a3a3; opacity:1; }
#valiIpt { background:#fff; border:max(1px,var(--px)) solid #848484; padding:var(--px) calc(2 * var(--px)); }
#valiIpt::placeholder { color:#838383; }
#valiIpt:focus { outline:auto; }
#invDivMsg { font-family:Roboto,Arial,Helvetica,sans-serif; color:#ff1a1a; font-size:calc(14 * var(--px));
  line-height:calc(20 * var(--px)); white-space:nowrap; }
.login #invDivMsg { position:absolute; z-index:2; pointer-events:none; }
#capt-cover { display:none; background:#fff; }
.login.remembered #capt-cover { display:block; }
.login.remembered .captcha-part { display:none; }
#welcome { background:#828282; color:#fff; font-family:Roboto,Arial,Helvetica,sans-serif; font-weight:700;
  font-size:calc(24 * var(--px)); text-transform:uppercase;
  border-radius:calc(10 * var(--px)) calc(10 * var(--px)) 0 0; display:flex; align-items:center; justify-content:center; }
.company-step { background:#fff; padding:calc(10 * var(--px)) calc(20 * var(--px));
  font-family:Roboto,Arial,Helvetica,sans-serif; font-size:calc(14 * var(--px)); color:#474747; }
#loginIdLabel { height:calc(25 * var(--px)); box-sizing:border-box; overflow:hidden; line-height:calc(15 * var(--px));
  padding:calc(4 * var(--px)) calc(3 * var(--px)) calc(6 * var(--px)) calc(8 * var(--px)); }
#compName { display:block; width:100%; box-sizing:border-box; margin-top:calc(15 * var(--px)); height:calc(25 * var(--px));
  padding:0 calc(26 * var(--px)) 0 calc(8 * var(--px)); border:max(1px,var(--px)) solid #aaa;
  border-radius:calc(5 * var(--px)); font:inherit; font-size:calc(13 * var(--px)); color:#444; cursor:pointer;
  appearance:none; box-shadow:inset 0 0 3px #fff,0 1px 1px rgba(0,0,0,.1);
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M0 0h10L5 6z' fill='%23444'/%3E%3C/svg%3E")
    right calc(9 * var(--px)) center / calc(9 * var(--px)) auto no-repeat,
    linear-gradient(#fff 20%,#f6f6f6 50%,#eee 52%,#f4f4f4 100%); }
.company-buttons { display:flex; align-items:flex-start; gap:calc(15 * var(--px)); margin-top:calc(20 * var(--px));
  padding-left:calc(15 * var(--px)); }
.company-buttons button { width:40%; border:0; border-radius:0; cursor:pointer; color:#fff; background:#575757;
  font:inherit; line-height:calc(18.67 * var(--px)); padding:calc(10 * var(--px)) calc(16 * var(--px)); }
.company-buttons #reset { background:#f95454; }
"""

# The real page's own script, nearly line for line: it draws the captcha, checks it
# and the empty fields before anything is sent, and remembers the User ID for 30
# days, after which that user isn't asked for the captcha. The local page adds one
# thing: the captcha's answer on its canvas (data-answer), for the local SOPs.
_LOGIN_SCRIPT = """
const CHARACTERS = 'A,B,C,D,E,F,G,H,I,J,K,L,M,N,O,P,Q,R,S,T,U,V,W,X,Y,Z,#,0,1,2,3,4,5,6,7,8,9,@,a,b,c,d,e,f,g,h,i,j,k,l,m,n,o,p,q,r,s,t,u,v,w,z,y,z,$'.split(',');
const form = document.getElementById('loginForm');
const message = document.getElementById('invDivMsg');
const loginId = document.getElementById('loginId');
let rightCode = '';
let cookieValueSplit = '';

function getImgValiCode() {
  const canvas = document.getElementById('valicode');
  const context = canvas.getContext('2d');
  const random = (n) => Math.random() * n;
  const colour = () => `rgb(${Math.floor(random(256))},${Math.floor(random(256))},${Math.floor(random(256))})`;
  canvas.width = 150;
  canvas.height = 30;
  context.fillStyle = '#9b9b9b';  // over the screenshot's own captcha
  context.fillRect(0, 0, 150, 30);
  const shown = [];
  for (let i = 0; i <= 6; i++) {
    const text = CHARACTERS[Math.floor(random(CHARACTERS.length))];
    const angle = (random(30) * Math.PI) / 180;
    const x = 10 + i * 20;
    const y = 20 + random(8);
    shown.push(text);
    context.font = 'bold 23px Roboto';
    context.translate(x, y);
    context.rotate(angle);
    context.fillStyle = '#fff';
    context.fillText(text, 0, 0);
    context.rotate(-angle);
    context.translate(-x, -y);
  }
  for (let i = 0; i <= 1; i++) {
    context.strokeStyle = colour();
    context.beginPath();
    context.moveTo(random(150), random(30));
    context.lineTo(random(150), random(30));
    context.stroke();
  }
  for (let i = 0; i < 5; i++) {
    const x = random(150);
    const y = random(30);
    context.strokeStyle = colour();
    context.beginPath();
    context.moveTo(x, y);
    context.lineTo(x + 1, y + 1);
    context.stroke();
  }
  rightCode = shown.join('');
  canvas.dataset.answer = rightCode;
}

function showHideCaptchaByCookieLoginId() {
  for (const cookie of document.cookie.split(';')) {
    const [name, value] = cookie.split('=');
    if (name.trim() === 'loginIdScript') cookieValueSplit = decodeURIComponent(value || '');
  }
  form.classList.toggle('remembered', loginId.value !== '' && loginId.value === cookieValueSplit);
}

function validateFields() {
  const password = document.getElementById('password').value;
  const valiIpt = document.getElementById('valiIpt');
  const remembered = loginId.value === cookieValueSplit;
  message.textContent = '';
  if (loginId.value === '') {
    message.textContent = 'Please Enter User ID';
  } else if (password === '') {
    message.textContent = 'Please Enter Password';
  } else if (valiIpt.value === '' && !remembered) {
    message.textContent = 'Please Enter Captcha';
  } else if (valiIpt.value !== rightCode && !remembered) {
    message.textContent = 'Please Enter Correct Captcha';
    valiIpt.value = '';
    getImgValiCode();
  } else {
    return true;
  }
  return false;
}

loginId.addEventListener('focus', () => { loginId.value = ''; });
loginId.addEventListener('change', showHideCaptchaByCookieLoginId);
document.getElementById('toggle').addEventListener('click', getImgValiCode);
document.getElementById('reset').addEventListener('click', () => { location.href = form.dataset.reset; });
form.addEventListener('submit', (event) => {
  if (!validateFields()) {
    event.preventDefault();
    return;
  }
  if (cookieValueSplit !== loginId.value) {
    document.cookie = `loginIdScript=${encodeURIComponent(loginId.value)}; max-age=${30 * 24 * 3600}; path=/odex; samesite=lax`;
  }
});
getImgValiCode();
"""

# The company step's script: the company has to be chosen, and if the login is signed
# in elsewhere, ODeX asks whether to close that session (OK) or start again.
_COMPANY_SCRIPT = """
const form = document.getElementById('loginForm');
const message = document.getElementById('invDivMsg');
form.addEventListener('submit', (event) => {
  message.textContent = '';
  if (!document.getElementById('compName').value) {
    event.preventDefault();
    message.textContent = 'Please select Company';
  }
});
document.getElementById('reset').addEventListener('click', () => { location.href = form.dataset.reset; });
if (form.dataset.otherSession) {
  if (confirm(form.dataset.otherSession)) {
    form.elements.closeOthers.value = '1';
    form.submit();
  } else {
    location.href = form.dataset.reset;
  }
}
"""


def _after_login(target: str) -> str:
    """Where to go once signed in: ODeX's own pages only, never back to the login."""
    return target if target.startswith("/odex/") and not target.startswith("/odex/log") else HOME


def _pending(request: Request) -> tuple[str, dict[str, Any] | None]:
    """This browser's session, while it has given the password but not the company."""
    token = request.cookies.get(COOKIE, "")
    cutoff = (datetime.now() - timedelta(minutes=PENDING_MINUTES)).isoformat(timespec="seconds")
    with store_of(request).read() as data:
        session = data["sessions"].get(token)
    if isinstance(session, dict) and not session["company"] and session["at"] > cutoff:
        return token, session
    return token, None


@router.get("/login")
def login_page(request: Request, next_page: str = Query(HOME, alias="next"), error: str = "") -> Response:
    s, field = LOGIN, LOGIN_FIELDS
    target = _after_login(next_page)
    reset = f"/odex/login/reset?next={quote(target)}"
    live = f"""<form id="loginForm" name="loginForm" class="login" action="/odex/login" method="post" autocomplete="off" data-reset="{esc(reset)}">
<input type="hidden" name="next" value="{esc(target)}">
<div id="invDivMsg" style="{s.at(*LOGIN_MESSAGE)}">{esc(LOGIN_ERRORS.get(error, ""))}</div>
<input id="loginId" name="loginId" class="login-input underlined" placeholder="User ID" autocomplete="off" style="{s.at(*field["#loginId"])}">
<input id="password" name="password" type="password" class="login-input underlined" placeholder="Password" autocomplete="off" style="{s.at(*field["#password"])}">
<div id="capt-cover" class="live" style="{s.at(*LOGIN_CAPTCHA)}"></div>
<canvas id="valicode" class="live captcha-part" width="150" height="30" style="{s.at(*field["#valicode"])}"></canvas>
<button id="toggle" class="live hot captcha-part" type="button" title="Another captcha" style="{s.at(*field["#toggle"])}"></button>
<input id="valiIpt" name="valiIpt" class="login-input captcha-part" placeholder="Please enter captcha code" autocomplete="off" style="{s.at(*field["#valiIpt"])}">
<button id="signIn" class="live hot" type="submit" title="Sign in" style="{s.at(*field["#signIn"])}"></button>
<button id="reset" class="live hot" type="reset" title="Reset" style="{s.at(*field["#reset"])}"></button>
</form>"""
    return _page("ODeX India", _shot(request, s, live), script=_LOGIN_SCRIPT, style=_LOGIN_STYLE)


@router.post("/login")
async def login(request: Request) -> Response:
    form = await request.form()
    target = _after_login(forms.text(form, "next"))
    login_id = forms.text(form, "loginId")
    password = str(form.get("password") or "")
    username, expected = odex_login()
    # The captcha is checked on the page, as ODeX's own is.
    if not login_id:
        error = "user"
    elif not password:
        error = "password"
    elif not (
        secrets.compare_digest(login_id.encode(), username.encode())
        and secrets.compare_digest(password.encode(), expected.encode())
    ):
        error = "wrong-login"
    else:
        error = ""
    if error:
        return RedirectResponse(f"/odex/login?error={error}&next={quote(target)}", status_code=303)
    # Through the first step: ODeX asks for the company next.
    token = secrets.token_urlsafe(24)
    cutoff = (datetime.now() - timedelta(minutes=PENDING_MINUTES)).isoformat(timespec="seconds")
    with store_of(request).change() as data:
        data["sessions"] = {
            key: session for key, session in data["sessions"].items()
            if signed_in(session) or (isinstance(session, dict) and session["at"] > cutoff)
        }
        data["sessions"][token] = {"user": username, "company": "", "at": now()}
    response = RedirectResponse(f"/odex/login/company?next={quote(target)}", status_code=303)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", path="/odex")
    return response


@router.get("/login/company")
def company_page(
    request: Request, next_page: str = Query(HOME, alias="next"), error: str = "", company: str = "", other: str = ""
) -> Response:
    """The login's second step, which the real page shows after the password: WELCOME,
    the User ID, the company to sign in as, and Confirm Sign In. ODeX's script and
    stylesheet give its ids, words and look; its screenshot hasn't been seen."""
    target = _after_login(next_page)
    _, session = _pending(request)
    if session is None:
        return RedirectResponse(f"/odex/login?next={quote(target)}", status_code=303)
    s = LOGIN
    reset = f"/odex/login/reset?next={quote(target)}"
    asking = f' data-other-session="{esc(OTHER_SESSION)}"' if other and company in COMPANIES else ""
    live = f"""<div id="welcome" class="live" style="{s.at(*LOGIN_HEADER)}">Welcome</div>
<form id="loginForm" name="loginForm" class="live company-step" action="/odex/login/company" method="post" autocomplete="off" data-reset="{esc(reset)}"{asking} style="{s.at(*LOGIN_BOX)}">
<input type="hidden" name="next" value="{esc(target)}">
<input type="hidden" name="closeOthers" value="">
<div id="invDivMsg">{esc(LOGIN_ERRORS.get(error, ""))}</div>
<div id="loginIdLabel" class="underlined">{esc(session["user"])}</div>
<select id="compName" name="compName" class="chosen-select">{_options(COMPANIES, company, "Select Company")}</select>
<div class="company-buttons">
<button id="confirmSignIn" type="submit">Confirm Sign In</button>
<button id="reset" type="button">Reset</button>
</div>
</form>"""
    return _page("ODeX India", _shot(request, s, live), script=_COMPANY_SCRIPT, style=_LOGIN_STYLE)


@router.post("/login/company")
async def confirm_sign_in(request: Request) -> Response:
    form = await request.form()
    target = _after_login(forms.text(form, "next"))
    company = forms.text(form, "compName")
    token, session = _pending(request)
    if session is None:
        return RedirectResponse(f"/odex/login?next={quote(target)}", status_code=303)
    if company not in COMPANIES:
        return RedirectResponse(f"/odex/login/company?error=company&next={quote(target)}", status_code=303)
    # One session per login: signing in again asks first, then closes the other one.
    with store_of(request).read() as data:
        others = [
            key for key, other in data["sessions"].items()
            if key != token and signed_in(other) and other["user"] == session["user"]
        ]
    if others and forms.text(form, "closeOthers") != "1":
        return RedirectResponse(
            f"/odex/login/company?other=1&company={quote(company)}&next={quote(target)}", status_code=303
        )
    with store_of(request).change() as data:
        for key in others:
            data["sessions"].pop(key, None)
        data["sessions"][token] = {**session, "company": company, "at": now()}
    return RedirectResponse(target, status_code=303)


@router.get("/login/reset")
def reset_login(request: Request, next_page: str = Query(HOME, alias="next")) -> Response:
    """Reset: drops a sign-in halfway through, and starts again."""
    token, session = _pending(request)
    if session is not None:
        with store_of(request).change() as data:
            data["sessions"].pop(token, None)
    return RedirectResponse(f"/odex/login?next={quote(_after_login(next_page))}", status_code=303)


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
    live = f"""<form id="counter-form" action="{COUNTER_PROCESS}" method="get">
<select id="counter-location" name="location" class="live field" style="{s.at(72, 243, 249, 273)}">{_options(LOCATIONS, _location(request), "")}</select>
<button id="counter-go" class="live hot" type="submit" title="Counter Process" style="{s.at(263, 242, 295, 274)}"></button>
</form>
<form id="epayment-form" action="{EPAYMENT}" method="get">
<select id="epayment-location" name="location" class="live field" style="{s.at(412, 243, 589, 273)}">{_options(LOCATIONS, _location(request), "")}</select>
<button id="epayment-go" class="live hot" type="submit" title="ePayment" style="{s.at(603, 242, 635, 274)}"></button>
</form>"""
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
const menuButtons = document.querySelectorAll('[data-menu]');
menuButtons.forEach((button) => button.addEventListener('click', (event) => {
  event.preventDefault();
  const menu = document.getElementById(button.dataset.menu);
  const opening = menu.hidden;
  menuButtons.forEach((other) => { document.getElementById(other.dataset.menu).hidden = true; });
  menu.hidden = !opening;
}));
"""


@router.get("/epayment")
def epayment(request: Request, location: str = "") -> Response:
    if current_user(request) is None:
        return to_login(request)
    if location and location not in LOCATIONS:
        return RedirectResponse(HOME, status_code=303)
    s = DASHBOARD
    live = f"""<button id="menu-requests" class="live hot" data-menu="requests-menu" title="Requests" style="{s.at(262, 0, 358, 47)}"></button>
<div id="requests-menu" class="dropdown live" hidden style="{s.at(204, 48, 441, 225)}">
<a id="menu-liner-requests" href="{LINER_REQUESTS}">Liner Requests</a>
<span>CFS Requests</span><span>DPD Requests</span><span>Terminal Request</span></div>
<button id="menu-documents" class="live hot" data-menu="documents-menu" title="Documents" style="{s.at(557, 0, 671, 47)}"></button>
<div id="documents-menu" class="dropdown live" hidden style="{s.at(487, 48, 724, 368)}">
<span>CFS GatePass</span><span>CFS Receipts</span><a id="menu-do-request" href="{DO_REQUESTS}">DO Request</a>
<span>Invoice Cum Receipt</span><span>Liner DO</span><span>Liner Receipts</span><span>View Final Invoices</span></div>
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


# The DO request: the team's second guide ("DO final steps corrected") ---------------------

# Its look, over the two screenshots.
_DO_STYLE = """
.do-list { background:#fff; font-family:Arial,Helvetica,sans-serif; color:#555; font-size:calc(11.5 * var(--px));
  padding-left:calc(14 * var(--px)); }
.do-list.as-pictured { background:transparent; }
.do-list .do-row { display:grid; grid-template-columns:repeat(9, calc(137.8 * var(--px))) 1fr; height:calc(22 * var(--px));
  align-items:center; border-bottom:1px solid #e6e6e6; }
.do-list .do-row span { padding:0 calc(4 * var(--px)); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.do-list .do-row a { color:#1a6fb5; }
.do-list .do-count { margin-top:calc(10 * var(--px)); color:#333; }
.live.ad { z-index:3; background-color:#fff; background-repeat:no-repeat; border:1px solid #c8c8c8; }
.live.ad.stand-in { display:flex; align-items:center; justify-content:center; color:#59636e; }
#ad-close { z-index:4; border-radius:50%; }
.live.field.box { border:max(1px,var(--px)) solid #8a8a8a; resize:none; line-height:1.3;
  padding:calc(2 * var(--px)) calc(4 * var(--px)); }
.live.tick { display:flex; align-items:center; justify-content:center; background:#fff; }
.live.tick input { margin:0; width:calc(13 * var(--px)); height:calc(13 * var(--px)); }
.documents { background:#fff; overflow-y:auto; overflow-x:hidden; }
.documents table { width:100%; border-collapse:collapse; table-layout:fixed; font-family:Arial,Helvetica,sans-serif;
  font-size:calc(12 * var(--px)); color:#333; }
.documents td { height:calc(28 * var(--px)); padding:0 calc(4 * var(--px)); border-bottom:1px solid #e3e3e3;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.documents tr:nth-child(even) td { background:#f9f9f9; }
.documents input[type=checkbox] { margin:0 0 0 calc(52 * var(--px)); width:calc(13 * var(--px)); height:calc(13 * var(--px)); }
.documents select { font:inherit; max-width:100%; }
.documents .doc-file-name { color:#1a6fb5; }
.documents .clip { display:inline-flex; position:relative; cursor:pointer; width:calc(15 * var(--px)); height:calc(15 * var(--px)); }
.documents .clip svg { width:100%; height:100%; }
.documents .clip input { position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; }
"""

_CLIP = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10.5 4.5 5.4 9.6a1.4 1.4 0 0 0 2 2l5.6-5.6a2.8 2.8 0 0 0'
    '-4-4L3.4 7.6a4.2 4.2 0 0 0 6 6L14 9" fill="none" stroke="#888" stroke-width="1.4" stroke-linecap="round"/></svg>'
)

# Steps 2-3: what the list does. The advertisement is the screenshot's own, shown
# over the rows until it's closed.
_DO_LIST_SCRIPT = """
document.getElementById('ad-close')?.addEventListener('click', () => {
  document.getElementById('advertisement').hidden = true;
  document.getElementById('ad-close').hidden = true;
});
"""

# Steps 4-5: ODeX fills the Liner/FF-Issuer from the BL No. once the field is left;
# Add Row puts a row on top of the document list, for one document and its file.
_DO_FORM_SCRIPT = """
const form = document.getElementById('do-request-form');
const blNo = document.getElementById('bl-no');
const liner = document.getElementById('liner-issuer');
const rows = document.querySelector('#document-rows tbody');
const documentNames = JSON.parse(form.dataset.documents);
const clip = document.querySelector('#document-rows .clip').innerHTML;
let added = 0;

blNo.addEventListener('change', async () => {
  const bl = blNo.value.trim().toUpperCase();
  let found = '';
  if (bl) {
    const answer = await fetch(`${form.dataset.lookup}?bl_no=${encodeURIComponent(bl)}`);
    if (answer.ok) found = (await answer.json()).liner || '';
  }
  liner.value = found;
  liner.setAttribute('value', found);
});

document.getElementById('add-row').addEventListener('click', () => {
  added += 1;
  const row = document.createElement('tr');
  row.className = 'added';
  row.dataset.row = String(added);
  row.innerHTML = '<td></td><td><input type="checkbox" class="doc-selected" disabled></td><td class="doc-file-name"></td>'
    + `<td><label class="clip" title="Attach">${clip}<input type="file" name="doc_file_${added}" class="doc-file" accept="application/pdf,.pdf"></label></td><td></td>`;
  const name = document.createElement('select');
  name.name = `doc_name_${added}`;
  name.className = 'doc-name';
  name.append(new Option('Select', ''), ...documentNames.map((n) => new Option(n, n)));
  row.firstChild.append(name);
  row.querySelector('.doc-file').addEventListener('change', (event) => {
    const file = event.target.files[0];
    row.querySelector('.doc-file-name').textContent = file ? file.name : '';
    row.querySelector('.doc-selected').checked = Boolean(file);
  });
  rows.prepend(row);
});

document.getElementById('delete-row').addEventListener('click', () => {
  rows.querySelector('tr.added')?.remove();
});
"""

DO_LIST_ROWS = 8  # the rows shown at once, newest first


def _liner_of(bl_no: str) -> str:
    """Who issued a BL, as ODeX looks it up from the IGM. The local one knows CMA CGM's only."""
    return LINER_NAMES[0] if forms.BL.match(bl_no) else ""


@router.get("/epayment/do-request")
def do_requests(request: Request, submitted: str = "", bl_no: str = "") -> Response:
    """Step 3: Documents → DO Request. The advertisement shows when the page is opened
    from the menu, over everything, until it's closed."""
    if current_user(request) is None:
        return to_login(request)
    s = DO_REQUEST_LIST
    bl_no = forms.code(bl_no)
    with store_of(request).read() as data:
        everything = [dict(r) for r in reversed(data["do_requests"])]
    items = [r for r in everything if not bl_no or r["bl_no"] == bl_no]
    shown = items[:DO_LIST_ROWS]
    rows = []
    for item in shown:
        status = esc(item["status"])
        if item.get("edo_file"):
            status += f' · <a class="download-edo" href="/odex/files/{esc(item["edo_file"])}">eDO</a>'
        cells = (
            esc(item["bl_no"]), "", esc(item.get("liner", "")), "", esc(item.get("destuff_type") or item.get("delivery_type", "")),
            "No", "Not via ODeX" if item.get("payment_not_via_odex", True) else "",
            datetime.fromisoformat(item["created_at"]).strftime("%d-%m-%Y %H:%M"), status, "",
        )
        rows.append(
            f'<div class="do-row" data-bl="{esc(item["bl_no"])}" data-ref="{esc(item["ref"])}" data-status="{esc(item["status"])}">'
            + "".join(f"<span>{cell}</span>" for cell in cells) + "</div>"
        )
    counted = f"Showing 1 to {len(shown)} of {len(items)} entries"
    height = 22 * max(1, len(shown)) + 36
    # With nothing to list, the picture's own empty table shows through.
    look = "live do-list" if rows else "live do-list as-pictured"
    live = [
        f'<form id="search-form" action="{DO_REQUESTS}" method="get">'
        f'<input id="search-bl" name="bl_no" class="live field" autocomplete="off" placeholder="SEARCH BL NUMBER" '
        f'value="{esc(bl_no)}" style="{s.at(1171, 44, 1347, 64)}">'
        f'<button id="search-bl-go" class="live hot" type="submit" title="Search" style="{s.at(1355, 44, 1382, 64)}"></button></form>',
        f'<a id="new-do-request" class="live hot" title="NEW DO REQUEST" href="{DO_REQUESTS}/new" style="{s.at(1323, 75, 1419, 94)}"></a>',
        f'<div class="live cover" style="{s.at(440, 173, 992, 374)}"></div>',  # the picture's own advertisement
        f'<div id="do-request-list" class="{look}" data-search="{esc(bl_no)}" style="{s.at(12, 127, 1420, 127 + height)}">'
        f'<div id="do-requests">{"".join(rows)}</div>'
        + (f'<div class="do-count">{counted}</div>' if rows else "") + "</div>",
    ]
    toast = ""
    if submitted:
        done = next((r for r in everything if r["id"] == submitted), None)
        if done:
            files = ", ".join(d["name"] for d in done["documents"])
            toast = _toast(True, f"DO request {done['ref']} for BL {done['bl_no']} saved, with {files}.", reference=done["ref"])
    elif not bl_no:
        ad_style = s.at(443, 176, 988, 370)
        if _has_picture(request, s):
            ad_style += (
                f";background-image:url('/odex/screens/{s.file}');background-size:100cqw auto;"
                "background-position:calc(-443 * var(--px)) calc(-176 * var(--px))"
            )
            ad = f'<div id="advertisement" class="live ad" style="{ad_style}"></div>'
        else:
            ad = f'<div id="advertisement" class="live ad stand-in" style="{ad_style}">Advertisement</div>'
        live.append(ad + f'<button id="ad-close" class="live hot" type="button" title="Close" style="{s.at(963, 178, 985, 196)}"></button>')
    return _page("Search DO Request", toast + _shot(request, s, "\n".join(live)), script=_DO_LIST_SCRIPT, style=_DO_STYLE)


@router.get("/epayment/do-request/bl-liner")
def do_request_liner(request: Request, bl_no: str = "") -> Response:
    """What fills the Liner/FF-Issuer once the BL No. is entered."""
    if current_user(request) is None:
        return JSONResponse({"liner": ""}, status_code=401)
    return JSONResponse({"liner": _liner_of(forms.code(bl_no))})


def _do_request_page(request: Request, values: dict[str, str], problems: list[str] | None = None) -> Response:
    s = DO_REQUEST_FORM
    toast = _toast(False, "The DO request was not saved.", problems=problems) if problems else ""
    pick = values.get
    standard = "".join(
        f'<tr data-document="{esc(name)}"><td>{esc(name)}</td><td><input type="checkbox" disabled></td><td></td>'
        f'<td><span class="clip">{_CLIP}</span></td><td></td></tr>'
        for name in DO_DOCUMENTS
    )
    columns = "".join(f'<col style="width:calc({w} * var(--px))">' for w in (734, 136, 336, 40)) + "<col>"
    ticked = " checked" if pick("payment_not_via_odex") else ""
    live = f"""<form id="do-request-form" action="{DO_REQUESTS}/new" method="post" enctype="multipart/form-data" data-lookup="{DO_REQUESTS}/bl-liner" data-documents="{esc(json.dumps(ROW_DOCUMENTS))}">
<input id="bl-no" name="bl_no" class="live field bl" autocomplete="off" value="{esc(pick("bl_no", ""))}" style="{s.at(176, 104, 466, 129)}">
<input id="liner-issuer" name="liner_issuer" class="live field" readonly tabindex="-1" value="{esc(pick("liner_issuer", ""))}" style="{s.at(642, 104, 933, 129)}">
<select id="location" name="location" class="live field" style="{s.at(1109, 104, 1400, 129)}">{_options(LOCATIONS, pick("location", _location(request)))}</select>
<select id="cargo-type" name="cargo_type" class="live field" style="{s.at(1576, 104, 1866, 129)}">{_options(CARGO_TYPES, pick("cargo_type", ""))}</select>
<select id="destuff-type" name="destuff_type" class="live field" style="{s.at(176, 137, 466, 161)}">{_options(DESTUFF_TYPES, pick("destuff_type", ""))}</select>
<select id="seaway-bl" name="seaway_bl" class="live field" style="{s.at(715, 137, 933, 161)}">{_options(YES_NO, pick("seaway_bl", ""))}</select>
<label class="live tick" title="Payment not via ODeX" style="{s.at(51, 176, 70, 195)}"><input id="payment-not-via-odex" name="payment_not_via_odex" type="checkbox" value="1"{ticked}></label>
<input id="runner-boy" name="runner_boy" class="live field" autocomplete="off" placeholder="Runner Boy" value="{esc(pick("runner_boy", ""))}" style="{s.at(620, 170, 820, 191)}">
<textarea id="remarks" name="remarks" class="live field box" style="{s.at(1109, 171, 1400, 207)}">{esc(pick("remarks", ""))}</textarea>
<input id="mobile-no" name="mobile_no" class="live field" autocomplete="off" placeholder="Mobile No" value="{esc(pick("mobile_no", ""))}" style="{s.at(1649, 170, 1866, 191)}">
<input id="hss-customer-name" name="hss_customer_name" class="live field" autocomplete="off" value="{esc(pick("hss_customer_name", ""))}" style="{s.at(176, 213, 466, 237)}">
<textarea id="factory-address" name="factory_address" class="live field box" style="{s.at(642, 215, 933, 253)}">{esc(pick("factory_address", ""))}</textarea>
<input id="factory-pin" name="factory_pin" class="live field" autocomplete="off" value="{esc(pick("factory_pin", ""))}" style="{s.at(1109, 213, 1400, 237)}">
<button id="add-row" class="live hot" type="button" title="Add Row" style="{s.at(16, 377, 97, 400)}"></button>
<button id="delete-row" class="live hot" type="button" title="Delete Row" style="{s.at(102, 377, 199, 400)}"></button>
<div id="document-list" class="live documents" style="{s.at(16, 444, 1856, 619)}"><table id="document-rows"><colgroup>{columns}</colgroup><tbody>{standard}</tbody></table></div>
<button id="save" class="live hot" type="submit" title="SAVE" style="{s.at(890, 661, 952, 685)}"></button>
</form>
<a id="back" class="live hot" title="BACK" href="{DO_REQUESTS}" style="{s.at(956, 661, 1006, 685)}"></a>"""
    return _page(
        "DO Request", toast + _shot(request, s, live), script=_DO_FORM_SCRIPT, style=_DO_STYLE,
        status_code=400 if problems else 200,
    )


@router.get("/epayment/do-request/new")
def new_do_request(request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    return _do_request_page(request, {})


@router.post("/epayment/do-request/new")
async def create_do_request(request: Request) -> Response:
    """SAVE: the request waits for CMA CGM to issue the delivery order (the control panel)."""
    user = current_user(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    keys = ("bl_no", "liner_issuer", "location", "cargo_type", "destuff_type", "seaway_bl", "runner_boy", "mobile_no",
            "remarks", "hss_customer_name", "factory_address", "factory_pin")
    values = {key: forms.text(form, key) for key in keys}
    values["bl_no"] = forms.code(values["bl_no"])
    values["payment_not_via_odex"] = "1" if form.get("payment_not_via_odex") else ""
    liner = _liner_of(values["bl_no"])
    problems = []
    if not liner:
        problems.append("Enter the BL No.")
    elif values["liner_issuer"] != liner:
        problems.append("The Liner/FF-Issuer is empty: ODeX fills it from the BL No. once you leave that field.")
    if values["location"] not in LOCATIONS:
        problems.append("Select the Location.")
    if values["cargo_type"] not in CARGO_TYPES:
        problems.append("Select the Cargo Type.")
    if values["destuff_type"] not in DESTUFF_TYPES:
        problems.append("Select the DE Stuffing Type.")
    if values["seaway_bl"] not in YES_NO:
        problems.append("Select Seaway BL/Telex BL.")
    if not values["payment_not_via_odex"]:
        problems.append("Add the payment details, or tick Payment not via ODeX.")
    mobile = re.sub(r"\D", "", values["mobile_no"])
    if values["mobile_no"] and len(mobile) != 10:
        problems.append("Enter the 10-digit Mobile No.")
    if not values["factory_address"]:
        problems.append("Enter the Factory Address.")
    if not re.fullmatch(r"\d{6}", values["factory_pin"]):
        problems.append("Enter the 6-digit Factory Location PIN.")
    documents = []
    rows = sorted(int(key[9:]) for key in form.keys() if key.startswith("doc_file_") and key[9:].isdigit())
    for row in rows:
        attached = await forms.attachment(form, f"doc_file_{row}", f"the document in row {row}", problems, required=False)
        if attached is None:
            continue
        name = forms.text(form, f"doc_name_{row}")
        if name not in ROW_DOCUMENTS:
            problems.append(f"Select the Document Name in row {row}.")
        documents.append((name, *attached))
    if not documents and not any("row" in problem for problem in problems):
        problems.append("Add a row and attach the documents: the Document List is required.")
    store = store_of(request)
    try:
        if problems:
            raise forms.Rejected(*problems)
        with store.change() as data:
            for earlier in data["do_requests"]:
                if earlier["bl_no"] == values["bl_no"] and earlier["status"] != "Rejected":
                    raise forms.Rejected(
                        f"A DO request for BL {values['bl_no']} is already there: {earlier['ref']} ({earlier['status']})."
                    )
            item_id = secrets.token_hex(6)
            data["do_requests"].append({
                "id": item_id, "ref": next_ref(data, "DOR", 4), "bl_no": values["bl_no"], "liner": liner,
                "location": values["location"], "cargo_type": values["cargo_type"], "destuff_type": values["destuff_type"],
                "seaway_bl": values["seaway_bl"], "payment_not_via_odex": True, "runner_boy": values["runner_boy"],
                "mobile_no": mobile, "remarks": values["remarks"], "hss_customer_name": values["hss_customer_name"],
                "factory_address": values["factory_address"], "factory_pin": values["factory_pin"],
                "documents": [
                    {"doc_type": name, "name": file_name, "size": len(content), "file": store.add_file(data, content, file_name)}
                    for name, file_name, content in documents
                ],
                "status": "Submitted", "created_at": now(), "by": user,
            })
    except forms.Rejected as rejected:
        return _do_request_page(request, values, rejected.problems)
    return RedirectResponse(f"{DO_REQUESTS}?submitted={item_id}", status_code=303)


# The security deposit: the team's third guide ("Security deposit number 1") --------------

# Its look, over the four screenshots.
_DEPOSIT_STYLE = """
#security-deposit-menu { padding:0; display:flex; align-items:center; }
#security-deposit-menu a { flex:1; padding:0 0 0 calc(23 * var(--px)); line-height:calc(34 * var(--px)); white-space:nowrap; }
.deposit-list { background:#fff; font-family:Arial,Helvetica,sans-serif; color:#333; }
.deposit-list.as-pictured { background:transparent; }
.deposit-list .dep-row { display:grid; height:calc(24 * var(--px)); align-items:center; border-bottom:1px solid #e6e6e6;
  grid-template-columns:calc(71 * var(--px)) calc(272 * var(--px)) calc(271 * var(--px)) repeat(3, calc(202 * var(--px)))
    calc(237 * var(--px)) calc(140 * var(--px)) 1fr; }
.deposit-list .dep-row span { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; padding-right:calc(6 * var(--px)); }
.deposit-list .row-select { margin:0 0 0 calc(8 * var(--px)); width:calc(13 * var(--px)); height:calc(13 * var(--px)); }
.deposit-list .dep-count { padding:calc(12 * var(--px)) 0 0 calc(274 * var(--px)); }
.deposit-list a { color:#1a6fb5; }
.live.field.box { border:max(1px,var(--px)) solid #8a8a8a; resize:none; line-height:1.3;
  padding:calc(2 * var(--px)) calc(4 * var(--px)); }
.live.field:disabled { background:#fff; color:#999; opacity:1; }  /* Chrome dims a disabled select: the picture would show through */
.live.cover.bl { color:#1e9bd7; font-weight:700; }
.deposit-actions { display:flex; justify-content:center; gap:10px; margin:12px auto 32px; }
.odex-btn { background:#644863; color:#fff; border:0; border-radius:5px; padding:6px 12px; cursor:pointer;
  font:14px Arial,Helvetica,sans-serif; text-decoration:none; }
.odex-btn:hover { background:#4f3850; }
"""

# Step 2: View opens the ticked request.
_DEPOSIT_LIST_SCRIPT = """
document.getElementById('view').addEventListener('click', (event) => {
  const ticked = document.querySelector('#deposit-rows .row-select:checked');
  if (!ticked) {
    document.getElementById('list-message').hidden = false;
    return;
  }
  location.href = event.currentTarget.dataset.view + encodeURIComponent(ticked.value);
});
"""

# Step 3: ODeX fills in the FPOD, Office Location, Consignee Name and Container Details once
# the BL No. and MBL No. are left, and the Refund To PAN once the third party is chosen.
_DEPOSIT_FORM_SCRIPT = """
const form = document.getElementById('deposit-form');
const parties = JSON.parse(form.dataset.parties);
const refundTo = document.getElementById('refund-to');
const party = document.getElementById('refund-to-name');
let asked = 0;

const fill = (id, value) => {
  const field = document.getElementById(id);
  field.value = value || '';
  field.setAttribute('value', field.value);
};

async function lookUp() {
  const ask = ++asked;
  delete form.dataset.details;
  const query = new URLSearchParams({
    bl_no: document.getElementById('bl-no').value.trim(), mbl_no: document.getElementById('mbl-no').value.trim(),
  });
  const answer = await fetch(`${form.dataset.lookup}?${query}`);
  const found = answer.ok ? await answer.json() : null;
  if (ask !== asked || !found) return;  // a later look-up has taken over
  fill('fpod', found.fpod);
  fill('office-location', found.office_location);
  fill('consignee-name', found.consignee);
  document.getElementById('container-rows').innerHTML = found.containers;
  if (found.known) form.dataset.details = 'filled';
}

function showParty() {
  const third = refundTo.value === 'ThirdParty';
  party.disabled = !third;
  if (!third) party.value = '';
  fill('refund-to-pan', third ? parties[party.value] : '');
}

document.getElementById('bl-no').addEventListener('change', lookUp);
document.getElementById('mbl-no').addEventListener('change', lookUp);
refundTo.addEventListener('change', showParty);
party.addEventListener('change', showParty);
showParty();
"""

DEPOSIT_LIST_ROWS = 8  # the rows shown at once, newest first
# Container Details on the new deposit's screenshot: where each column's text starts.
# The View screenshot has the table 6 px to the left and 72 px lower.
_CONTAINER_COLUMNS = (
    ("no", 23), ("size", 179), ("type", 342), ("cargo", 510), ("deposit", 644), ("liability", 822),
    ("returned", 1005), ("damage_cost", 1243), ("damage_status", 1721),
)


def _bl_details(location: str, bl_no: str, mbl_no: str) -> dict[str, Any]:
    """What ODeX fills in from the IGM once the BL No. and MBL No. are entered. The local
    ODeX has no IGM: it knows its office's place of delivery, and nothing of the consignee
    or the containers."""
    known = bool(forms.BL.match(bl_no) and forms.BL.match(mbl_no))
    return {
        "known": known,
        "fpod": FPOD.get(location, "") if known else "",
        "office_location": location if known else "",
        "consignee": AS_PER_IGM if known else "",
    }


def _containers(s: Screen, known: bool, *, dx: int = 0, dy: int = 0) -> str:
    """Container Details over the picture's sample row: one row standing in for the IGM's
    containers, the count and the totals, or nothing before the BL is entered."""
    cover = f'<div class="live cover" style="{s.at(16 + dx, 315 + dy, 1890 + dx, 378 + dy)}"></div>'
    if not known:
        return cover
    values = {
        "no": AS_PER_IGM, "size": "", "type": "", "cargo": "", "deposit": str(DEPOSIT_PER_CONTAINER),
        "liability": str(DAMAGE_LIABILITY), "returned": "", "damage_cost": "0", "damage_status": "",
    }
    cells = [
        f'<span class="cell" data-col="{key}" style="{s.at(x + dx, 315 + dy, x + dx + 160, 330 + dy)}">{esc(values[key])}</span>'
        for key, x in _CONTAINER_COLUMNS
    ]
    totals = (
        (40, 337, 300, "Showing 1 to 1 of 1 entries", ""), (36, 358, 100, "Total", ";font-weight:700"),
        (652, 356, 800, f"{DEPOSIT_PER_CONTAINER:.1f}", ""), (1221, 356, 1300, "0.0", ""),
    )
    cells += [
        f'<span class="cell" style="{s.at(x + dx, y + dy, x1 + dx, y + 15 + dy)};color:#333{extra}">{esc(text)}</span>'
        for x, y, x1, text, extra in totals
    ]
    return cover + "".join(cells)


@router.get("/counter-process")
def counter_process(request: Request, location: str = "") -> Response:
    """Step 1: Counter Process, opened for the location chosen on the services page."""
    if current_user(request) is None:
        return to_login(request)
    if location and location not in LOCATIONS:
        return RedirectResponse(HOME, status_code=303)
    s = COUNTER
    # The picture shows the menu open; the live one opens with a click, as ODeX's does.
    live = f"""<button id="menu-security-deposit" class="live hot" data-menu="security-deposit-menu" title="Security Deposit" style="{s.at(358, 0, 470, 38)}"></button>
<div id="security-deposit-menu" class="dropdown live" hidden style="{s.at(289, 33, 476, 81)}">
<a id="menu-deposit-refund" href="{DEPOSITS}">Security Deposit &amp; Refund</a></div>
<a id="home" class="live hot" title="Main menu" href="{HOME}" style="{s.at(1314, 6, 1338, 29)}"></a>"""
    response = _page("Counter Process", _shot(request, s, live), script=_MENU_SCRIPT, style=_DEPOSIT_STYLE)
    if location:
        response.set_cookie(LOCATION_COOKIE, location, samesite="lax", path="/odex")
    return response


@router.get("/counter-process/deposit-refund")
def deposit_requests(request: Request, bl_no: str = "", status: str = "", submitted: str = "") -> Response:
    """Step 2: Search Deposit/Refund Request. View opens the ticked request."""
    if current_user(request) is None:
        return to_login(request)
    s = DEPOSIT_LIST
    bl_no = forms.code(bl_no)
    status = status if status in DEPOSIT_STATUSES else ""
    with store_of(request).read() as data:
        everything = [dict(d) for d in reversed(data["deposits"])]
    items = [d for d in everything if (not bl_no or d["bl_no"] == bl_no) and (not status or d["status"] == status)]
    shown = items[:DEPOSIT_LIST_ROWS]
    rows = []
    for item in shown:
        state = esc(item["status"])
        if item.get("slip_file"):
            state += f' · <a class="download-slip" href="/odex/files/{esc(item["slip_file"])}">Slip</a>'
        cells = (
            f'<input type="checkbox" class="row-select" title="Select" value="{esc(item["id"])}">',
            esc(datetime.fromisoformat(item["created_at"]).strftime("%d-%m-%Y %H:%M")), "", esc(item["ref"]),
            esc(item["location"]), esc(item["line"]), esc(item["bl_no"]), esc(item["refund_to"]), state,
        )
        rows.append(
            f'<div class="dep-row" data-id="{esc(item["id"])}" data-bl="{esc(item["bl_no"])}" data-ref="{esc(item["ref"])}" '
            f'data-status="{esc(item["status"])}">' + "".join(f"<span>{cell}</span>" for cell in cells) + "</div>"
        )
    height = 24 * max(1, len(shown)) + 40
    # With nothing to list, the picture's own empty table shows through.
    look = "live deposit-list" if rows else "live deposit-list as-pictured"
    counted = f'<div class="dep-count">Showing 1 to {len(shown)} of {len(items)} entries</div>' if rows else ""
    live = f"""<form id="deposit-search" action="{DEPOSITS}" method="get">
<input id="search-bl" name="bl_no" class="live field" autocomplete="off" placeholder="BL NO." value="{esc(bl_no)}" style="{s.at(1354, 62, 1523, 86)}">
<select id="search-status" name="status" class="live field" style="{s.at(1554, 62, 1724, 86)}">{_options(DEPOSIT_STATUSES, status, "All")}</select>
<button id="search-go" class="live hot" type="submit" title="Search" style="{s.at(1749, 61, 1785, 87)}"></button>
</form>
<button id="view" class="live hot" type="button" title="View" data-view="{DEPOSITS}/" style="{s.at(15, 94, 76, 120)}"></button>
<div id="list-message" class="live cover" hidden style="{s.at(252, 96, 700, 118)};color:#c0392b">Select a request to view it.</div>
<a id="new-deposit" class="live hot" title="NEW - DEPOSIT" href="{DEPOSITS}/new" style="{s.at(1796, 94, 1905, 119)}"></a>
<div id="deposit-list" class="{look}" data-search="{esc(bl_no)}" style="{s.at(12, 178, 1906, 178 + height)}"><div id="deposit-rows">{"".join(rows)}</div>{counted}</div>"""
    toast = ""
    if submitted:
        done = next((d for d in everything if d["id"] == submitted), None)
        if done:
            toast = _toast(
                True, f"Deposit request {done['ref']} for BL {done['bl_no']} submitted: {done['status']}.", reference=done["ref"]
            )
    return _page(
        "Search Deposit/Refund Request", toast + _shot(request, s, live), script=_DEPOSIT_LIST_SCRIPT, style=_DEPOSIT_STYLE
    )


@router.get("/counter-process/deposit-refund/bl-details")
def deposit_bl_details(request: Request, bl_no: str = "", mbl_no: str = "") -> Response:
    """What fills the FPOD, Office Location, Consignee Name and Container Details."""
    if current_user(request) is None:
        return JSONResponse({}, status_code=401)
    details = _bl_details(_location(request), forms.code(bl_no), forms.code(mbl_no))
    return JSONResponse({**details, "containers": _containers(DEPOSIT_FORM, details["known"])})


def _deposit_page(request: Request, values: dict[str, str], problems: list[str] | None = None) -> Response:
    s = DEPOSIT_FORM
    toast = _toast(False, "The deposit request was not submitted.", problems=problems) if problems else ""
    pick = values.get
    details = _bl_details(_location(request), pick("bl_no", ""), pick("mbl_no", ""))
    pan = REFUND_PARTIES.get(pick("refund_to_name", ""), "") if pick("refund_to", "") == "ThirdParty" else ""

    def box(name: str, at: tuple[int, int, int, int], css: str = "field") -> str:
        return (
            f'<input id="{name.replace("_", "-")}" name="{name}" class="live {css}" autocomplete="off" '
            f'value="{esc(pick(name, ""))}" style="{s.at(*at)}">'
        )

    def filled_in(element_id: str, value: str, at: tuple[int, int, int, int]) -> str:
        """A field ODeX fills in itself."""
        return f'<input id="{element_id}" class="live field" readonly tabindex="-1" value="{esc(value)}" style="{s.at(*at)}">'

    looked_up = ' data-details="filled"' if details["known"] else ""
    live = f"""<form id="deposit-form" action="{DEPOSITS}/new" method="post" enctype="multipart/form-data" data-lookup="{DEPOSITS}/bl-details" data-parties="{esc(json.dumps(REFUND_PARTIES))}"{looked_up}>
<select id="line" name="line" class="live field" style="{s.at(168, 108, 463, 133)}">{_options(LINER_NAMES, pick("line", ""))}</select>
{box("bl_no", (642, 108, 937, 133), "field bl")}
{box("mbl_no", (1117, 108, 1412, 133))}
{filled_in("fpod", details["fpod"], (168, 140, 463, 165))}
{filled_in("office-location", details["office_location"], (1117, 140, 1412, 165))}
<select id="mode-of-payment" name="mode_of_payment" class="live field" style="{s.at(1591, 140, 1868, 165)}">{_options(DEPOSIT_PAYMENT_MODES, pick("mode_of_payment", ""))}</select>
{filled_in("consignee-name", details["consignee"], (168, 172, 463, 197))}
<select id="refund-to" name="refund_to" class="live field" style="{s.at(1117, 172, 1249, 197)}">{_options(REFUND_TO, pick("refund_to", ""))}</select>
<select id="refund-to-name" name="refund_to_name" class="live field" style="{s.at(1265, 172, 1412, 197)}">{_options(tuple(REFUND_PARTIES), pick("refund_to_name", ""))}</select>
{filled_in("refund-to-pan", pan, (1591, 172, 1886, 197))}
<textarea id="customer-remarks" name="customer_remarks" class="live field box" style="{s.at(642, 204, 937, 240)}">{esc(pick("customer_remarks", ""))}</textarea>
<div id="container-rows" style="display:contents">{_containers(s, details["known"])}</div>
<select id="attachment-name" name="attachment_name" class="live field" style="{s.at(37, 470, 469, 493)}">{_options(DEPOSIT_DOCUMENTS, pick("attachment_name", NOC_DOCUMENT))}</select>
<div class="live cover" style="{s.at(500, 468, 940, 494)};justify-content:center"><input id="attachment-file" name="attachment_file" type="file" accept="application/pdf,.pdf"></div>
{box("attachment_remarks", (971, 474, 1309, 497))}
{box("bank", (168, 641, 463, 666))}
{box("amount", (642, 641, 937, 666))}
{box("bank_payment_reference", (1117, 641, 1412, 666))}
<input id="payment-date" name="payment_date" class="live field" autocomplete="off" placeholder="dd-mm-yyyy" value="{esc(pick("payment_date", ""))}" style="{s.at(1591, 641, 1853, 666)}">
{box("cma_ireceipt_no", (168, 673, 463, 698))}
{box("beneficiary_name", (168, 759, 463, 784))}
{box("refund_bank_name", (642, 759, 937, 784))}
{box("branch_name", (1117, 759, 1412, 784))}
{box("ac_type", (1591, 759, 1886, 784))}
{box("ac_no", (168, 806, 463, 831))}
{box("ifsc_code", (642, 806, 937, 831))}
</form>"""
    # The guide's screenshot ends above the form's buttons, so they go under it.
    actions = (
        f'<div class="deposit-actions" style="max-width:{s.width}px">'
        '<button id="submit-deposit" class="odex-btn" type="submit" form="deposit-form">SUBMIT</button>'
        f'<a id="back" class="odex-btn" href="{DEPOSITS}">BACK</a></div>'
    )
    return _page(
        "Security Deposit And Refund Request", toast + _shot(request, s, live) + actions, script=_DEPOSIT_FORM_SCRIPT,
        style=_DEPOSIT_STYLE, status_code=400 if problems else 200,
    )


@router.get("/counter-process/deposit-refund/new")
def new_deposit(request: Request) -> Response:
    if current_user(request) is None:
        return to_login(request)
    return _deposit_page(request, {})


@router.post("/counter-process/deposit-refund/new")
async def create_deposit(request: Request) -> Response:
    """SUBMIT: the request waits for CMA CGM to approve the deposit (the control panel)."""
    user = current_user(request)
    if user is None:
        return to_login(request)
    form = await request.form()
    keys = ("line", "bl_no", "mbl_no", "mode_of_payment", "refund_to", "refund_to_name", "customer_remarks",
            "attachment_name", "attachment_remarks", "bank", "amount", "bank_payment_reference", "payment_date",
            "cma_ireceipt_no", "beneficiary_name", "refund_bank_name", "branch_name", "ac_type", "ac_no", "ifsc_code")
    values = {key: forms.text(form, key) for key in keys}
    for key in ("bl_no", "mbl_no", "bank_payment_reference", "cma_ireceipt_no", "ac_no", "ifsc_code"):
        values[key] = forms.code(values[key])
    third = values["refund_to"] == "ThirdParty"
    amount, paid_on = forms.amount(values["amount"]), forms.past_dmy_date(values["payment_date"])
    problems = []
    if values["line"] not in LINER_NAMES:
        problems.append("Select the Line.")
    if not forms.BL.match(values["bl_no"]):
        problems.append("Enter the BL No.")
    if not forms.BL.match(values["mbl_no"]):
        problems.append("Enter the MBL No.")
    if values["mode_of_payment"] not in DEPOSIT_PAYMENT_MODES:
        problems.append("Select the Mode Of Payment.")
    if values["refund_to"] not in REFUND_TO:
        problems.append("Select whom the deposit is refunded to.")
    elif third and values["refund_to_name"] not in REFUND_PARTIES:
        problems.append("Select the third party the deposit is refunded to.")
    noc = await forms.attachment(form, "attachment_file", "NOC", problems, required=third)
    if noc and values["attachment_name"] not in DEPOSIT_DOCUMENTS:
        problems.append("Select the Document Name of the attachment.")
    elif noc and third and values["attachment_name"] != NOC_DOCUMENT:
        problems.append(f"Attach the {NOC_DOCUMENT}.")
    if not values["bank"]:
        problems.append("Enter the Bank.")
    if amount is None:
        problems.append("Enter the Amount.")
    if not forms.UTR.match(values["bank_payment_reference"]):
        problems.append("Enter the Bank Payment Reference.")
    if paid_on is None:
        problems.append("Enter the Payment date as dd-mm-yyyy, not in the future.")
    if not forms.INVOICE_NO.match(values["cma_ireceipt_no"]):
        problems.append("Enter the CMA iReceipt No.")
    for key, label in (("beneficiary_name", "Beneficiary Name"), ("refund_bank_name", "Bank Name"),
                       ("branch_name", "Branch Name"), ("ac_type", "A/C Type")):
        if not values[key]:
            problems.append(f"Enter the {label} of the refund.")
    if not re.fullmatch(r"\d{6,20}", values["ac_no"]):
        problems.append("Enter the A/C No. of the refund.")
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", values["ifsc_code"]):
        problems.append("Enter the IFSC Code of the refund.")
    location = _location(request)
    details = _bl_details(location, values["bl_no"], values["mbl_no"])
    store = store_of(request)
    try:
        if problems:
            raise forms.Rejected(*problems)
        with store.change() as data:
            for earlier in data["deposits"]:
                if earlier["bl_no"] == values["bl_no"] and earlier["status"] != "Rejected":
                    raise forms.Rejected(
                        f"A deposit request for BL {values['bl_no']} is already there: {earlier['ref']} ({earlier['status']})."
                    )
            item_id = secrets.token_hex(6)
            data["deposits"].append({
                "id": item_id, "ref": next_ref(data, "SD", 5), "line": values["line"], "bl_no": values["bl_no"],
                "mbl_no": values["mbl_no"], "location": location, "fpod": details["fpod"], "consignee": details["consignee"],
                "mode_of_payment": values["mode_of_payment"], "refund_to": values["refund_to"],
                "refund_to_name": values["refund_to_name"] if third else "",
                "refund_pan": REFUND_PARTIES.get(values["refund_to_name"], "") if third else "",
                "customer_remarks": values["customer_remarks"],
                "attachments": [{
                    "doc_type": values["attachment_name"], "name": noc[0], "file": store.add_file(data, noc[1], noc[0]),
                    "remarks": values["attachment_remarks"],
                }] if noc else [],
                "bank_name": values["bank"], "amount": amount, "utr": values["bank_payment_reference"],
                "payment_date": paid_on, "ireceipt_no": values["cma_ireceipt_no"],
                "refund_account": {
                    "beneficiary": values["beneficiary_name"], "bank": values["refund_bank_name"],
                    "branch": values["branch_name"], "type": values["ac_type"], "number": values["ac_no"],
                    "ifsc": values["ifsc_code"],
                },
                "status": "Deposit Requested", "created_at": now(), "by": user,
            })
    except forms.Rejected as rejected:
        return _deposit_page(request, values, rejected.problems)
    return RedirectResponse(f"{DEPOSITS}?submitted={item_id}", status_code=303)


@router.get("/counter-process/deposit-refund/{item_id}")
def view_deposit(item_id: str, request: Request) -> Response:
    """Step 4: the request as ODeX keeps it, opened with View from the search."""
    if current_user(request) is None:
        return to_login(request)
    with store_of(request).read() as data:
        item = next((dict(d) for d in data["deposits"] if d["id"] == item_id), None)
    if item is None:
        return Response("No such request.", status_code=404)
    s = DEPOSIT_VIEW
    account = item["refund_account"]
    attached = item["attachments"][0] if item["attachments"] else {"doc_type": "", "name": "", "file": "", "remarks": ""}

    def value(at: tuple[int, int, int, int], text: str, element_id: str = "", css: str = "") -> str:
        named = f' id="{element_id}"' if element_id else ""
        return f'<div{named} class="live cover line {css}" style="{s.at(*at)}">{esc(text)}</div>'

    link = (
        f'<a id="attachment-link" href="/odex/files/{esc(attached["file"])}">{esc(attached["name"])}</a>'
        if attached["file"] else ""
    )
    # The top of the View screenshot sits 2 px lower than the form's, and the rest 72 px
    # lower, under "For office use only", which ODeX fills in after a refund.
    live = [
        value((162, 110, 457, 135), item["line"]),
        value((636, 110, 931, 135), item["bl_no"], "view-bl-no", "bl"),
        value((1111, 110, 1406, 135), item["mbl_no"]),
        value((162, 142, 457, 167), item["fpod"]),
        value((636, 142, 931, 167), item["ref"], "request-no"),
        value((1111, 142, 1406, 167), item["location"]),
        value((1585, 142, 1860, 167), item["mode_of_payment"]),
        value((162, 174, 457, 199), item["consignee"]),
        value((1111, 174, 1243, 199), item["refund_to"]),
        value((1259, 174, 1406, 199), item["refund_to_name"]),
        value((1585, 174, 1869, 199), item["refund_pan"]),
        value((162, 208, 457, 233), item["status"], "request-status"),
        f'<div class="live cover" style="{s.at(638, 208, 929, 241)};white-space:normal">{esc(item["customer_remarks"])}</div>',
        _containers(s, True, dx=-6, dy=72),
        value((31, 542, 463, 565), attached["doc_type"]),
        f'<div class="live cover" style="{s.at(494, 540, 940, 566)}">{link}</div>',
        value((965, 546, 1303, 569), attached["remarks"]),
        value((162, 713, 457, 738), item["bank_name"]),
        value((636, 713, 931, 738), f"{item['amount']:.2f}"),
        value((1111, 713, 1406, 738), item["utr"]),
        value((1585, 713, 1848, 738), datetime.fromisoformat(item["payment_date"]).strftime("%d-%m-%Y")),
        value((162, 745, 457, 770), item["ireceipt_no"]),
        value((162, 831, 457, 856), account["beneficiary"]),
        value((636, 831, 931, 856), account["bank"]),
        value((1111, 831, 1406, 856), account["branch"]),
        value((1585, 831, 1869, 856), account["type"]),
        value((162, 878, 457, 903), account["number"]),
        value((636, 878, 931, 903), account["ifsc"]),
        f'<a id="back" class="live hot" title="BACK" href="{DEPOSITS}" style="{s.at(789, 928, 839, 953)}"></a>',
    ]
    shot = _shot(request, s, "\n".join(live), request_ref=item["ref"], request_status=item["status"])
    return _page("Security Deposit And Refund Request", shot, style=_DEPOSIT_STYLE)
