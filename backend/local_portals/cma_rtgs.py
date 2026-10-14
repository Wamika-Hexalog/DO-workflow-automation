"""The local version of CMA CGM's "RTGS Confirmation (CMA CGM) - New Request" page,
at the real one's path (rtgs.india.cma-cgm.com/index.php/Finance/RTGS/NewRequest/)
under /cma-rtgs.

It follows screenshots of the real page: the same sections, labels, placeholders
and order, and an invoice grid that works like the real one, a spreadsheet you
click into, type, and press Enter (it looks like Handsontable, so it has
Handsontable's structure and class names). Ops file it once per payment: the
shipment payment, with its invoice number, and the security deposit, with the
Security Deposit box ticked and the OBL. CMA CGM then emails a receipt for each
(the control panel plays that part).

What the screenshots didn't show is made up: the Submit button, and the page
after it, which answers with the #outcome element the SOPs read.
"""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from . import forms, pages
from .pages import esc
from .store import next_ref, now

router = APIRouter(prefix="/cma-rtgs")

NEW_REQUEST = "/cma-rtgs/index.php/Finance/RTGS/NewRequest/"
CARRIERS = ("CMA CGM", "ANL", "APL", "CNC")
CURRENCIES = (("INR", "INR - Indian Rupees"), ("USD", "USD - US Dollar"))
GRID_ROWS = 20
MAX_INVOICES = 300

_STYLE = """
.rtgs { max-width:1160px; }
.rtgs-bar { display:flex; justify-content:flex-end; position:relative; }
.rtgs-head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; margin:8px 0 4px; }
.rtgs-head h1 { color:#0b3a6e; font-size:22px; font-weight:500; margin:0; }
.pill { border:1px solid #2aa0e6; color:#1f8fd6; background:#fff; border-radius:999px; padding:6px 18px; cursor:pointer; font:inherit; }
.carriers { position:absolute; right:0; top:110%; z-index:5; background:#fff; border:1px solid var(--line); border-radius:6px;
            padding:4px; box-shadow:0 8px 24px rgba(0,0,0,.15); }
.carriers a { display:block; padding:6px 14px; color:var(--ink); text-decoration:none; }
.section { display:flex; align-items:center; gap:8px; font-size:17px; font-weight:600; margin:20px 0 8px; }
.section::before { content:""; width:12px; height:12px; border-radius:3px; background:#e8455b; }
.panel { background:#f3f6f9; border:1px solid #dde4ea; border-radius:4px; padding:16px; }
.pair { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px 40px; }
.line { display:grid; grid-template-columns:170px minmax(0,1fr); align-items:start; gap:12px; }
.line > label { text-align:right; padding-top:6px; color:#333; }
.line input, .line select { width:100%; font:inherit; padding:6px 8px; border:1px solid #c9d2db; border-radius:2px; background:#fff; }
.line input:disabled { background:#eceff2; }
.line small { display:block; color:#2e7d32; font-size:12px; margin-top:4px; }
.date-input { display:flex; position:relative; }
.date-input input[type=text] { border-right:0; }
.calendar { border:1px solid #c9d2db; background:#eceff2; padding:0 12px; cursor:pointer; }
#calendarPicker { position:absolute; right:0; bottom:0; width:0; height:0; opacity:0; border:0; padding:0; }
.caption { font-size:12px; font-weight:600; margin:0 0 8px; }
.invoice-area { display:grid; grid-template-columns:minmax(0,660px) minmax(0,1fr); gap:24px; align-items:start; }
.side { display:flex; flex-direction:column; gap:12px; }
.note ul { margin:8px 0 0; padding-left:20px; }
.rtgs-actions { display:flex; gap:8px; margin-top:20px; }
#help { margin-top:8px; }
/* The invoice grid, after Handsontable. */
.handsontable .wtHolder { position:relative; height:270px; overflow:auto; border:1px solid #ccc; background:#fff; }
.handsontable table.htCore { border-collapse:separate; border-spacing:0; width:100%; table-layout:fixed; }
.htCore th, .htCore td { border-right:1px solid #ccc; border-bottom:1px solid #ccc; height:23px; padding:0 4px;
                         font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.htCore th { background:#f3f3f3; color:#222; font-weight:400; text-align:center; position:sticky; top:0; z-index:2; }
.htCore tbody th { position:static; }
.htCore td { background:#fff; cursor:cell; }
.htCore td.htCenter { text-align:center; }
.htCore td.htRight { text-align:right; }
.htCore td.htDimmed { background:#d9d9d9; color:#777; }
.htCore td.htInvalid { background:#ff4c42; color:#fff; }
.htCore td.current { box-shadow:inset 0 0 0 2px #4b89ff; }
textarea.handsontableInput { position:absolute; z-index:3; margin:0; padding:2px 4px; border:2px solid #5292f7; resize:none;
                             font:13px/1.4 system-ui,sans-serif; opacity:0; pointer-events:none; overflow:hidden; }
textarea.handsontableInput.open { opacity:1; pointer-events:auto; }
@media (max-width: 760px) {
  .pair, .invoice-area { grid-template-columns:1fr; }
  .line { grid-template-columns:1fr; }
  .line > label { text-align:left; padding-top:0; }
}
"""

# The grid, the carrier menu, the calendar and the advance amount. The grid works
# like Handsontable: clicking a cell selects it, typing opens its editor, Enter or
# Tab keeps the value and moves on, Escape drops it, and pasting from Excel fills
# the cells from the selected one. Ticking Security Deposit greys out the row's
# invoice number. Submitting puts the grid's rows in the "invoices" field as JSON.
_SCRIPT = """
const grid = document.getElementById('invoiceGrid');
const holder = grid.querySelector('.wtHolder');
const body = grid.querySelector('tbody');
const editor = grid.querySelector('textarea.handsontableInput');
const COLS = ['deposit', 'invoice', 'bl', 'amount', 'remarks'];
const MAX_ROWS = %(max_rows)d;
let current = null;
let editing = false;

const tdAt = (tr, col) => tr.querySelector(`td[data-col="${col}"]`);
const ticked = (tr) => tr.querySelector('input.htCheckboxRendererInput').checked;
const readOnly = (td) => td.dataset.col === 'deposit' || (td.dataset.col === 'invoice' && ticked(td.parentElement));

function addRow() {
  const n = body.rows.length + 1;
  const tr = document.createElement('tr');
  tr.innerHTML = `<th>${n}</th>`
    + '<td data-col="deposit" class="htCenter"><input type="checkbox" class="htCheckboxRendererInput"></td>'
    + '<td data-col="invoice"></td><td data-col="bl"></td><td data-col="amount" class="htRight htNumeric"></td>'
    + '<td data-col="remarks"></td>';
  body.append(tr);
}
for (let i = 0; i < %(rows)d; i += 1) addRow();

function place() {
  const cell = current.getBoundingClientRect();
  const box = holder.getBoundingClientRect();
  editor.style.top = `${cell.top - box.top + holder.scrollTop}px`;
  editor.style.left = `${cell.left - box.left + holder.scrollLeft}px`;
  editor.style.width = `${cell.width}px`;
  editor.style.height = `${cell.height}px`;
}

function close() {
  editing = false;
  editor.classList.remove('open');
  editor.value = '';
}

function commit() {
  if (!editing) return;
  const value = editor.value.trim();
  current.textContent = value;
  if (current.dataset.col === 'amount') {
    current.classList.toggle('htInvalid', value !== '' && !(Number(value.replace(/,/g, '')) > 0));
  }
  close();
  if (value && current.parentElement === body.lastElementChild && body.rows.length < MAX_ROWS) addRow();
}

function select(td) {
  if (editing) commit();
  if (current) current.classList.remove('current');
  current = td;
  td.classList.add('current');
  place();
  editor.value = '';
  editor.focus({ preventScroll: true });
}

function open(text) {
  if (readOnly(current)) return false;
  editing = true;
  editor.value = text;
  place();
  editor.classList.add('open');
  return true;
}

function move(rows, cols) {
  const r = Math.min(Math.max(current.parentElement.sectionRowIndex + rows, 0), body.rows.length - 1);
  const c = Math.min(Math.max(COLS.indexOf(current.dataset.col) + cols, 0), COLS.length - 1);
  select(tdAt(body.rows[r], COLS[c]));
}

holder.addEventListener('mousedown', (event) => {
  const td = event.target.closest('td');
  if (!td) return;
  // A click on the box still ticks it; anywhere else, the editor keeps the focus.
  if (!event.target.matches('input.htCheckboxRendererInput')) event.preventDefault();
  select(td);
});
holder.addEventListener('dblclick', (event) => {
  const td = event.target.closest('td');
  if (td && td === current && open(td.textContent)) editor.focus();
});
body.addEventListener('change', (event) => {
  if (!event.target.matches('input.htCheckboxRendererInput')) return;
  const invoice = tdAt(event.target.closest('tr'), 'invoice');
  invoice.classList.toggle('htDimmed', event.target.checked);
  if (event.target.checked) invoice.textContent = '';
});
editor.addEventListener('keydown', (event) => {
  if (!current) return;
  const key = event.key;
  if (editing) {
    if (key === 'Enter') { event.preventDefault(); commit(); move(1, 0); }
    else if (key === 'Tab') { event.preventDefault(); commit(); move(0, event.shiftKey ? -1 : 1); }
    else if (key === 'Escape') { event.preventDefault(); close(); }
    return;
  }
  const arrows = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
  if (arrows[key]) { event.preventDefault(); move(...arrows[key]); }
  else if (key === 'Tab') { event.preventDefault(); move(0, event.shiftKey ? -1 : 1); }
  else if (key === 'Enter' || key === 'F2') { event.preventDefault(); open(current.textContent); }
  else if (key === 'Delete' || key === 'Backspace') { event.preventDefault(); if (!readOnly(current)) current.textContent = ''; }
  else if (key === ' ' && current.dataset.col === 'deposit') { event.preventDefault(); current.querySelector('input').click(); }
  else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !open('')) event.preventDefault();
});
editor.addEventListener('paste', (event) => {
  if (editing || !current) return;
  event.preventDefault();
  const lines = event.clipboardData.getData('text').replace(/\\r/g, '').split('\\n').filter((line) => line.trim());
  const start = COLS.indexOf(current.dataset.col);
  let tr = current.parentElement;
  for (const line of lines) {
    if (!tr) {
      if (body.rows.length >= MAX_ROWS) break;
      addRow();
      tr = body.lastElementChild;
    }
    line.split('\\t').forEach((value, i) => {
      const col = COLS[start + i];
      const td = col && col !== 'deposit' ? tdAt(tr, col) : null;
      if (td && !readOnly(td)) td.textContent = value.trim();
    });
    tr = tr.nextElementSibling;
  }
});

document.getElementById('rtgs-form').addEventListener('submit', () => {
  commit();
  const text = (tr, col) => tdAt(tr, col).textContent.trim();
  const rows = [...body.rows]
    .map((tr) => ({
      security_deposit: ticked(tr), invoice_no: text(tr, 'invoice'), bl_no: text(tr, 'bl'),
      amount: text(tr, 'amount'), remarks: text(tr, 'remarks'),
    }))
    .filter((r) => r.security_deposit || r.invoice_no || r.bl_no || r.amount || r.remarks);
  document.getElementById('invoices').value = JSON.stringify(rows);
});
document.getElementById('rtgs-form').addEventListener('reset', () => {
  [...body.rows].forEach((tr) => {
    tr.querySelector('input').checked = false;
    tr.querySelectorAll('td[data-col]:not([data-col="deposit"])').forEach((td) => {
      td.textContent = '';
      td.classList.remove('htDimmed', 'htInvalid');
    });
  });
  close();
});

const advance = document.getElementById('submitAdvance');
const advanceAmount = document.getElementById('advanceAmount');
advance.addEventListener('change', () => {
  advanceAmount.disabled = !advance.checked;
  if (!advance.checked) advanceAmount.value = '';
});

const picker = document.getElementById('calendarPicker');
document.getElementById('openCalendar').addEventListener('click', () => picker.showPicker && picker.showPicker());
picker.addEventListener('change', () => {
  const [y, m, d] = picker.value.split('-');
  if (d) document.getElementById('dateOfTransfer').value = `${d}-${m}-${y}`;
});

const carriers = document.getElementById('carrierMenu');
document.getElementById('changeCarrier').addEventListener('click', () => { carriers.hidden = !carriers.hidden; });
document.getElementById('needHelp').addEventListener('click', () => {
  const help = document.getElementById('help');
  help.hidden = !help.hidden;
});
"""


def _line(label: str, control: str, hint: str = "") -> str:
    hint_html = f"<small>{esc(hint)}</small>" if hint else ""
    return f'<div class="line"><label>{esc(label)} *</label><div>{control}{hint_html}</div></div>'


def _input(name: str, field_id: str, placeholder: str, extra: str = "") -> str:
    return f'<input type="text" id="{field_id}" name="{name}" placeholder="{esc(placeholder)}" autocomplete="off" {extra}>'


def _answer(body: str, status_code: int = 200) -> Response:
    page = f'<div class="rtgs"><div class="rtgs-head"><h1>RTGS Confirmation - New Request</h1></div>{body}</div>'
    return pages.page("RTGS Confirmation - New Request", page, site="cma_rtgs", status_code=status_code, style=_STYLE)


@router.get("")
def root() -> Response:
    return RedirectResponse(NEW_REQUEST, status_code=303)


@router.get("/index.php/Finance/RTGS/NewRequest/")
def new_request_page(carrier: str = "CMA CGM") -> Response:
    carrier = carrier if carrier in CARRIERS else "CMA CGM"
    carrier_links = "".join(f'<a href="{NEW_REQUEST}?carrier={esc(c)}">{esc(c)}</a>' for c in CARRIERS)
    currencies = "".join(f'<option value="{code}">{esc(name)}</option>' for code, name in CURRENCIES)
    notes = (
        "You may copy &amp; paste details from Excel directly.",
        "Maximum 300 invoices can be entered.",
        "If you are adjusting the current Invoice with Credit Note, please enter Credit Note details.",
        "Receipt will be issued in the name of invoicing party of the first invoice.",
    )
    date_control = (
        '<div class="date-input">'
        '<input type="text" id="dateOfTransfer" name="date_of_transfer" class="datepicker" autocomplete="off">'
        '<button type="button" class="calendar" id="openCalendar" aria-label="Pick the date">▦</button>'
        '<input type="date" id="calendarPicker" tabindex="-1" aria-hidden="true"></div>'
    )
    body = f"""<div class="rtgs">
<div class="rtgs-bar"><button type="button" class="pill" id="changeCarrier">Change Carrier ▾</button>
<div class="carriers" id="carrierMenu" hidden>{carrier_links}</div></div>
<div class="rtgs-head"><h1 id="pageTitle">RTGS Confirmation ({esc(carrier)}) - New Request</h1>
<button type="button" class="pill" id="needHelp">? Need Help</button></div>
<p class="hint" id="help" hidden>Enter who you are, the payment as the bank made it, and what it pays: an invoice
number, BL number and amount on each row, or for a security deposit, tick the box and enter the OBL and amount.
Each payment needs its own request. A receipt is emailed once the payment is found.</p>
<form method="post" action="{NEW_REQUEST}" id="rtgs-form" autocomplete="off">
<input type="hidden" name="carrier" value="{esc(carrier)}">
<input type="hidden" name="invoices" id="invoices" value="[]">
<div class="section">Contact Details</div>
<div class="panel pair">
{_line("Company Name & City", _input("company_name", "companyName", "Company Name"))}
{_line("Contact Number", _input("contact_number", "contactNumber", "Contact Number"))}
{_line("Contact Name", _input("contact_name", "contactName", "Contact Name"))}
{_line("Email(s)", _input("emails", "emails", "Email"), "Enter maximum 3 id's seperated by comma")}
</div>
<div class="section">Payment Details</div>
<div class="panel pair">
{_line("UTR Number", _input("utr_number", "utrNumber", "UTR Number"))}
{_line("Currency", f'<select id="currency" name="currency">{currencies}</select>')}
{_line("Your Bank A/C Nb", _input("bank_account", "bankAccount", "Your Bank A/C Number"))}
{_line("Amount", _input("amount", "amount", "Amount Transfered"))}
{_line("Date of Transfer", date_control)}
</div>
<div class="section">Invoice Details (Debit/Credit Note) *</div>
<p class="caption">For Security Deposit payment,click on checkbox below and enter OBL and Amount</p>
<div class="invoice-area">
<div id="invoiceGrid" class="handsontable"><div class="ht_master handsontable"><div class="wtHolder">
<table class="htCore"><colgroup><col style="width:44px"><col style="width:96px"><col><col><col style="width:110px"><col></colgroup>
<thead><tr><th></th><th>Security<br>Deposit</th><th>Invoice Number</th><th>BL Number</th><th>Amount</th><th>Remarks (if any)</th></tr></thead>
<tbody></tbody></table>
<textarea class="handsontableInput" tabindex="-1" autocomplete="off" spellcheck="false"></textarea>
</div></div></div>
<div class="side">
<div class="panel"><label style="display:block;text-align:center;margin-bottom:12px">
<input type="checkbox" id="submitAdvance" name="submit_advance" value="1"> Submit Advance Payment</label>
<div class="line"><label style="padding-top:6px">Advance Amount</label>
<input type="text" id="advanceAmount" name="advance_amount" placeholder="Advance Amount" disabled></div></div>
<div class="panel note"><u>* Note</u><ul>{"".join(f"<li>{n}</li>" for n in notes)}</ul></div>
</div>
</div>
<div class="rtgs-actions"><button type="submit" class="btn primary" id="submit">Submit</button>
<button type="reset" class="btn" id="reset">Reset</button></div>
</form></div>"""
    script = _SCRIPT % {"rows": GRID_ROWS, "max_rows": MAX_INVOICES}
    return pages.page("RTGS Confirmation - New Request", body, site="cma_rtgs", script=script, style=_STYLE)


def _invoice_rows(raw: str, problems: list[str]) -> list[dict]:
    """The grid's rows, checked the way the page asks for them."""
    try:
        rows = json.loads(raw or "[]")
    except ValueError:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        problems.append("The invoice details couldn't be read.")
        return []
    if len(rows) > MAX_INVOICES:
        problems.append(f"Enter at most {MAX_INVOICES} invoices.")
    checked = []
    for n, row in enumerate(rows, start=1):
        deposit = row.get("security_deposit") is True
        invoice_no = forms.code(str(row.get("invoice_no") or ""))
        bl_no = forms.code(str(row.get("bl_no") or ""))
        amount = forms.amount(str(row.get("amount") or ""))
        if deposit and invoice_no:
            problems.append(f"Row {n}: a security deposit has no invoice number.")
        if not deposit and not forms.INVOICE_NO.match(invoice_no):
            problems.append(f"Row {n}: enter the invoice number, or tick Security Deposit.")
        if not forms.BL.match(bl_no):
            problems.append(f"Row {n}: enter the BL number{' (the OBL)' if deposit else ''}.")
        if amount is None:
            problems.append(f"Row {n}: enter the amount.")
        checked.append({
            "security_deposit": deposit, "invoice_no": "" if deposit else invoice_no, "bl_no": bl_no,
            "amount": amount or 0.0, "remarks": str(row.get("remarks") or "").strip(),
        })
    return checked


@router.post("/index.php/Finance/RTGS/NewRequest/")
async def create_request(request: Request) -> Response:
    form = await request.form()
    carrier = forms.text(form, "carrier") or "CMA CGM"
    company, contact = forms.text(form, "company_name"), forms.text(form, "contact_name")
    phone = forms.text(form, "contact_number").replace(" ", "")
    addresses = forms.emails(forms.text(form, "emails"))
    utr, currency = forms.code(forms.text(form, "utr_number")), forms.text(form, "currency")
    account = forms.text(form, "bank_account").replace(" ", "")
    amount = forms.amount(forms.text(form, "amount"))
    paid_on = forms.past_dmy_date(forms.text(form, "date_of_transfer"))
    wants_advance = form.get("submit_advance") == "1"
    advance = forms.amount(forms.text(form, "advance_amount")) if wants_advance else None
    problems = []
    if carrier not in CARRIERS:
        problems.append("Choose the carrier.")
    if not company:
        problems.append("Enter the company name and city.")
    if not contact:
        problems.append("Enter the contact name.")
    if not (phone.removeprefix("+").isdigit() and 10 <= len(phone.removeprefix("+")) <= 12):
        problems.append("Enter the contact number.")
    if addresses is None:
        problems.append("Enter one to three email ids, separated by commas.")
    if not forms.UTR.match(utr):
        problems.append("Enter the UTR number.")
    if currency not in dict(CURRENCIES):
        problems.append("Choose the currency.")
    if not (account.isdigit() and 6 <= len(account) <= 20):
        problems.append("Enter your bank account number.")
    if amount is None:
        problems.append("Enter the amount transferred.")
    if paid_on is None:
        problems.append("Enter the date of transfer as DD-MM-YYYY, not in the future.")
    if wants_advance and advance is None:
        problems.append("Enter the advance amount.")
    rows = _invoice_rows(forms.text(form, "invoices"), problems)
    if not rows and advance is None:
        problems.append("Enter the invoice details: an invoice, or a ticked security deposit, on each row.")
    covered = round(sum(r["amount"] for r in rows) + (advance or 0), 2)
    if amount is not None and not problems and abs(covered - amount) > 0.005:
        problems.append(f"The rows add up to {covered:,.2f}, not the {amount:,.2f} transferred.")
    try:
        if problems:
            raise forms.Rejected(*problems)
        with request.app.state.store.change() as data:
            for earlier in data["rtgs_requests"]:
                if earlier["utr"] == utr and earlier["status"] != "Rejected":
                    raise forms.Rejected(f"UTR {utr} is already confirmed, on request {earlier['ref']}.")
            ref = next_ref(data, "RTGS", 4)
            data["rtgs_requests"].append({
                "id": secrets.token_hex(6), "ref": ref, "carrier": carrier, "company_name": company,
                "contact_name": contact, "contact_number": phone, "emails": addresses, "utr": utr,
                "currency": currency, "bank_account": account, "amount": amount, "transfer_date": paid_on,
                "invoices": rows, "advance_amount": advance,
                "bl_no": ", ".join(dict.fromkeys(r["bl_no"] for r in rows)),
                "status": "Received", "created_at": now(),
            })
    except forms.Rejected as rejected:
        outcome = pages.outcome(False, "The request was not submitted.", problems=rejected.problems)
        return _answer(f'{outcome}<p><a class="btn" id="back" href="{NEW_REQUEST}">Back</a></p>', status_code=400)
    paying = " and ".join(
        f"the security deposit on {r['bl_no']}" if r["security_deposit"] else f"invoice {r['invoice_no']}" for r in rows
    ) or "an advance"
    message = (
        f"RTGS confirmation {ref} received: UTR {utr}, {currency} {amount:,.2f} for {paying}. The receipt will be "
        f"emailed to {', '.join(addresses or [])} once the payment is found."
    )
    outcome = pages.outcome(True, message, reference=ref)
    return _answer(f'{outcome}<p><a class="btn" id="back" href="{NEW_REQUEST}">New request</a></p>')
