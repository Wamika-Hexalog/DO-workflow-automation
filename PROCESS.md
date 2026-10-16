# The DO process, step by step

These are the steps of one shipment's delivery order process, from the pre-alert to the eDO, as the app runs them. Use the numbers to name a step: "step 6" is Finance's mail with the UTRs. Steps 1 to 7 are the team's own list. Steps 8 to 12 follow `DO PROCESS FLOW E2E.xlsx`.

The steps run only when `DO_AUTOMATE_PORTALS` is on. `make demo-local` turns it on. The app reads a mail only if its subject or body has `#DOHACK`. A marked mail in a wording no rule knows is read by Gemini, and the job's History says so (see the README's Email section). The mail examples below use demo values.

| # | Step | Starts with | Who acts | Task in the Control Tower |
|---|---|---|---|---|
| 1 | The pre-alert opens the job | The pre-alert mail | The app | none (a mail) |
| 2 | Request the DO invoice on ODeX | Step 1 attaches the MBL | The agent | Request the DO invoice on ODeX |
| 3 | ODeX says the invoice is released | ODeX's "Invoices Received" mail | The app | none (a mail) |
| 4 | Download the invoice from ODeX | Step 3 | The agent | Collect the DO invoice from ODeX |
| 5 | Send the invoice to Finance | Step 4 | The app | Send the DO invoice to Finance |
| 6 | Finance pays and sends the UTRs | Finance's reply | The app | none (a mail) |
| 7 | Confirm the payments on CMA CGM's RTGS page, twice: the delivery, then the security deposit. Each receipt comes by mail. | Ops approve | The agent | Confirm the shipment payment on CMA CGM's RTGS page; Confirm the security deposit on CMA CGM's RTGS page |
| 8 | Raise the security deposit on ODeX | Ops approve | The agent | Raise the security deposit on ODeX |
| 9 | ODeX says the deposit is approved | ODeX's security slip mail | The app | none (a mail) |
| 10 | Review and build the DO folder | Every document is in place | Ops | Review and build the DO folder |
| 11 | Raise the DO request on ODeX | Ops approve | The agent | Raise the DO request on ODeX |
| 12 | Collect the eDO | CMA CGM's eDO mail | The agent | Collect the eDO from ODeX |

"The agent" is the browser agent. It fills the portal pages. Ops approve the steps that confirm or move money (7 and 8) and the DO request (11). Steps 2, 4, 5 and 12 run by themselves. When one fails, its task shows the error and a "Try again" button.

## 1. The pre-alert opens the job

**Starts with:** a pre-alert mail to the app's mailbox (`OUTLOOK_RECIPIENT_EMAIL`). For the demo, we send it from our own mailbox.

The mail must have:

- `#DOHACK` in the subject.
- The words "MBL" and "HBL", and each house bill number, in the subject or the body. The app knows the HBL formats in `HBL_PATTERN`. A house bill in a new format needs a change there.
- The MBL (PDF or RTF) and each HBL as attachments.

The app:

- Opens a job for the MBL, with the next job number, if no job has that MBL. There is one job for each MBL.
- Attaches each file to its document by its file name or its text. An RTF MBL becomes a PDF.
- Fills the HBL numbers, containers, vessel, voyage and consignees from the bills.
- Checks each document against the job.

The two bonds are fixed documents, so every job has them. The app writes the letters from the fields.

**Kept:** the fields on the job, and the files in `backend/data/files/<job id>/`.

**Workflow:** `email-inbound` sends `customs/do.document.received` and `customs/do.shipment.updated`.

## 2. Request the DO invoice on ODeX

**Starts with:** step 1, when it attaches the MBL (`customs/do.document.received` for the `mbl` slot). Nobody starts it.

The agent follows the team's guide "Delivery Order Request-First step 1", steps 1 to 5: ePayment for the MBL's place of delivery, then Requests → Liner Requests → Request New Invoice. It enters the MBL number and the delivery type, and attaches the MBL as the Seaway BL.

**Kept:** ODeX's request number, in the task's note and the job's history.

**Workflow:** `do-invoice-request`, then `customs/do.invoice.requested`. This step submits a form, so it does not retry. If it fails, check ODeX before you start it again.

## 3. ODeX says the invoice is released

**Starts with:** ODeX's notice that the liner released the invoice. For the demo, it can come from our own mailbox. Forward a real notice to the app's mailbox with `#DOHACK` at the start of the subject.

The app reads this shape, from a real ODeX mail:

```
Subject: #DOHACK ODeX: Invoices Received for ABC1234567

Invoices released for ABC1234567,
...
Invoice No.     Invoice Type   Total Invoice Amt.   Bill to Party
INV-DEMO-00001  NON FREIGHT    61250.00             HEXALOG GLOBAL SUPPLY CHAINS PVT LTD
```

**Kept:** the invoice number (`do_invoice_no`).

**Workflow:** `email-inbound` sends `customs/do.invoice.verified`.

## 4. Download the invoice from ODeX

**Starts with:** step 3.

The agent follows the same guide, steps 6 to 10: ePayment → EDO DETAILS, a search for the MBL, then the BL's invoice request. It reads the invoice and downloads it. If the liner has not released it yet, the task waits and shows ODeX's status.

**Kept:**

- The invoice number and amount (`do_invoice_no`, `do_invoice_amount`).
- The invoice PDF, at `backend/data/files/<job id>/portal/DO-invoice-<MBL>.pdf`.

**Workflow:** `do-invoice-collect`, then `customs/do.invoice.collected`. This step only reads ODeX, so it retries once.

## 5. Send the invoice to Finance

**Starts with:** step 4.

The app mails Finance from the app's mailbox, in the form Customs Ops use:

```
To: Finance's email (fixed details: finance_email)
Cc: every address in the fixed details' mail_cc
Subject: HXL/IMP/26-27/0001 // ABC1234567 // CMA // #DOHACK
Attachment: DO-invoice-ABC1234567.pdf

Dear Finance team,
PFA Shipping line invoice
Kindly arrange payment & share UTR Details
Also please arrange Security Amount of Rs. 40,000/-
Please share the UTRs on these lines:
Invoice UTR:
Deposit UTR:
Thanks & Regards,
Customs Operations
```

- The deposit amount is the job's "Security deposit" field (`deposit_amount`, 40000 by default).
- The marker at the end of the subject brings Finance's reply back to the app.
- During testing, `finance_email` is our own mailbox.
- Every mail the app sends copies the addresses in the fixed details' `mail_cc`.
- The app sends through Outlook when the `OUTLOOK_*` settings are filled. Without them, the mail goes to `backend/data/files/outbox/`.
- A job gets this mail only once.

**Workflow:** `do-finance-mail`, then `customs/do.payment.requested`.

## 6. Finance pays and sends the UTRs

**Starts with:** Finance's reply to the mail of step 5. A reply keeps the subject, so it keeps `#DOHACK`.

Finance writes each UTR on its line, after the colon:

```
Invoice UTR: UTRDEMO000001
Deposit UTR: UTRDEMO000002
```

If Finance writes in its own words instead ("paid the invoice with UTR …, the deposit with UTR …"), Gemini reads the reply.

The app finds the job by the MBL in the subject. The empty lines in the quoted mail give nothing. The app fills:

- `payment_utr` (UTR for the shipment payment).
- `deposit_utr` (UTR for the security deposit).
- `payment_date` (Date of transfer), with the day the reply arrives, if it is empty. If the bank paid on another day, type the real date on the job.

Then the two confirmations of step 7 come up for ops.

**Workflow:** `email-inbound` sends `customs/do.payment.utr-received`. `do-payments-refresh` then puts the payments in front of ops.

## 7. Confirm both payments on CMA CGM's RTGS page

CMA CGM's RTGS Confirmation page is filed twice, once for each payment Finance made: first the delivery (the DO invoice), then the security deposit. Each filing has its own UTR and gets its own receipt by mail.

**Starts with:** ops approve each filing in the Control Tower. The two tasks come up together, in that order. Each task shows the amount, the last 4 digits of the UTR and the date. The delivery's task also shows the invoice. A task comes up only when the job has every value the page needs.

The two filings have the same Contact Details, bank account and Date of Transfer: `forwarder_name`, `rtgs_contact_name`, `rtgs_contact_number`, the emails for the receipts (`do_mailbox`), `payer_account_no` and `payment_date`. These parts are different:

| | 7a. The delivery | 7b. The security deposit |
|---|---|---|
| Task in the Control Tower | Confirm the shipment payment on CMA CGM's RTGS page | Confirm the security deposit on CMA CGM's RTGS page |
| UTR Number | `payment_utr` (Finance's "Invoice UTR") | `deposit_utr` (Finance's "Deposit UTR") |
| Amount | `do_invoice_amount` | `deposit_amount` (40000) |
| Invoice Details, row 1 | Security Deposit not ticked. Invoice Number: `do_invoice_no`. BL Number: the MBL. Amount: the invoice's. | Security Deposit ticked, so there is no Invoice Number. BL Number: the MBL (the OBL). Amount: the deposit's. |
| After the submit | The job moves to **Invoice paid**. | The job stays where it is. |
| Receipt by mail (7c) | DO Receipt (folder document 7) | Security Receipt (folder document 5) |

For each filing, the agent ticks the privacy policy, submits the page and keeps the Request Reference from the "Submitted Successfully" page in the task's note.

**7c. The receipts.** CMA CGM mails each receipt to the emails on the form (`do_mailbox`). Forward each one to the app's mailbox with `#DOHACK`. In `make demo-local`, the control panel sends them to the app by itself. Both mails are "RTGS/NEFT Confirmation Request Processed". The app tells them apart by the invoice row:

| Invoice row of the mail | The receipt goes to |
|---|---|
| An invoice number (the delivery) | DO Receipt (folder document 7) |
| "-" in place of the invoice (the security deposit) | Security Receipt (folder document 5). Its number fills the CMA iReceipt no. (`security_receipt_no`), which step 8 needs |

**Workflows:** `do-rtgs-payment` and `do-rtgs-deposit`, then `customs/do.rtgs.submitted`. Each confirms money, so neither retries. If one fails, check the RTGS page before you approve it again.

## 8. Raise the security deposit on ODeX

**Starts with:** ops approve "Raise the security deposit on ODeX". The task comes up when the job has what the deposit needs. Usually the last thing to arrive is the Security Receipt's number from step 7c.

The agent follows the team's guide "Security deposit number 1", once for each HBL: Counter Process for the MBL's place of delivery, then Security Deposit → Security Deposit & Refund → NEW - DEPOSIT. It enters:

- Requestor details: the Line, the HBL as the BL No., and the MBL No. ODeX fills in the FPOD, the office, the consignee and the containers. Mode Of Payment is Non-ODeX-RTGS. The refund goes to a third party: the CHA, as ODeX lists it (`refund_party`). ODeX fills in its PAN.
- Attachment: the NOC on the letterhead of the MBL's consignee, which the app writes.
- Payment details: the bank (`payer_bank`), `deposit_amount`, the deposit's UTR as the Bank Payment Reference, `payment_date`, and the CMA iReceipt No (`security_receipt_no`).
- Refund details: the CHA's bank account (fixed details: `refund_party`, `refund_bank`, `refund_branch`, `refund_account_type`, `refund_account_no`, `refund_ifsc`).

Then it submits, searches the HBL on Search Deposit/Refund Request, and opens the request with View, as the guide's step 4 does. A job whose deposit is refunded to the consignee doesn't start: the fixed details hold only the CHA's account.

**Kept:** each deposit's Request No. (SD…) and its status, in the task's note.

**Workflow:** `do-deposit-request`, then `customs/do.deposit.submitted`. This step moves money, so it does not retry.

## 9. ODeX says the deposit is approved

**Starts with:** ODeX's mail with the security slip, one for each HBL. Forward it with `#DOHACK`.

```
Subject: ODeX: Security slip - <deposit number>

Please find the attached security slip issued by CMA CGM AGENCIES INDIA PRIVATE LIMITED
BL No. - <HBL>
Instrument No. - <UTR>
Status - Deposit Approved
```

The app attaches the slip to the Security-cum-ODEX Receipt (folder document 6) of the job with that HBL. When every HBL has its slip, the job moves to **Deposit approved**.

**Workflow:** `email-inbound` sends `customs/do.document.received`. `do-payments-refresh` then sends `customs/do.deposit.approved`.

## 10. Review and build the DO folder

**Starts with:** every document in place with no flag (`customs/do.folder.ready`).

Ops open Review and build on the job. They look at each page as it will appear, with its stamps, and build the PDF. The job moves to **DO folder ready**.

**Kept:** the folder PDF (`GET /api/shipments/<id>/folder.pdf`).

**Workflows:** `do-folder-verify` and `do-folder-build`, then `customs/do.docs.ready`.

## 11. Raise the DO request on ODeX

**Starts with:** ops approve "Raise the DO request on ODeX".

The agent follows the team's guide "DO final steps corrected": Documents → DO Request, once for each HBL. ODeX wants the HBL number here, not the MBL. The agent enters the destuff type, Seaway BL, the runner boy, the remarks, the HSS customer, and the factory address and PIN. It adds the folder PDF through Add Row, as `ALL <last 4 characters of the HBL>.pdf`. The job moves to **DO requested**.

**Workflow:** `do-request-submit`, then `customs/do.request.submitted`.

## 12. Collect the eDO

**Starts with:** CMA CGM's mail that the eDO is issued, with `#DOHACK`. We have not seen a real one yet, so no rule knows it: Gemini reads it. A code line with the MBL or one of its HBLs also works, without the model:

```
DOHACK:edo:ABC1234567
```

The agent opens Documents → DO Request on ODeX, finds each HBL and downloads its eDO to `backend/data/files/<job id>/portal/<HBL>-eDO.pdf`. When every HBL has its eDO, the job moves to **eDO received**.

**Workflow:** `do-edo-collect`, then `customs/do.case.completed`.

## Reminders and the morning digest

A step that waits too long mails its contact, as the team's table of 16 Oct 2026 sets it. The clock is the one in the Control Tower. The first reminder goes after the first time below, then one more at each repeat, until the end of the window. At the end of the window, one escalation goes to the next person, with the contact in Cc. A reminder missed while the app was off isn't sent late: only the latest one due goes out.

| Step | First reminder | Then every | Window | Mail to | Escalate to |
|---|---|---|---|---|---|
| 3 CMA CGM releases the invoice | 6 h | 6 h | 24 h | Envogue's contact | the ops lead |
| 6 Finance sends the UTRs | 1 h | 3 h | 12 h | the ops lead | the manager |
| 7c CMA CGM mails the receipts | 12 h | 3 h | 24 h | CMA CGM's contact | the ops lead |
| 8 Raise the security deposit | 1 h | 3 h | 24 h | the ops lead | the ops lead |
| 9 CMA CGM approves the deposit | 1 h | 3 h | 24 h | ODeX's contact | the ops lead |
| 10 Review and build | 1 h | 1 h | 6 h | the ops lead | the ops lead |
| 11 DO request | 2 h | 3 h | 24 h | the ops lead | the manager |
| 2 to 11, the whole run | - | - | 12 days | the ops lead | the manager |

The hours are clock hours: nights and Sundays count. Each contact is a setting in `backend/.env` (`REMINDERS_OPS_LEAD`, `REMINDERS_MANAGER`, `REMINDERS_ENVOGUE_POC`, `REMINDERS_CMA_POC`, `REMINDERS_ODEX_POC`); a contact left empty gets no mail. Reminders don't copy `mail_cc`. The Control Tower shows, under each clock, when the next reminder goes, and marks a step "Late" once its first reminder is due.

Each morning at 09:30 IST, the digest goes to `DIGEST_TO`: every open job, the steps that wait and for how long, and which are late.

The check runs every 10 minutes. To run it now: send `customs/do.reminders.check` from the Inngest dashboard (http://localhost:8288). `customs/do.digest.send` sends the digest now.

## Not built yet

- The refund: Counter Process → Deposit and Refund → Request Refund (the sheet's "Counter Process-Refund request").
- Rules for the real eDO mail (step 12) and Finance's real reply (step 6). Until we have samples, Gemini reads them.
- The SOPs for the real ODeX and the real RTGS page. The agent opens only the local portals (`DO_BROWSER_ALLOWED_HOSTS`).

## Where to look

- **The job:** the Control Tower shows every step above, 1 to 12, in order. Each step says where it stands: done, done outside the app (the job was already past it before the app ran it), needs you, waiting on Finance or CMA CGM with how long it has waited, or not started. Approve and Try again are on the step. History shows each mail, each agent run and each change of stage.
- **Questions:** Ask about this job, on the job, and the Help page. The assistant reads the app's records (the job's steps and history, the mail log, the mailbox, the agent's runs) and says why something did not happen and how to fix it. For example: "We sent the pre-alert but no job opened. Why?"
- **Money:** the Finance page adds up every job's DO invoice (read from the PDF the agent collects in step 4), what Finance has paid, and the deposits.
- **The workflows:** the Inngest dashboard, http://localhost:8288.
- **The local portals:** the control panel, http://127.0.0.1:8100/control, plays CMA CGM and Finance. With autoplay on, it answers each request after a few seconds.
- **The data:** the database is `backend/data/do_folder.db`. A job's files are in `backend/data/files/<job id>/`. What the agent downloads is in its `portal/` folder.

## Run it with real mail

1. Run `make demo-local`. It starts the app on :8000, the Inngest dev server and the local portals.
2. In the app's fixed details, fill the ones that have no default: Finance's email (`finance_email`), the bank account that pays CMA CGM (`payer_account_no`), and the account the deposit is refunded into (`refund_bank`, `refund_branch`, `refund_account_no`, `refund_ifsc`).
3. Run `ngrok http 8000 --url=<the host in WEBHOOK_BASE_URL>`. Use no other host: the app's guard protects only that one.
4. When the tunnel is up, run `curl -X POST http://127.0.0.1:8000/api/webhooks/graph/subscribe`. `GET /api/webhooks/status` shows the subscription.
5. Send the pre-alert (step 1). Steps 2 to 5 run by themselves.
6. Reply to Finance's mail with the two UTR lines (step 6). Then approve each step in the Control Tower.

Run one copy of the app on the mailbox at a time. Each copy reads every mail and runs every step.
