# Local SOPs

The CMA CGM DO process uses two websites: ODeX, and CMA CGM's RTGS payments page. The SOPs here drive local versions of both (`backend/local_portals`). That lets the whole process be shown, and every SOP be proven, without touching a real site. The browser agent is python-brain's, and the SOPs use python-brain's format. All their steps are scripted Playwright steps, so no model is called.

The local RTGS page copies screenshots of CMA CGM's real one. The local ODeX follows the SOP sheet of `DO PROCESS FLOW E2E.xlsx` and the sample documents in `Docs/`, since its real pages haven't been seen yet. See [Moving to the real sites](#moving-to-the-real-sites).

## Run them

```bash
make portals                                              # http://127.0.0.1:8100
make portal-step JOB=0431 STEP=odex_request_invoice SHOW=1
```

`portal-step` runs one step for a job in the app's database. `app/portal_params.py` builds the values from the job, and the run log goes on the job. `SHOW=1` opens the browser in slow motion. For a per-house-bill step, `HBL=...` runs only that house bill.

Between steps, play CMA CGM on the control panel at http://127.0.0.1:8100/control: verify the invoice, email the receipts, approve the deposit, issue the eDO. You can also turn on autoplay so each answer comes by itself a few seconds later. The panel also shows the emails CMA CGM would send, with their attachments. Nothing is actually sent.

`make demo-local` runs the app, the Inngest dev server and the portals together. With it, approving a DO request in the app raises it on the local ODeX, in a visible browser.

ODeX login: `hexalog-demo` / `local-demo-only`, from `LOCAL_ODEX_USERNAME` and `LOCAL_ODEX_PASSWORD` (`app/config.py`). It's not a secret: the portal only runs on this machine.

## The steps and their data

Each step lists the page fields, what the SOP types into each one, and where in the database the value comes from. "Job" fields are on the job's form in the app. "Fixed details" are shared by every job. If anything is missing, the step won't start, and the error names every missing item.

### 1. Request the DO invoice: `odex_request_invoice`

ODeX → Request → Liner Request → Request New Invoice. Once per MBL, after CMA CGM files the IGM.

| Page field | Filled with |
|---|---|
| Liner | Fixed details: Liner on ODeX (`odex_liner`, default CMA CGM) |
| Invoice type | Always "Delivery Order" |
| B/L number | Job: Master B/L no. (`mbl_no`): CMA CGM invoices the master bill |
| Container numbers | Job: Container nos. (`containers`) |
| Email(s) for the invoice | Fixed details: Emails for CMA CGM's invoice and receipts (`do_mailbox`) |
| Remarks | The job number |

It reads back ODeX's request number (`odex_invoice_request_ref`). CMA CGM then verifies the request and emails the invoice. Its number and amount go on the job as DO invoice no. and DO invoice amount.

### 2. Confirm the payments: `cma_rtgs_shipment_payment` and `cma_rtgs_security_deposit`

CMA CGM's "RTGS Confirmation (CMA CGM) - New Request" page (`rtgs.india.cma-cgm.com/index.php/Finance/RTGS/NewRequest/`), after Finance pays and shares the UTRs. It's filed twice per MBL, once for each payment, with the same SOP (`cma_rtgs/new_request.yaml`):

- **The shipment payment**: invoice grid row 1 holds the DO invoice number, the B/L and the amount.
- **The security deposit**: row 1 has Security Deposit ticked, the OBL (the master B/L) and the amount. The invoice number cell is greyed out.

| Page field | Filled with |
|---|---|
| Company Name & City | Fixed details: Forwarder (`forwarder_name`) |
| Contact Number | Fixed details: Contact number on CMA CGM's RTGS page (`rtgs_contact_number`, default 8802757004) |
| Contact Name | Fixed details: Contact name on CMA CGM's RTGS page (`rtgs_contact_name`, default Kishan Rawat) |
| Email(s) | Fixed details: Emails for CMA CGM's invoice and receipts (`do_mailbox`, at most 3, default kishan@hexalog.in,customs-ops@Hexalog.in) |
| UTR Number | Job: UTR for the shipment payment (`payment_utr`), or UTR for the security deposit (`deposit_utr`) |
| Currency | Always INR - Indian Rupees |
| Your Bank A/C Nb | Fixed details: Bank account no. that pays CMA CGM (`payer_account_no`). It isn't in the repo: enter it once in the app |
| Amount | Job: DO invoice amount (`do_invoice_amount`), or Security deposit (`deposit_amount`, default 40,000), as 54720.68 |
| Date of Transfer | Job: Date of transfer (`payment_date`), as DD-MM-YYYY |
| Grid row 1: Security Deposit | Ticked for the security deposit only |
| Grid row 1: Invoice Number | Job: DO invoice no. (`do_invoice_no`), for the shipment payment only |
| Grid row 1: BL Number | Job: Master B/L no. (`mbl_no`) |
| Grid row 1: Amount | The same amount as above |

Before submitting, the SOP checks that it's CMA CGM's page (the same page serves other carriers under Change Carrier), and reads row 1 back to make sure the grid holds what it typed. Each run reads back the request number (`cma_rtgs_request_ref`). CMA CGM then emails a receipt for each payment, and they go in the DO folder: the DO Receipt and the Security Receipt.

### 3. Raise the security deposit: `odex_deposit_new`

ODeX → Counter Process → Deposit and Refund → New Deposit. Once per house B/L: ODeX takes a deposit per B/L.

| Page field | Filled with |
|---|---|
| Liner | Fixed details: Liner on ODeX (`odex_liner`) |
| B/L number | The house B/L |
| Deposit amount | Job: Security deposit (`deposit_amount`) |
| Payment mode | Always RTGS/NEFT |
| UTR / instrument no. | Job: UTR for the security deposit (`deposit_utr`) |
| Payment date | Job: Date of transfer (`payment_date`) |
| Bank name | Fixed details: Bank that pays CMA CGM (`payer_bank`) |
| Refund to | Job: Deposit refunded to (`deposit_refund_to`, default Third Party) |
| Third party name | Job: CHA (`cha`), for a refund to a third party |
| Consignee's NOC | For a refund to a third party: Hexalog's no-objection to refunding the CHA. The app writes it from the job's fields (`noc_refund` in `app/letters.py`, after `NOC (5).docx`), stamped with the Hexalog stamp |
| Security deposit receipt | The Security Receipt in the job's DO folder |

It reads back the deposit reference (`odex_deposit_ref`).

### 4. Check the deposit, download its slip: `odex_deposit_status`

ODeX → Counter Process → Deposit and Refund. Once per house B/L. This step only reads, so it's safe to run again.

It reads back the deposit's reference and status (`deposit_ref`, `deposit_status`). Once CMA CGM has approved the deposit, it also downloads the Security Deposit Acknowledgement Slip, the folder's Security-cum-ODEX Receipt. The slip goes to `data/files/<job>/portal/<HBL>-security-deposit-slip.pdf` (`downloaded_file_path`).

### 5. Raise the DO request: `odex_do_request_new`

ODeX → e-Payment → DO Request → New DO Request. Once per house B/L. This is the SOP the app's `do-request-submit` workflow runs when `DO_ODEX_SOP` points to it.

| Page field | Filled with |
|---|---|
| Liner | Fixed details: Liner on ODeX (`odex_liner`) |
| B/L number | The house B/L, not the MBL |
| DO type | Job: D/O or E-DO (`do_type`) |
| Delivery type | Job: Delivery type (`delivery_type`) |
| Container numbers | Job: Container nos. (`containers`) |
| Remarks | The job number |
| Documents (Add Row) | The built DO folder PDF, which must be up to date. It's the same file for every house B/L of the MBL |

It reads back ODeX's request number (`odex_request_ref`), which the app logs on the job.

### 6. Check the DO request, download the eDO: `odex_do_request_status`

ODeX → e-Payment → DO Request. Once per house B/L. This step only reads, so it's safe to run again. It reads back the request's reference and status. Once CMA CGM has issued the delivery order, it also downloads the eDO to `data/files/<job>/portal/<HBL>-eDO.pdf`.

### Fields filled as the process goes

The job form has two groups for values that arrive by email:

- **Liner invoice**: DO invoice no. and amount, from CMA CGM's email once it verifies the invoice.
- **Payment and deposit**: the two UTRs and the date of transfer, from Finance's email. The deposit amount defaults to 40,000, and the refund defaults to a third party, the CHA.

For now ops type these values in. Once the Graph subscription's emails are read, the ingest can fill them.

## How the SOPs are written

- Each SOP has a header comment listing its params and where each one comes from.
- Each ODeX SOP logs in from the credentials block, then goes through the menus as a person would.
- Drop-downs are set by an `execute_script` step with `script_args`. Values are passed as arguments and are never pasted into the script.
- The RTGS page's invoice grid is filled the way a person fills it: click the cell, type the value, press Enter, one cell at a time.
- A submit and the check of its answer are separate steps. The agent retries a failed step up to three times, and only the check is safe to repeat. The check reads the portal's answer (`#outcome` on the local sites). If the portal turned the form down, the check throws, and the run fails with the portal's own message.
- The local portals refuse a second deposit or DO request for the same B/L, and a second payment with the same UTR. This is how a real portal would behave. The app also takes its own claim before raising each DO request, so it never raises one twice.
- Downloads use a fixed selector. The check step marks the row's link with an id, and sets a param that the download step's `condition` reads.
- Every SOP ends with `- action: end`. Without it the agent repeats the last step, and `run_sop` refuses such an SOP.

## Moving to the real sites

The SOPs open `{params.odex_url}` and `{params.cma_rtgs_url}`, which come from `DO_ODEX_URL` and `DO_CMA_RTGS_URL`. The browser agent may only open the hosts listed in `DO_BROWSER_ALLOWED_HOSTS`, which defaults to this machine. A run that tries to open anything else is blocked and fails, naming the host.

Pointing an SOP at a real site means:

1. Copy the SOP to `sops/odex/` (or `sops/cma_rtgs/`).
2. Swap in the real page's selectors and paths, and use the real login (`ODEX_USERNAME` and `ODEX_PASSWORD` in `backend/.env`).
3. Set the URL, and allow the real host in `DO_BROWSER_ALLOWED_HOSTS`.

The params, the data behind them and the workflows stay the same.

What's still missing for the real SOPs:

1. **The real ODeX pages.** A walk-through or screenshots of ODeX's three forms and two lists, like the ones we have for the RTGS page. That gives the fields, their order and the selectors.
2. **ODeX login.** A test login, and whether login asks for an OTP or a captcha.
3. **The rest of the RTGS page.** The screenshots stop above the Submit button. We still need: what's below the grid (the Submit button, and any captcha), what the page shows after submitting, and whether the page needs a login. Saving the page as HTML (or a look in the browser's DevTools) would also confirm the selectors, and whether the grid really is Handsontable.
4. **Several house B/Ls under one MBL.** Is the deposit ₹40,000 for each house B/L, or one ₹40,000 split across them? On ODeX the app enters the full amount for each house B/L for now. On the RTGS page it confirms one deposit against the MBL, as in the screenshot.
5. **The JSR NOC.** Confirm that `NOC (5).docx` is the "JSR NOC" the SOP sheet asks for on the deposit. It is Hexalog's no-objection to refunding JSR, and the app now writes it.
6. **The UTR on the deposit.** The sample deposit slip shows UTR …4389, the invoice payment's UTR, not …8497 from the deposit's own receipt. Which one does ODeX expect?
7. **The mailbox.** Which of the Email(s) on the RTGS page the Graph subscription watches, so the receipts reach the app. It should be kept apart from python-brain's `datahub@hexalog.in` pipeline.
8. **Sample emails.** The liner's invoice-verified email, Finance's UTR reply, CMA CGM's receipt emails and the eDO email, to write their extractors.
9. **The DO request's documents.** Does ODeX take the combined folder PDF in one Add Row, or does it want one row per document type?
10. **The refund.** Is the refund request (Counter Process → Request Refund) in scope? It isn't built.
