"""The local version of CMA CGM's "RTGS Confirmation (CMA CGM) - New Request" page,
at the real one's path under /cma-rtgs
(rtgs.india.cma-cgm.com/index.php/Finance/RTGS/NewRequest?shipcomp_code=0001).

It copies the real page's HTML (as served on 2026-10-14): the same element ids and
names, placeholders, validation rules and messages, the privacy policy tick and
the Submit button. So the SOP written against it uses the real page's selectors.
The invoice grid is Handsontable 0.28 on the real page. Here it's a stand-in with
Handsontable's structure and class names that works the same way: click a cell,
type, press Enter. Ticking Security Deposit clears and greys out the row's
invoice number, and the page posts the grid as invoice_data, like the real one:
{"data": [[security_deposit, invoice_number, bl_number, amount, remarks], ...]}.

Ops file it once per payment: the shipment payment, with its invoice number, and
the security deposit, ticked, against the OBL. A good request gets the real
page's "Submitted Successfully" page with its request reference. CMA CGM then
emails a receipt for each (the control panel plays that part).
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import forms, pages
from .pages import esc
from .store import next_number, now

router = APIRouter(prefix="/cma-rtgs")

PATH = "/cma-rtgs/index.php/Finance/RTGS/NewRequest"
CARRIERS = {"0001": "CMA CGM", "0002": "ANL"}
CURRENCIES = (("INR", "INR - Indian Rupees"), ("USD", "USD - US Dollars"))
GRID_ROWS = 15
MAX_INVOICES = 300

# The real page's email rule, from its data-validation-regexp.
EMAIL_PATTERN = r"^(\s?[^\s,]+@[^\s,]+\.[^\s,]+\s?,)*(\s?[^\s,]+@[^\s,]+\.[^\s,]+)$"

# The real page's messages, from its data-validation-error-msg attributes.
MESSAGES = {
    "company_name": "Please enter valid Company Name & City (Minimum 5 & Maximum 150 characters)",
    "contact_name": "Please enter valid Contact Name (Minimum 3 & Maximum 30 characters)",
    "contact_number": "Please enter valid Contact Number (Minimum 10 & Maximum 20 characters)",
    "contact_email": "Please enter valid Email(s)",
    "utr_number": "Please enter valid UTR Number",
    "bank_account_number": "Please enter valid Bank A/C Number",
    "transfer_date": "Please enter Transfer Date",
    "amount_transfered": "Please enter valid Amount (10 to 90000000)",
    "advance_payment_amount": "Please enter valid Amount (20 to 10000000)",
    "accept_tc": "You have to agree to our privacy policy",
}

# The fields, with the real page's validation attributes.
_FIELDS: dict[str, tuple[str, str]] = {
    "company_name": (
        "Company Name",
        'maxlength="150" data-validation="length" data-validation-length="5-150"',
    ),
    "contact_name": (
        "Contact Name",
        'maxlength="30" data-validation="length" data-validation-length="3-30"',
    ),
    "contact_number": (
        "Contact Number",
        'maxlength="20" data-validation="length" data-validation-length="10-20"',
    ),
    "contact_email": (
        "Email",
        f'maxlength="200" data-validation="custom" data-validation-regexp="{esc(EMAIL_PATTERN)}"',
    ),
    "utr_number": (
        "UTR Number",
        'data-validation="length alphanumeric" data-validation-length="min5"',
    ),
    "bank_account_number": (
        "Your Bank A/C Number",
        'data-validation="length alphanumeric" data-validation-allowing="-_" data-validation-length="min8"',
    ),
    "amount_transfered": (
        "Amount Transfered",
        'data-validation="number" data-validation-allowing="float,range[10;90000000]"',
    ),
}

_STYLE = """
.rtgs { max-width:1160px; }
.rtgs-bar { display:flex; justify-content:flex-end; position:relative; }
.rtgs-head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; margin:8px 0 4px; }
.rtgs h1 { color:#0b3a6e; font-size:22px; font-weight:500; margin:0; }
.pill { display:inline-block; border:1px solid #2aa0e6; color:#1f8fd6; background:#fff; border-radius:999px;
        padding:6px 18px; cursor:pointer; font:inherit; text-decoration:none; }
.pill.solid { background:#1aa3f0; border-color:#1aa3f0; color:#fff; font-weight:600; }
.dropdown-menu { position:absolute; right:0; top:110%; z-index:5; background:#fff; border:1px solid var(--line);
                 border-radius:6px; padding:4px; margin:0; list-style:none; box-shadow:0 8px 24px rgba(0,0,0,.15); }
.dropdown-menu a { display:block; padding:6px 14px; color:var(--ink); text-decoration:none; }
#infoMessage .alert { margin:12px 0 0; }
.alert-danger { background:#f2dede; border:1px solid #ebccd1; color:#a94442; border-radius:4px; padding:10px 14px; }
.rtgs h3 { display:flex; align-items:center; gap:8px; font-size:17px; font-weight:600; margin:20px 0 8px; }
.rtgs h3 .mark { width:12px; height:12px; border-radius:3px; background:#e8455b; }
.rtgs h6 { font-size:12px; font-weight:600; margin:0 0 8px; }
.form-box, .bg-light-blue { background:#f3f6f9; border:1px solid #dde4ea; border-radius:4px; padding:16px; }
.form-box .row { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 40px; }
.form-group { display:grid; grid-template-columns:170px minmax(0,1fr); gap:12px; align-items:start; margin-bottom:14px; }
.form-group > label { text-align:right; padding-top:6px; color:#333; }
.form-control { width:100%; font:inherit; padding:6px 8px; border:1px solid #c9d2db; border-radius:2px; background:#fff; }
.form-control:disabled { background:#eceff2; }
.help-block { display:block; color:#2e7d32; font-size:12px; margin:4px 0 0; }
.has-error .form-control { border-color:#a94442; }
.form-error, .has-error .help-block.form-error { color:#a94442; }
.input-group { display:flex; position:relative; }
.input-group .form-control { border-right:0; }
.input-group-addon { border:1px solid #c9d2db; background:#eceff2; padding:4px 12px; cursor:pointer; }
#calendar_picker { position:absolute; right:0; bottom:0; width:0; height:0; opacity:0; border:0; padding:0; }
#invoice_details { display:grid; grid-template-columns:minmax(0,660px) minmax(0,1fr); gap:24px; align-items:start; }
.side { display:flex; flex-direction:column; gap:12px; padding-top:44px; }
.side ul { margin:8px 0 0; padding-left:20px; }
.form-submit-area { display:flex; flex-wrap:wrap; justify-content:flex-end; align-items:center; gap:12px; margin-top:20px; }
.form-submit-area .form-error { flex-basis:100%; text-align:right; }
#submit_rtgs { background:#1aa3f0; border:0; color:#fff; border-radius:999px; padding:8px 30px; font:inherit; font-weight:600; cursor:pointer; }
#help { margin-top:8px; }
.ref-row { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:12px; margin:24px 0 12px; }
.ref-row h3 { margin:0; }
.summary { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px 24px; }
.summary div { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:12px; }
.summary span { text-align:right; color:#333; }
/* The invoice grid, after Handsontable. */
.handsontable .wtHolder { position:relative; height:265px; overflow:auto; border:1px solid #ccc; background:#fff; }
.handsontable table.htCore { border-collapse:separate; border-spacing:0; width:100%; table-layout:fixed; }
.htCore th, .htCore td { border-right:1px solid #ccc; border-bottom:1px solid #ccc; height:23px; padding:0 4px;
                         font-size:13px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.htCore th { background:#f3f3f3; color:#222; font-weight:400; text-align:center; position:sticky; top:0; z-index:2; }
.htCore tbody th { position:static; }
.htCore td { background:#fff; cursor:cell; }
.htCore td.htCenter { text-align:center; }
.htCore td.htNumeric { text-align:right; }
.htCore td.grey { background:lightgrey; }
.htCore td.htInvalid { background:#ff4c42; color:#fff; }
.htCore td.current { box-shadow:inset 0 0 0 2px #4b89ff; }
textarea.handsontableInput { position:absolute; z-index:3; margin:0; padding:2px 4px; border:2px solid #5292f7; resize:none;
                             font:13px/1.4 system-ui,sans-serif; opacity:0; pointer-events:none; overflow:hidden; }
textarea.handsontableInput.open { opacity:1; pointer-events:auto; }
@media (max-width: 760px) {
  .form-box .row, #invoice_details, .summary { grid-template-columns:1fr; }
  .form-group, .summary div { grid-template-columns:1fr; }
  .form-group > label, .summary span { text-align:left; padding-top:0; }
  .side { padding-top:0; }
}
"""

# The grid, the form checks, the calendar, the advance amount and the menus.
#
# The grid works like Handsontable: clicking a cell selects it, typing opens its
# editor, Enter or Tab keeps the value and moves on, Escape drops it, and pasting
# from Excel fills the cells from the selected one. Like the real page's
# beforeChange hook, ticking Security Deposit clears and greys out the invoice
# number, and an invoice number unticks Security Deposit.
#
# The checks follow jQuery Form Validator, which the real page uses: on Submit,
# each field's data-validation rule is checked, and a failing field gets its
# data-validation-error-msg in a span.form-error, with nothing sent.
_SCRIPT = r"""
const grid = document.getElementById('invoice_table');
const holder = grid.querySelector('.wtHolder');
const body = grid.querySelector('tbody');
const editor = grid.querySelector('textarea.handsontableInput');
const COLS = ['security_deposit', 'invoice_number', 'bol_number_submitted', 'amount', 'remarks'];
const MAX_ROWS = %(max_rows)d;
let current = null;
let editing = false;

const tdAt = (tr, col) => tr.querySelector(`td[data-col="${col}"]`);
const ticked = (tr) => tr.querySelector('input.htCheckboxRendererInput').checked;
const readOnly = (td) => td.dataset.col === 'security_deposit'
  || (td.dataset.col === 'invoice_number' && ticked(td.parentElement));

function addRow() {
  const n = body.rows.length;
  const tr = document.createElement('tr');
  tr.innerHTML = `<th><div class="relative"><span class="rowHeader">${n + 1}</span></div></th>`
    + `<td class="htCenter htMiddle" data-col="security_deposit"><input class="htCheckboxRendererInput" type="checkbox" autocomplete="off" tabindex="-1" data-row="${n}" data-col="0"></td>`
    + '<td data-col="invoice_number"></td><td data-col="bol_number_submitted"></td>'
    + '<td class="htNumeric" data-col="amount"></td><td data-col="remarks"></td>';
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

function setDeposit(tr, on) {
  tr.querySelector('input.htCheckboxRendererInput').checked = on;
  const invoice = tdAt(tr, 'invoice_number');
  if (on) invoice.textContent = '';
  invoice.classList.toggle('grey', on);
  invoice.classList.toggle('white', !on);
}

function commit() {
  if (!editing) return;
  let value = editor.value.trim();
  const col = current.dataset.col;
  if (col === 'remarks' && value.length > 60) {
    alert('Maximum 60 characters allowed in Remarks field. Extra characters removed automatically. Text: ' + value);
    value = value.slice(0, 60);
  }
  if (col === 'amount' && value !== '') {
    const number = Number(value.replace(/,/g, ''));
    const ok = !Number.isNaN(number);
    current.classList.toggle('htInvalid', !ok);
    if (ok) value = number.toFixed(2);
  }
  current.textContent = value;
  if (col === 'invoice_number' && value) setDeposit(current.parentElement, false);
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
  if (event.target.matches('input.htCheckboxRendererInput')) setDeposit(event.target.closest('tr'), event.target.checked);
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
  else if (key === ' ' && current.dataset.col === 'security_deposit') { event.preventDefault(); current.querySelector('input').click(); }
  else if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !open('')) event.preventDefault();
});
editor.addEventListener('paste', (event) => {
  if (editing || !current) return;
  event.preventDefault();
  const lines = event.clipboardData.getData('text').replace(/\r/g, '').split('\n').filter((line) => line.trim());
  const start = COLS.indexOf(current.dataset.col);
  let tr = current.parentElement;
  for (const line of lines) {
    if (!tr) {
      if (body.rows.length >= MAX_ROWS) break;
      addRow();
      tr = body.lastElementChild;
    }
    line.split('\t').forEach((value, i) => {
      const col = COLS[start + i];
      const td = col && col !== 'security_deposit' ? tdAt(tr, col) : null;
      if (td && !readOnly(td)) td.textContent = value.trim();
    });
    tr = tr.nextElementSibling;
  }
});

// What hot.getData() gives the real page: every row, as an array of its cells.
function gridData() {
  const cell = (tr, col) => tdAt(tr, col).textContent.trim() || null;
  return [...body.rows].map((tr) => {
    const amount = cell(tr, 'amount');
    const number = amount === null ? null : Number(amount.replace(/,/g, ''));
    return [ticked(tr), cell(tr, 'invoice_number'), cell(tr, 'bol_number_submitted'),
            number === null || Number.isNaN(number) ? amount : number, cell(tr, 'remarks')];
  });
}

const form = document.getElementById('rtgsForm');

function passes(rule, value, input) {
  const d = input.dataset;
  if (rule === 'required') return input.type === 'checkbox' ? input.checked : value !== '';
  if (rule === 'length') {
    const spec = d.validationLength;
    if (spec.startsWith('min')) return value.length >= Number(spec.slice(3));
    const [low, high] = spec.split('-').map(Number);
    return value.length >= low && value.length <= high;
  }
  if (rule === 'alphanumeric') {
    const allowing = d.validationAllowing || '';
    return value !== '' && [...value].every((c) => /[A-Za-z0-9]/.test(c) || allowing.includes(c));
  }
  if (rule === 'custom') return new RegExp(d.validationRegexp).test(value);
  if (rule === 'date') {
    if (!/^\d{2}-\d{2}-\d{4}$/.test(value)) return false;
    const [dd, mm, yyyy] = value.split('-').map(Number);
    const day = new Date(yyyy, mm - 1, dd);
    return day.getFullYear() === yyyy && day.getMonth() === mm - 1 && day.getDate() === dd;
  }
  if (rule === 'number') {
    const number = Number(value);
    if (value === '' || Number.isNaN(number)) return false;
    const range = /range\[(\d+);(\d+)\]/.exec(d.validationAllowing || '');
    return !range || (number >= Number(range[1]) && number <= Number(range[2]));
  }
  return true;
}

function validate() {
  form.querySelectorAll('.form-error').forEach((e) => e.remove());
  form.querySelectorAll('.has-error').forEach((e) => e.classList.remove('has-error'));
  let ok = true;
  form.querySelectorAll('[data-validation]').forEach((input) => {
    if (input.disabled) return;
    const value = input.value.trim();
    if (input.dataset.validation.split(' ').every((rule) => passes(rule, value, input))) return;
    ok = false;
    const message = document.createElement('span');
    message.className = 'help-block form-error';
    message.textContent = input.dataset.validationErrorMsg;
    const container = input.dataset.validationErrorMsgContainer;
    if (container) document.querySelector(container).append(message);
    else if (input.type === 'checkbox') input.closest('.form-submit-area').append(message);
    else input.parentElement.append(message);
    input.closest('.form-group, .form-submit-area').classList.add('has-error');
  });
  return ok;
}

form.addEventListener('submit', (event) => {
  commit();
  document.getElementById('invoice_data').value = JSON.stringify({ data: gridData() });
  if (!validate()) event.preventDefault();
});

document.getElementById('advance_payment').addEventListener('click', (event) => {
  document.getElementById('advance_payment_amount').disabled = !event.target.checked;
});
['amount_transfered', 'advance_payment_amount'].forEach((id) => {
  document.getElementById(id).addEventListener('keyup', (event) => {
    event.target.value = event.target.value.replace(/,/g, '');
  });
});

const picker = document.getElementById('calendar_picker');
document.getElementById('transfer_date_button').addEventListener('click', () => picker.showPicker && picker.showPicker());
picker.addEventListener('change', () => {
  const [y, m, d] = picker.value.split('-');
  if (d) document.getElementById('transfer_date').value = `${d}-${m}-${y}`;
});

const carriers = document.getElementById('carrier_menu');
document.getElementById('change_carrier').addEventListener('click', (event) => {
  event.preventDefault();
  carriers.hidden = !carriers.hidden;
});
document.getElementById('show_user_guide').addEventListener('click', () => {
  const help = document.getElementById('help');
  help.hidden = !help.hidden;
});
"""


def _carrier_menu(code: str) -> str:
    others = "".join(
        f'<li><a href="{PATH}?shipcomp_code={other}">{esc(name)}</a></li>' for other, name in CARRIERS.items() if other != code
    )
    return (
        '<div class="rtgs-bar"><a href="#" class="pill" id="change_carrier">Change Carrier ▾</a>'
        f'<ul class="dropdown-menu" id="carrier_menu" hidden>{others}</ul></div>'
    )


def _group(field_id: str, label: str, control: str, hint: str = "") -> str:
    help_block = f'<p class="help-block">{esc(hint)}</p>' if hint else ""
    return (
        f'<div class="form-group"><label for="{field_id}" class="col-md-4 control-label">{esc(label)} *</label>'
        f'<div class="col-md-7">{control}{help_block}</div></div>'
    )


def _text_field(field_id: str, values: dict[str, str]) -> str:
    placeholder, rules = _FIELDS[field_id]
    return (
        f'<input type="text" class="form-control" id="{field_id}" name="{field_id}" placeholder="{esc(placeholder)}" '
        f'{rules} data-validation-error-msg="{esc(MESSAGES[field_id])}" value="{esc(values.get(field_id, ""))}">'
    )


def _form_page(code: str, values: dict[str, str] | None = None, problems: list[str] | None = None) -> Response:
    values = values or {}
    carrier = CARRIERS[code]
    alert = (
        '<p class="alert alert-danger">The request was not submitted. ' + " ".join(esc(p) for p in problems) + "</p>"
        if problems else ""
    )
    currencies = "".join(
        f'<option value="{value}"{" selected" if values.get("currency") == value else ""}>{esc(name)}</option>'
        for value, name in CURRENCIES
    )
    date_control = (
        '<div class="input-group date" id="transfer_date_group">'
        '<input type="text" class="form-control hasDatepicker" name="transfer_date" id="transfer_date" '
        'data-validation="date" data-validation-format="dd-mm-yyyy" data-validation-require-leading-zero="true" '
        'data-validation-error-msg-container="#error-transfer-date" '
        f'data-validation-error-msg="{esc(MESSAGES["transfer_date"])}" value="{esc(values.get("transfer_date", ""))}">'
        '<span class="input-group-addon" id="transfer_date_button">▦</span>'
        '<input type="date" id="calendar_picker" tabindex="-1" aria-hidden="true"></div>'
        '<span id="error-transfer-date"></span>'
    )
    notes = (
        "You may copy &amp; paste details from Excel directly.",
        "Maximum 300 invoices can be entered.",
        "If you are adjusting the current Invoice with Credit Note, please enter Credit Note details.",
        "Receipt will be issued in the name of invoicing party of the first invoice.",
    )
    body = f"""<div class="rtgs">
{_carrier_menu(code)}
<div id="infoMessage" class="container">{alert}</div>
<div class="rtgs-head"><h1>RTGS Confirmation ({esc(carrier)}) - New Request</h1>
<a class="pill" id="show_user_guide">? Need Help</a></div>
<p class="hint" id="help" hidden>Enter your contact details, the payment as your bank made it (the exact amount and
its UTR), and what it pays: the invoice number, BL number and amount, one invoice per row. For a security deposit,
tick the box and enter the OBL and amount. Accept the privacy policy, then Submit.</p>
<form action="{PATH}?shipcomp_code={code}" class="form-horizontal" id="rtgsForm" autocomplete="off" method="post" accept-charset="utf-8">
<input type="hidden" name="shipcomp_code" id="shipcomp_code" value="{code}"/>
<h3><span class="mark"></span> Contact Details</h3>
<div class="form-box bg-light-blue" id="contact_details"><div class="row">
<div>{_group("company_name", "Company Name & City", _text_field("company_name", values))}
{_group("contact_name", "Contact Name", _text_field("contact_name", values))}</div>
<div>{_group("contact_number", "Contact Number", _text_field("contact_number", values))}
{_group("contact_email", "Email(s)", _text_field("contact_email", values), "Enter maximum 3 id's seperated by comma")}</div>
</div></div>
<h3><span class="mark"></span> Payment Details</h3>
<div class="form-box bg-light-blue"><div class="row">
<div>{_group("utr_number", "UTR Number", _text_field("utr_number", values))}
{_group("bank_account_number", "Your Bank A/C Nb", _text_field("bank_account_number", values))}
{_group("transfer_date", "Date of Transfer", date_control)}</div>
<div>{_group("currency", "Currency", f'<select class="form-control" id="currency" name="currency">{currencies}</select>')}
{_group("amount_transfered", "Amount", _text_field("amount_transfered", values))}</div>
</div></div>
<div class="row" id="invoice_details">
<div><h3><span class="mark"></span> Invoice Details (Debit/Credit Note) *</h3>
<h6>For Security Deposit payment,click on checkbox below and enter OBL and Amount</h6>
<div id="invoice_table" class="handsontable htRowHeaders htColumnHeaders"><div class="ht_master handsontable"><div class="wtHolder">
<table class="htCore"><colgroup><col style="width:50px"><col style="width:96px"><col><col><col style="width:110px"><col></colgroup>
<thead><tr><th></th><th>Security<br>Deposit</th><th>Invoice Number</th><th>BL Number</th><th>Amount</th><th>Remarks (if any)</th></tr></thead>
<tbody></tbody></table>
<textarea class="handsontableInput" tabindex="-1" autocomplete="off" spellcheck="false"></textarea>
</div></div></div></div>
<div class="side">
<div class="bg-light-blue"><label for="advance_payment" style="display:block;text-align:center;margin-bottom:12px">
<input type="checkbox" id="advance_payment" name="advance_payment" value="Y"> Submit Advance Payment</label>
<div class="form-group"><label for="advance_payment_amount" class="col-md-4 control-label">Advance Amount</label>
<div class="col-md-7"><input type="text" class="form-control" id="advance_payment_amount" name="advance_payment_amount"
placeholder="Advance Amount" data-validation="number" data-validation-allowing="float,range[20;10000000]"
data-validation-error-msg="{esc(MESSAGES["advance_payment_amount"])}" value="" disabled="disabled"></div></div></div>
<div class="bg-light-blue"><p><u>* Note</u></p><ul>{"".join(f"<li>{n}</li>" for n in notes)}</ul></div>
</div>
</div>
<div class="form-submit-area">
<input type="hidden" value="" id="invoice_data" name="invoice_data">
<input type="checkbox" id="accept_tc" name="accept_tc" data-validation="required"
data-validation-error-msg="{esc(MESSAGES["accept_tc"])}"><label for="accept_tc">&nbsp; I have read and accept
<a href="#privacy-policy">CMA CGM privacy policy</a></label>
<input type="submit" name="submit_rtgs" id="submit_rtgs" class="btn btn-rounded btn-primary-blue" value="Submit">
</div>
</form></div>"""
    script = _SCRIPT % {"rows": GRID_ROWS, "max_rows": MAX_INVOICES}
    return pages.page(
        "RTGS Confirmation - New Request", body, site="cma_rtgs", script=script, style=_STYLE,
        status_code=400 if problems else 200,
    )


def _submitted_page(code: str, request: dict[str, Any]) -> Response:
    """The real page's answer to a good request."""
    carrier = CARRIERS[code]
    submitted = datetime.fromisoformat(request["created_at"]).strftime("%d-%b-%Y %H:%M:%S")
    transferred = date.fromisoformat(request["transfer_date"]).strftime("%d-%b-%Y")
    facts = (
        ("Request Reference", request["ref"]),
        ("Status", "submitted"),
        ("Submitted On (IST)", submitted),
        ("UTR Number", request["utr"]),
        ("Date of Transfer", transferred),
        ("Amount Transfered", f"{request['currency']}  {request['amount']:.2f}"),
    )
    body = f"""<div class="rtgs">
{_carrier_menu(code)}
<div class="rtgs-head"><h1>RTGS Confirmation Request ({esc(carrier)}) Submitted Successfully</h1></div>
<div class="ref-row"><h3 id="request_reference">Request Reference {esc(request["ref"])}</h3>
<a class="pill solid" id="submit_new_request" href="{PATH}?shipcomp_code={code}">Submit New Request</a></div>
<div class="bg-light-blue summary">{"".join(f"<div><span>{esc(k)}</span><b>{esc(v)}</b></div>" for k, v in facts)}</div>
<p>Your request has been successfully submitted. We will process your request once amount is credited in our bank account.</p>
</div>"""
    return pages.page("RTGS Confirmation - Submitted", body, site="cma_rtgs", style=_STYLE)


def _rows(raw: str, problems: list[str]) -> list[dict[str, Any]]:
    """The grid's filled rows, from invoice_data, checked."""
    try:
        data = json.loads(raw or "{}").get("data")
    except (ValueError, AttributeError):
        data = None
    if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
        problems.append("The invoice details couldn't be read.")
        return []
    rows = []
    for n, row in enumerate(data, start=1):
        deposit, invoice_no, bl_no, amount, remarks = (list(row) + [None] * 5)[:5]
        if deposit is not True and not any(str(v or "").strip() for v in (invoice_no, bl_no, amount, remarks)):
            continue  # an empty spare row
        invoice_no, bl_no = forms.code(str(invoice_no or "")), forms.code(str(bl_no or ""))
        value = forms.amount(str(amount if amount is not None else ""))
        if deposit is True and invoice_no:
            problems.append(f"Row {n}: a security deposit has no invoice number.")
        if deposit is not True and not forms.INVOICE_NO.match(invoice_no):
            problems.append(f"Row {n}: enter the invoice number, or tick Security Deposit.")
        if not forms.BL.match(bl_no):
            problems.append(f"Row {n}: enter the BL number{' (the OBL)' if deposit is True else ''}.")
        if value is None:
            problems.append(f"Row {n}: enter the amount.")
        rows.append({
            "security_deposit": deposit is True, "invoice_no": "" if deposit is True else invoice_no, "bl_no": bl_no,
            "amount": value or 0.0, "remarks": str(remarks or "").strip()[:60],
        })
    if len(rows) > MAX_INVOICES:
        problems.append(f"Maximum {MAX_INVOICES} invoices can be entered.")
    return rows


def _check(values: dict[str, str], form_checked: bool) -> list[str]:
    """The page's own rules again, as a server would: the browser's checks can be skipped."""
    problems = []
    company, contact, phone = values["company_name"], values["contact_name"], values["contact_number"]
    if not 5 <= len(company) <= 150:
        problems.append(MESSAGES["company_name"])
    if not 3 <= len(contact) <= 30:
        problems.append(MESSAGES["contact_name"])
    if not 10 <= len(phone) <= 20:
        problems.append(MESSAGES["contact_number"])
    if not re.match(EMAIL_PATTERN, values["contact_email"]) or forms.emails(values["contact_email"]) is None:
        problems.append(MESSAGES["contact_email"])
    if not (len(values["utr_number"]) >= 5 and values["utr_number"].isalnum()):
        problems.append(MESSAGES["utr_number"])
    account = values["bank_account_number"]
    if not (len(account) >= 8 and all(c.isalnum() or c in "-_" for c in account)):
        problems.append(MESSAGES["bank_account_number"])
    if forms.past_dmy_date(values["transfer_date"]) is None:
        problems.append(MESSAGES["transfer_date"])
    amount = forms.amount(values["amount_transfered"])
    if amount is None or not 10 <= amount <= 90000000:
        problems.append(MESSAGES["amount_transfered"])
    if values["currency"] not in dict(CURRENCIES):
        problems.append("Please select the Currency.")
    if not form_checked:
        problems.append(MESSAGES["accept_tc"])
    return problems


@router.get("")
@router.get("/index.php/Finance/RTGS/NewRequest")
@router.get("/index.php/Finance/RTGS/NewRequest/")
def new_request_page(shipcomp_code: str = "0001") -> Response:
    return _form_page(shipcomp_code if shipcomp_code in CARRIERS else "0001")


@router.post("/index.php/Finance/RTGS/NewRequest")
@router.post("/index.php/Finance/RTGS/NewRequest/")
async def create_request(request: Request, shipcomp_code: str = "0001") -> Response:
    form = await request.form()
    code = forms.text(form, "shipcomp_code") or shipcomp_code
    code = code if code in CARRIERS else "0001"
    keys = ("company_name", "contact_name", "contact_number", "contact_email", "utr_number", "bank_account_number",
            "transfer_date", "currency", "amount_transfered")
    values = {key: forms.text(form, key) for key in keys}
    values["utr_number"] = forms.code(values["utr_number"])
    problems = _check(values, bool(form.get("accept_tc")))
    advance = None
    if form.get("advance_payment") == "Y":
        advance = forms.amount(forms.text(form, "advance_payment_amount"))
        if advance is None or not 20 <= advance <= 10000000:
            problems.append(MESSAGES["advance_payment_amount"])
    rows = _rows(forms.text(form, "invoice_data"), problems)
    if not rows and advance is None:
        problems.append("Enter the invoice details: an invoice, or a ticked security deposit, on each row.")
    amount = forms.amount(values["amount_transfered"])
    covered = round(sum(r["amount"] for r in rows) + (advance or 0), 2)
    if amount is not None and not problems and abs(covered - amount) > 0.005:
        problems.append(f"The rows add up to {covered:,.2f}, not the {amount:,.2f} transferred.")
    try:
        if problems:
            raise forms.Rejected(*problems)
        with request.app.state.store.change() as data:
            for earlier in data["rtgs_requests"]:
                if earlier["utr"] == values["utr_number"] and earlier["status"] != "Rejected":
                    raise forms.Rejected(
                        f"UTR Number {values['utr_number']} is already submitted, request reference {earlier['ref']}."
                    )
            record = {
                "id": secrets.token_hex(6), "ref": str(91500000 + next_number(data, "rtgs_reference")),
                "carrier": CARRIERS[code], "company_name": values["company_name"],
                "contact_name": values["contact_name"], "contact_number": values["contact_number"],
                "emails": forms.emails(values["contact_email"]) or [], "utr": values["utr_number"],
                "currency": values["currency"], "bank_account": values["bank_account_number"], "amount": amount,
                "transfer_date": forms.past_dmy_date(values["transfer_date"]), "invoices": rows,
                "advance_amount": advance, "bl_no": ", ".join(dict.fromkeys(r["bl_no"] for r in rows)),
                "status": "Received", "created_at": now(),
            }
            data["rtgs_requests"].append(record)
    except forms.Rejected as rejected:
        return _form_page(code, values, rejected.problems)
    return _submitted_page(code, record)
