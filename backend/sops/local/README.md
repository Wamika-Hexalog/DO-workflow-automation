# Local SOPs

The CMA CGM DO process uses two websites: ODeX, and CMA CGM's RTGS payments page. The SOPs here drive local versions of both (`backend/local_portals`). That lets the whole process be shown, and every SOP be proven, without touching a real site. The browser agent is python-brain's, and the SOPs use python-brain's format. All their steps are scripted Playwright steps, so no model is called.

The local RTGS page copies CMA CGM's real one, from its HTML: the same ids, fields, checks and messages, and the same page after Submit. So the RTGS SOP uses the real page's selectors.

The local ODeX's login is the real ODeX India login page: its screenshot (`make odex-login-screen` takes it, without typing anything), with live fields under the real ids, and the real page's script. It draws and checks the captcha the same way, then asks for the company, and asks before closing another session of the same login. So the SOPs' login steps are the real ones.

The local ODeX shows the team's three step-by-step guides: "Delivery Order Request-First step 1" for the invoice, "Security deposit number 1" for the security deposit, and "DO final steps corrected" for the DO request. Each page is a guide's screenshot, with live buttons and fields over only what the process clicks or types. The screenshots show a real ODeX account, so they stay out of git: `make assets` takes them from the guides in `Docs/Docs/` into `assets/private/odex/`. Without them, each page shows a grey stand-in with the same live parts, so the SOPs and the tests still run. See [Moving to the real sites](#moving-to-the-real-sites).

## Run them

```bash
make portals                                              # http://127.0.0.1:8100
make portal-step JOB=0431 STEP=odex_request_invoice SHOW=1
```

`portal-step` runs one step for a job in the app's database. `app/portal_params.py` builds the values from the job, and the run log goes on the job. `SHOW=1` opens the browser in slow motion. For a per-house-bill step, `HBL=...` runs only that house bill.

Between steps, play CMA CGM on the control panel at http://127.0.0.1:8100/control: release the invoice, email the receipts, approve the deposit, issue the eDO. You can also turn on autoplay so each answer comes by itself a few seconds later. The panel also shows the emails CMA CGM would send, with their attachments. Nothing is actually sent.

`make demo-local` runs the app, the Inngest dev server and the portals together, with `DO_AUTOMATE_PORTALS` on. The whole process then runs from the mail, each step in a visible browser:

1. Attach the MBL to a job (or send the draft MBL and HBL mail). The agent requests the DO invoice on ODeX.
2. Release the invoice on the control panel. Its mail brings the agent back to collect the invoice. The app then mails it to Finance; locally, that mail goes to the app's outbox folder.
3. Under Finance on the control panel, pay and reply. The app reads the two UTRs. The two RTGS confirmations then wait for ops, under Process on the job.
4. Approve them. Email the receipts on the control panel, and the raise of the deposit waits for ops. Approve it.
5. Approve the deposit on the control panel. ODeX's slip comes by mail, and the job moves to Deposit approved.
6. Review and build the folder, and approve the DO request. Issue the eDO on the control panel: its mail brings the agent back to collect it, and the job moves to eDO received.

Autoplay answers for CMA CGM and Finance by itself, so only the ops approvals need a click. `make portal-step` still runs any one step by hand.

ODeX login: `hexalog-demo` / `local-demo-only`, from `LOCAL_ODEX_USERNAME` and `LOCAL_ODEX_PASSWORD` (`app/config.py`). It's not a secret: the portal only runs on this machine. Type the captcha as shown (it's case-sensitive), then choose the company, `LOCAL_ODEX_ACCOUNT`, and Confirm Sign In. Like the real ODeX, a browser that has signed in with the User ID isn't asked for the captcha again for 30 days.

## The steps and their data

Each step lists the page fields, what the SOP types into each one, and where in the database the value comes from. "Job" fields are on the job's form in the app. "Fixed details" are shared by every job. If anything is missing, the step won't start, and the error names every missing item.

### 1. Request the DO invoice: `odex_request_invoice`

The team's ODeX guide, steps 1–5. Once per MBL, after CMA CGM files the IGM:

1. On the services page, choose ePayment's location: the MBL's final place of delivery (NCR for ICD Tughlakabad).
2. Open ePayment, then Requests → Liner Requests → Request New Invoice.

| Page field | Filled with |
|---|---|
| ePayment's location (services page) | Job: ODeX location (`odex_location`, default NCR) |
| Liner Name | Fixed details: Liner on ODeX (`odex_liner`, CMA CGM): the option that starts with it |
| BL No. | Job: Master B/L no. (`mbl_no`): CMA CGM invoices the master bill |
| BL Location | Job: ODeX location (`odex_location`) |
| Type | Always Import |
| Type Of Delivery | Job: Delivery type (`delivery_type`), in ODeX's words: Factory Destuff |
| Job No. | The job number |
| Invoice In Name of | Not typed: ODeX keeps it, the same every time (guide, step 5) |
| Attachment | Seaway BL: the MBL in the DO folder, uploaded as `MBL-<MBL no.>.pdf` |
| Remarks (on the attachment) | Always "PLEASE RELEASE TAX INVOICE" |

It reads back ODeX's request number (`odex_invoice_request_ref`). The liner then releases the invoice on ODeX.

### 1b. Collect the DO invoice: `odex_invoice_fetch`

The team's ODeX guide, steps 6–10. Once per MBL, after the liner releases the invoice. This step only reads, so it's safe to run again:

1. Open ePayment for the job's location, enter the MBL in the search, and click EDO DETAILS.
2. On the BL Status page, open the request (Requests card).
3. On View Invoice Request, read the invoice and download its attachment.

It reads back the request number and the invoice's number, date, amount and status. Once the invoice is out, it saves the invoice PDF to `data/files/<job>/portal/DO-invoice-<MBL>.pdf` (`downloaded_file_path`). That's the file that goes to Finance with the security deposit. The invoice's number and amount go on the job as DO invoice no. and DO invoice amount, and a line in the job's history says so. Before the release it reports the request's status and downloads nothing.

### 2. Confirm the payments: `cma_rtgs_shipment_payment` and `cma_rtgs_security_deposit`

CMA CGM's "RTGS Confirmation (CMA CGM) - New Request" page (`rtgs.india.cma-cgm.com/index.php/Finance/RTGS/NewRequest?shipcomp_code=0001`), after Finance pays and shares the UTRs. It has no login and no captcha. It's filed twice per MBL, once for each payment, with the same SOP (`cma_rtgs/new_request.yaml`):

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

Before submitting, the SOP:

- checks that it's CMA CGM's page (`shipcomp_code` 0001), since the same page serves ANL under Change Carrier;
- reads row 1 back to make sure the grid holds what it typed;
- ticks "I have read and accept CMA CGM privacy policy", which the page requires.

The page checks the form in the browser before sending anything. A failing field gets the page's own message, such as "Please enter valid UTR Number", and the run fails with those messages. A good request gets "RTGS Confirmation Request (CMA CGM) Submitted Successfully" and a request reference, which the SOP reads back (`cma_rtgs_request_ref`). CMA CGM then emails a receipt for each payment, and they go in the DO folder: the DO Receipt and the Security Receipt.

### 3. Raise the security deposit: `odex_deposit_new`

The team's guide "Security deposit number 1", once CMA CGM's Security Receipt is in:

1. On the services page, choose Counter Process's location (NCR for ICD Tughlakabad) and click its arrow.
2. Security Deposit → Security Deposit & Refund, then NEW - DEPOSIT.
3. Fill in the requestor, payment and refund details, and attach the NOC.
4. SUBMIT. Then search the BL No. and open the request with View, for its Request No.

It runs once per house B/L: ODeX takes a deposit per B/L.

| Page field | Filled with |
|---|---|
| Counter Process's location (services page) | Job: ODeX location (`odex_location`, default NCR) |
| Line | Fixed details: Liner on ODeX (`odex_liner`, CMA CGM): the option that starts with it |
| BL No. | The house B/L |
| MBL No. | Job: Master B/L no. (`mbl_no`) |
| FPOD, Office Location, Consignee Name, Container Details | Filled in by ODeX from the BL. The local ODeX has no IGM: it fills in its office's place of delivery, and "As per IGM" for the consignee and the containers |
| Mode Of Payment | Non-ODeX-RTGS, every time: Finance pays CMA CGM by RTGS |
| Refund To-Name | ThirdParty, and the CHA as ODeX lists it: fixed details, Deposit refunded to (`refund_party`). ODeX fills in the Refund To PAN. A job refunded to the consignee doesn't start: the fixed details hold only the CHA's account |
| Attachment Details | "NOC (on the letter head of MBL consignee)": Hexalog's no-objection to refunding the CHA. The app writes it from the job's fields (`noc_refund` in `app/letters.py`, after `NOC (5).docx`), stamped with the Hexalog stamp |
| Bank | Fixed details: Bank that pays CMA CGM (`payer_bank`) |
| Amount | Job: Security deposit (`deposit_amount`), as 40000.00 |
| Bank Payment Reference | Job: UTR for the security deposit (`deposit_utr`) |
| Payment date | Job: Date of transfer (`payment_date`), as DD-MM-YYYY |
| CMA iReceipt No | Job: CMA iReceipt no. (`security_receipt_no`). The mail ingest reads it off CMA CGM's Security Receipt |
| Beneficiary Name (as per bank records) | Fixed details: Deposit refunded to (`refund_party`) |
| Bank Name, Branch Name, A/C Type, A/C No., IFSC Code | Fixed details: the refund account (`refund_bank`, `refund_branch`, `refund_account_type`, `refund_account_no`, `refund_ifsc`). Only the type has a default, Current |

It reads back the Request No. and the status from the request's View (`odex_deposit_ref`, `odex_deposit_status`). The guide's screenshots end above the form's buttons, so the local page puts SUBMIT and BACK under the picture.

### 4. Check the deposit, download its slip: `odex_deposit_status`

Counter Process → Security Deposit & Refund, the request searched by its BL No. Once per house B/L. This step only reads, so it's safe to run again.

It reads back the deposit's reference and status (`deposit_ref`, `deposit_status`). Once CMA CGM has approved the deposit, it also downloads the Security Deposit Acknowledgement Slip, the folder's Security-cum-ODEX Receipt. The slip goes to `data/files/<job>/portal/<HBL>-security-deposit-slip.pdf` (`downloaded_file_path`).

### 5. Raise the DO request: `odex_do_request_new`

The team's guide "DO final steps corrected", once the DO folder is ready:

1. On the services page, choose ePayment's location (NCR for ICD Tughlakabad) and click its arrow.
2. Documents → DO Request.
3. Close the advertisement, then NEW DO REQUEST.
4. Fill in the BL information.
5. Add Row, attach the DO folder, and SAVE.

It runs once per house B/L. This is the SOP the app's `do-request-submit` workflow runs when `DO_ODEX_SOP` points to it.

| Page field | Filled with |
|---|---|
| BL No. | The house B/L. ODeX's note on the page asks for one request per HBL once the IGM is filed for it. |
| Liner/FF-Issuer | Filled in by ODeX from the BL No.; the SOP checks it's the liner on ODeX (`odex_liner`) |
| Location | Job: ODeX location (`odex_location`) |
| Cargo Type | General, every time |
| DE Stuffing Type | Job: Delivery type, in ODeX's words: Factory De-stuff is Factory, CFS De-stuff and LCL are CFS |
| Seaway BL/Telex BL | Job: Endorsed. Yes for a waybill or a telex release, No for an OBL |
| Payment not via ODeX | Ticked: Finance pays CMA CGM by RTGS |
| Runner Boy, Mobile No. | Fixed details (`odex_runner_boy`, `odex_runner_mobile`). Empty in the guide, so they're skipped while empty |
| Remarks | Fixed details: "Forwarder Letter attached to authorise CHA" (`odex_do_remarks`) |
| HSS Customer Name | Fixed details: HEXALOG GLOBAL SUPPLY CHAINS PVT LTD (`odex_hss_customer`) |
| Factory Address, Factory Location PIN | Fixed details: the guide's address and 122015 (`factory_address`, `factory_pin`) |
| Document List (Add Row) | "All document in a single pdf": the built DO folder, which must be up to date. It's the same file for every house B/L of the MBL, named as the team names it: `ALL 157B.pdf` for HBL …157B |

It reads back ODeX's request number (`odex_request_ref`), which the app logs on the job.

### 6. Check the DO request, download the eDO: `odex_do_request_status`

ePayment → Documents → DO Request, as in step 5, then search the house B/L. Once per house B/L. This step only reads, so it's safe to run again. It reads back the request's reference and status. Once CMA CGM has issued the delivery order, it also downloads the eDO to `data/files/<job>/portal/<HBL>-eDO.pdf`. The guide stops at SAVE, so where the real ODeX offers the eDO isn't known; the local one puts it on the request's row.

### Fields filled as the process goes

The job form has two groups for values that arrive by email:

- **Liner invoice**: DO invoice no. and amount, read off ODeX by step 1b.
- **Payment and deposit**: the two UTRs and the date of transfer, from Finance's email. The deposit amount defaults to 40,000, and the refund defaults to a third party, the CHA.

For now ops type these values in. Once the Graph subscription's emails are read, the ingest can fill them.

## How the SOPs are written

- Each SOP has a header comment listing its params and where each one comes from.
- Each ODeX SOP logs in from the credentials block, then goes through the menus as a person would. The login is the real one's: User ID, password, the captcha (the local ODeX puts its answer on the page, in `data-answer`; the real one needs a person), Sign in, then the company and Confirm Sign In. If the login is signed in somewhere else, ODeX asks in a dialog whether to close that session. The agent accepts every dialog, so it says OK.
- Drop-downs are set by an `execute_script` step with `script_args`. Values are passed as arguments and are never pasted into the script.
- The RTGS page's invoice grid is Handsontable (0.28 on the real page). It's filled the way a person fills it: click the cell, type the value, press Enter, one cell at a time.
- A submit and the check of its answer are separate steps. The agent retries a failed step up to three times, and only the check is safe to repeat. The check reads the portal's answer (`#outcome` on the local sites). If the portal turned the form down, the check throws, and the run fails with the portal's own message.
- The local portals refuse a second deposit or DO request for the same B/L, and a second payment with the same UTR. This is how a real portal would behave. The app also takes its own claim before raising each DO request, so it never raises one twice.
- Downloads use a fixed selector. The check step marks the row's link with an id, and sets a param that the download step's `condition` reads.
- Every SOP ends with `- action: end`. Without it the agent repeats the last step, and `run_sop` refuses such an SOP.

## Moving to the real sites

The SOPs open `{params.odex_url}` and `{params.cma_rtgs_url}`, which come from `DO_ODEX_URL` and `DO_CMA_RTGS_URL`. The browser agent may only open the hosts listed in `DO_BROWSER_ALLOWED_HOSTS`, which defaults to this machine. A run that tries to open anything else is blocked and fails, naming the host.

The RTGS SOP already uses the real page's selectors. Pointing it at the real site takes only `DO_CMA_RTGS_URL=https://rtgs.india.cma-cgm.com` and `DO_BROWSER_ALLOWED_HOSTS=rtgs.india.cma-cgm.com`. It has never been run there: there, it confirms a real payment. Do it first with a real payment, with someone watching (`SHOW=1`).

Pointing an ODeX SOP at the real site means:

1. Copy the SOP to `sops/odex/`.
2. Swap in the real page's selectors and paths, and use the real login (`ODEX_USERNAME` and `ODEX_PASSWORD` in `backend/.env`).
3. Set the URL, and allow the real host in `DO_BROWSER_ALLOWED_HOSTS`.

The params, the data behind them and the workflows stay the same.

What's still missing for the real SOPs:

1. **The real ODeX pages' HTML.** The guides' screenshots show what the invoice, deposit and DO request steps look like, but not ODeX's element ids. The SOPs for the real ODeX need each page's HTML (saved as "Webpage, Complete", or from the browser's DevTools), as the RTGS SOP had.
2. **ODeX login.** The real login page is known: User ID, password and a picture captcha (no OTP), then the company, then Confirm Sign In. The local ODeX copies it. Still needed:
   - A test login.
   - Someone to type the captcha: a step where the run waits for a person. ODeX doesn't ask a browser that has signed in with that User ID in the last 30 days, but the agent starts a fresh browser each run, so it is always asked.
   - Whether Hexalog's login has one company or several (the SOPs pick the only one, and stop if there are more), and whether it also asks for a location.
   - The company step's look: it only shows after a real sign-in, so the local one is built from ODeX's script and stylesheet. A screenshot of it would settle it. So would the message for a wrong password, which hasn't been seen.
   - One session per login: when the agent signs in, ODeX signs out anyone else using the same login. Ops may want the agent to have its own login.
3. **A first real RTGS confirmation.** The RTGS SOP has the real page's selectors but has only run on the local page. Its first real run should be with a real payment, watched. Also: the real page's answer when it turns a request down on the server (a UTR entered twice, say) hasn't been seen.
4. **Several house B/Ls under one MBL.** Is the deposit ₹40,000 for each house B/L, or one ₹40,000 split across them? On ODeX the app enters the full amount for each house B/L for now. On the RTGS page it confirms one deposit against the MBL, as in the screenshot.
5. **The JSR NOC.** Confirm that `NOC (5).docx` is the "JSR NOC" the SOP sheet asks for on the deposit. It is Hexalog's no-objection to refunding JSR, and the app now writes it.
6. **The UTR and the receipt on the deposit.** The app enters the deposit's own UTR as the Bank Payment Reference, and the Security Receipt's number as the CMA iReceipt No. But the sample deposit slip and the guide's screenshot both show the invoice payment's UTR. Which UTR, and which receipt's number, does ODeX expect?
7. **The mailbox.** Which of the Email(s) on the RTGS page the Graph subscription watches, so the receipts reach the app. It should be kept apart from python-brain's `datahub@hexalog.in` pipeline.
8. **Sample emails.** The liner's invoice-verified email, Finance's UTR reply, CMA CGM's receipt emails and the eDO email, to write their extractors.
9. **The DO request.** The guide "DO final steps corrected" settles most of it: the combined folder goes in one Add Row, as "All document in a single pdf". Still open:
   - Its text says to enter SHZ8483546, the MBL, as the BL No., but its screenshots show WOLSZSE26085157B, the HBL. The SOP enters the HBL, as the screenshots and ODeX's note do, once per house bill.
   - It says to enter the runner boy and his mobile no. "as shown in the screenshot", but the screenshot leaves them empty. They're fixed details, empty for now.
   - Are the factory address and PIN always the guide's (Hexalog's), or the consignee's factory for each job?
   - The drop-downs' other options (only General, Factory and Yes are seen), what ODeX shows after SAVE, and where it offers the eDO.
10. **The refund.** Is the refund request (Counter Process → Request Refund) in scope? It isn't built.
11. **The rest of the deposit form.** The guide leaves the form's Status empty, ends above its buttons, and shows only its own choice in Mode Of Payment, Document Name and Refund To-Name. The local page guesses the other options. The guide is "number 1", for the first house B/L: does the second one differ?
