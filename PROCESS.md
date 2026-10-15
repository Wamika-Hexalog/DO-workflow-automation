# The DO process, step by step

These are the steps of one shipment's delivery order process, from the pre-alert to the eDO, as the app runs them. Use the numbers to name a step: "step 6" is Finance's mail with the UTRs. Steps 1 to 7 are the team's own list. Steps 8 to 12 follow `DO PROCESS FLOW E2E.xlsx`.

The steps run only when `DO_AUTOMATE_PORTALS` is on. `make demo-local` turns it on. The app reads a mail only if its subject or body has `#DOHACK`. The mail examples below use demo values.

| # | Step | Starts with | Who acts | Task under Process |
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

The app finds the job by the MBL in the subject. The empty lines in the quoted mail give nothing. The app fills:

- `payment_utr` (UTR for the shipment payment).
- `deposit_utr` (UTR for the security deposit).
- `payment_date` (Date of transfer), with the day the reply arrives, if it is empty. If the bank paid on another day, type the real date on the job.

Then the two confirmations of step 7 come up for ops.

**Workflow:** `email-inbound` sends `customs/do.payment.utr-received`. `do-payments-refresh` then puts the payments in front of ops.

## 7. Confirm both payments on CMA CGM's RTGS page

CMA CGM's RTGS Confirmation page is filed twice, once for each payment Finance made: first the delivery (the DO invoice), then the security deposit. Each filing has its own UTR and gets its own receipt by mail.

**Starts with:** ops approve each filing under Process. The two tasks come up together, in that order. Each task shows the amount, the last 4 digits of the UTR and the date. The delivery's task also shows the invoice. A task comes up only when the job has every value the page needs.

The two filings have the same Contact Details, bank account and Date of Transfer: `forwarder_name`, `rtgs_contact_name`, `rtgs_contact_number`, the emails for the receipts (`do_mailbox`), `payer_account_no` and `payment_date`. These parts are different:

| | 7a. The delivery | 7b. The security deposit |
|---|---|---|
| Task under Process | Confirm the shipment payment on CMA CGM's RTGS page | Confirm the security deposit on CMA CGM's RTGS page |
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
| "-" in place of the invoice (the security deposit) | Security Receipt (folder document 5) |

**Workflows:** `do-rtgs-payment` and `do-rtgs-deposit`, then `customs/do.rtgs.submitted`. Each confirms money, so neither retries. If one fails, check the RTGS page before you approve it again.

## 8. Raise the security deposit on ODeX

**Starts with:** ops approve "Raise the security deposit on ODeX". The task comes up when the job has what the deposit needs. Usually the last thing to arrive is the Security Receipt from step 7c.

The agent opens Counter Process → Deposit and Refund → New Deposit, once for each HBL, and enters:

- The liner, the HBL number, `deposit_amount`, `deposit_utr`, `payment_date` and the bank (`payer_bank`).
- Who gets the refund (`deposit_refund_to`). For a third party (the default), also the CHA's name and the NOC for the refund, which the app writes.
- The Security Receipt from the DO folder.

**Kept:** each deposit's ODeX number, in the task's note.

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

**Starts with:** CMA CGM's mail that the eDO is issued. We have not seen a real one yet, so until we have a sample this mail needs a code line with the MBL or one of its HBLs:

```
DOHACK:edo:ABC1234567
```

The agent opens Documents → DO Request on ODeX, finds each HBL and downloads its eDO to `backend/data/files/<job id>/portal/<HBL>-eDO.pdf`. When every HBL has its eDO, the job moves to **eDO received**.

**Workflow:** `do-edo-collect`, then `customs/do.case.completed`.

## Not built yet

- The refund: Counter Process → Deposit and Refund → Request Refund (the sheet's "Counter Process-Refund request").
- The real eDO mail (step 12). Finance's real reply (step 6) also needs a sample: today it must carry the two UTR lines.
- The SOPs for the real ODeX and the real RTGS page. The agent opens only the local portals (`DO_BROWSER_ALLOWED_HOSTS`).

## Where to look

- **The job:** Process shows the tasks above, with Approve and Try again. History shows each mail, each agent run and each change of stage.
- **The workflows:** the Inngest dashboard, http://localhost:8288.
- **The local portals:** the control panel, http://127.0.0.1:8100/control, plays CMA CGM and Finance. With autoplay on, it answers each request after a few seconds.
- **The data:** the database is `backend/data/do_folder.db`. A job's files are in `backend/data/files/<job id>/`. What the agent downloads is in its `portal/` folder.

## Run it with real mail

1. Run `make demo-local`. It starts the app on :8000, the Inngest dev server and the local portals.
2. In the app's fixed details, fill the two that have no default: Finance's email (`finance_email`) and the bank account that pays CMA CGM (`payer_account_no`).
3. Run `ngrok http 8000 --url=<the host in WEBHOOK_BASE_URL>`. Use no other host: the app's guard protects only that one.
4. When the tunnel is up, run `curl -X POST http://127.0.0.1:8000/api/webhooks/graph/subscribe`. `GET /api/webhooks/status` shows the subscription.
5. Send the pre-alert (step 1). Steps 2 to 5 run by themselves.
6. Reply to Finance's mail with the two UTR lines (step 6). Then approve each step under Process.

Run one copy of the app on the mailbox at a time. Each copy reads every mail and runs every step.
