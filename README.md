# DO Workflow

HexaBuild app for the customs team's CMA CGM import delivery orders. For each MBL the team collects the DO folder, the app checks every document belongs to the shipment, stamps them, and builds the single PDF that is uploaded with the DO request on ODeX.

- `backend/` is FastAPI with SQLite (Python 3.12, `uv`). It owns the data, the folder definition, the checks, the letters and the PDF, and runs the process as Inngest workflows.
- `frontend/` is React with Vite. It renders the board and forms from the API and saves every edit as you type.
- The workflow engine (Inngest) and the browser agent are python-brain's, from `logistics-platform`, adapted to keep everything in SQLite instead of MongoDB.

The process follows `DO PROCESS FLOW E2E.xlsx`: its "DO Folder Document Collection" sheet sets the documents, their order and their stamps.

## Run it

You need [uv](https://docs.astral.sh/uv/) (it installs Python 3.12) and Node 18+, and the `Docs/` folder the customs team shared (stamps, bonds and sample documents) at the top of the repo.

```bash
make setup         # Python 3.12 and packages with uv; npm install, including the Inngest dev server
make assets        # transparent stamps, bonds, letterheads and the ODeX guides' screenshots, from Docs/
make odex-login-screen  # the real ODeX login page's screenshot, for the local one (nothing is typed or sent)
make dev           # API on :8000, UI with hot reload on :5173, Inngest dev server on :8288
make demo          # builds the UI; serves it with the API on :8000, plus the Inngest dev server
make test          # backend tests (the workflows run in process; no dev server needed)
make test-browser  # the browser agent in a real Chromium: a mock ODeX page, and the whole process on the local portals
make try-agent     # the browser agent logs in and uploads a dummy file on a public practice site; SHOW=1 to watch
make portals       # local versions of ODeX and CMA CGM's RTGS page, on :8100 (see below)
make demo-local    # make demo plus the local portals: the whole process runs from the mail, in a visible browser
```

The Inngest dev server's dashboard, at http://localhost:8288, shows every workflow run and its steps. Secrets (the browser agent's `GOOGLE_API_KEY`, the ODeX login) go in `backend/.env`; see `backend/.env.example`.

Demo data loads on first start. Job 0431 (MBL GGZ3193024) carries the sample documents from `Docs/` and is ready to review and build. "Reset demo data" in the UI, or `POST /api/demo/reset`, puts it back. The database, uploads and built PDFs live in `backend/data/`; delete that folder for a clean start.

## The DO folder

One folder and one PDF per MBL, in this order:

| # | Document | Source | Stamp |
|---|---|---|---|
| 1 | Bond (Consignee) | Fixed, page 2 of `bonds.pdf` | none |
| 2 | Bond (Client) | Fixed, page 1 of `bonds.pdf` | none |
| 3 | HBL, once per house bill | Email | Envogue Styles |
| 4 | MBL, every page | Email | Hexalog Global |
| 5 | Security Receipt | Email | none |
| 6 | Security-cum-ODEX Receipt, once per house bill | Email | none |
| 7 | DO Receipt | Email | none |
| 8 | NOC for DO (CHA) | Written by the app | Jai Shri Ram Darbar |
| 9 | Endorsed Delivery Order | Written by the app, or a signed copy uploaded instead | Jai Shri Ram Darbar |
| 10 | NOC for DO (Hexalog) | Written by the app | Hexalog Global |
| 11 | Authorization Letter | Written by the app | Hexalog Global |
| 12 | Checklist | Written by the app | none |

Before a folder can be built, every document must be in place and clear of flags:

- **Missing**: not received yet, or a letter still needs a field filled in.
- **Duplicate**: the same file in two places, or a file already attached to another job.
- **Mismatched**: the MBL (or, for HBL documents, the house bill) number isn't in the file. When it names another job's MBL, the flag says which.
- **No readable text**: a scan the app can't check. The operator opens it and confirms it by eye.

The operator then steps through every document as it will appear, stamps included, and confirms before building. A job can't move to DO folder ready or later without a built folder, and any later change to a document, a field or the fixed details marks the folder out of date. Replaced and removed files are kept, and every change is in the job's history.

## Workflows

The process runs as small Inngest functions, each triggered by an event, in python-brain's style. Event names follow the DO-workflow-automation design (`customs/do.*`).

| Function | Triggered by | What it does |
|---|---|---|
| `do-invoice-request` | `customs/do.document.received` for the MBL | Requests the DO invoice on ODeX (`DO_AUTOMATE_PORTALS`) |
| `do-invoice-collect` | `customs/do.invoice.verified` (the liner's mail) | Collects the invoice from ODeX: its number, amount and PDF go on the job |
| `do-finance-mail` | `customs/do.invoice.collected` | Mails the invoice to Finance (Outlook, or the outbox on disk without it), asking for the two UTRs |
| `do-payments-refresh` | `customs/do.shipment.updated`, `customs/do.document.received`, the UTR and invoice events | Puts each payment step in front of ops once the job has what it needs; moves the job to Deposit approved once ODeX's slip is in for every house bill |
| `do-rtgs-payment`, `do-rtgs-deposit` | `customs/task.completed` for each payment | Confirm the shipment payment and the security deposit on CMA CGM's RTGS page; the first moves the job to Invoice paid |
| `do-deposit-request` | `customs/task.completed` for the deposit | Raises the security deposit on ODeX for each house bill |
| `do-folder-verify` | `customs/do.document.received`, `customs/do.shipment.updated` (once edits settle) | Keeps the "Review and build" task in line with the documents; sends `customs/do.folder.ready` when everything is in place |
| `do-folder-build` | `customs/task.completed` for the folder review | Builds the PDF, moves the job to DO folder ready, opens the ODeX DO request task, sends `customs/do.docs.ready` |
| `do-request-submit` | `customs/task.completed` for the DO request | Raises the DO request on ODeX once per house bill, uploading the folder PDF, then moves the job to DO requested and sends `customs/do.request.submitted` |
| `do-edo-collect` | `customs/do.edo.received` (CMA CGM's mail) | Collects the eDO from ODeX for each house bill; once all are in, moves the job to eDO received |

With `DO_AUTOMATE_PORTALS` on (`make demo-local` turns it on), the steps before and after the folder run from the mail, as `docs/design/LLD.md` §4.2 lays out. The agent runs a step by itself when its mail arrives. Ops approve the three that confirm or move money (the two RTGS confirmations and the ODeX deposit) and the DO request, under Process on the job. A step that fails stops with the error on its task. Ops can start it again from there, after checking the portal for a step that submits. Each submit takes an insert-once claim, so a replay never submits twice. With it off, nothing new runs, and ops do these steps by hand as before.

Ops act through tasks: building the folder completes the review, approving a payment or the DO request starts its run. Each request takes an insert-once claim per house bill, so a replayed or repeated run never raises it twice. A browser run that fails isn't retried blindly: the error goes on the task, with the house bills to check on ODeX before approving again.

Until the ODeX SOP is written, the DO request is raised by hand and approving it records that it's done. `backend/sops/odex/README.md` says what the SOP needs; once the file is there, approving runs the browser agent instead. Pointing `DO_ODEX_SOP` at the local ODeX's SOP (`make demo-local` does) shows the whole run on this machine.

Uploads and edits tell the engine in the background, so a stopped engine never slows the app. Building and approving need it: if it isn't running they say so, and nothing is left half-started.

## Email (Outlook)

Mail arrives through Microsoft Graph: a subscription on the mailbox calls `POST /api/webhooks/microsoft-graph`, which sends python-brain's `EmailService:InboundMessage` event to the `email-inbound` workflow. The connector (`backend/app/connectors/`) is python-brain's, with only its imports changed.

There is no general classifier. A mail is read only if it has the short marker `#DOHACK` (a whole word, in the subject or the body; set `MAIL_MARKER` to change it, or to nothing to read every mail the recognizers know). The facts are then read from the mail itself (`backend/app/email_infer.py`), for the three kinds of mail the demo uses:

| Mail | What is read | What happens |
|---|---|---|
| CMA CGM RTGS/NEFT confirmation with an invoice | UTR, invoice number, BL number | `payment_utr` and `do_invoice_no` are filled; the receipt goes to the DO Receipt slot |
| The same with no invoice (the 40,000 deposit) | UTR, BL number | `deposit_utr` is filled; the receipt goes to the Security Receipt slot |
| ODeX security slip | The house bill number | The slip goes to the Security-cum-ODEX Receipt slot of the job that has that house bill |
| ODeX "Invoices Received for <B/L>" (the liner released the invoices) | The B/L and each invoice number | `do_invoice_no` is filled, and `customs/do.invoice.verified` sends the agent to collect the invoice from ODeX |
| Finance's reply to the app's payment mail | "Invoice UTR:" and "Deposit UTR:" | `payment_utr`, `deposit_utr` and, when empty, the date of transfer are filled |
| Draft MBL and HBL mail | The MBL number | Opens the job if there is none. Each attachment goes to its slot by file name, or by what is in it. Read from the bills: HBL numbers, containers, vessel, voyage, and the consignees |

Mail with the marker that no recognizer knows is ignored, and so is a mail with no marker. To be exact about a mail, put explicit codes in the subject or the body instead (they win over the recognizers):

```
DOHACK:<stage>:<MBL>[:<arg>]
```

The flow after a mail with MBL and HBL:

```
EmailService:InboundMessage → email-inbound   (attach each file, fill HBL and container numbers)
  → customs/do.document.received, customs/do.shipment.updated
  → do-folder-verify    (checks every document belongs to the shipment; flags the rest)
  → customs/do.folder.ready → ops review and build (stamps go on and the letters are filled from the fields)
  → do-folder-build → customs/do.docs.ready → do-request-submit
```

### Try it

Forward the mails, in this order, to the watched mailbox with `#DOHACK` at the start of the subject and nothing else added: the draft MBL and HBLs mail, the two RTGS confirmations, and the ODeX security slip. Forward the original so its attachments stay. The draft mail opens the job: it creates one for the MBL, fills the HBL numbers, containers, vessel, voyage and consignees from the bills, and attaches them (an RTF MBL becomes a plain-text PDF). The other mails then add their documents to that job. The explicit `DOHACK:` codes (above) are for a mail the recognizers get wrong.

**Without Outlook**, with `make dev` running:

```bash
cd backend && uv run python scripts/simulate_mail.py --subject "#DOHACK Fw: ODeX: Security slip - SD2026000000001" \
  --text-file slip-mail.txt --attach slip.pdf
```

The script writes the mail to `backend/data/files/inbox/` and sends the `EmailService:InboundMessage` event. The app reads a mail from there when its id is a folder there, and from Graph otherwise. In a shell, write `"${MBL}:slot"`, not `$MBL:slot`: zsh reads `:s` as a modifier.

Mail without a code, or with an MBL no job has, is ignored. To receive real mail, fill in the `OUTLOOK_*`, `WEBHOOK_BASE_URL` and `GRAPH_CLIENT_STATE` variables in `backend/.env` and point a tunnel at port 8000 (`ngrok http 8000 --url=<your domain>`). On the tunnel's hostname only `POST/GET /api/webhooks/microsoft-graph` is open (Graph can't send a login; the webhook checks `clientState`). Everything else, the UI included, asks for a login from `BASIC_AUTH_USERS=user:password,...`; with none set the tunnel serves only that webhook. `/api/inngest` is never served on the tunnel. Local requests need no login. For the UI through the tunnel use `make demo`, which serves the built UI from the API on 8000. `GET /api/webhooks/status` shows the subscription.

## Local portals

The process runs on two websites, ODeX and CMA CGM's RTGS payments page, and waits days on CMA CGM in between. `backend/local_portals` has local versions of both, so it can be shown end to end in minutes and its SOPs proven without touching a real site:

- **ODeX** (`/odex`). The login is the real ODeX India login page, which works like it: the same fields and ids, its captcha drawn and checked the same way, the company to choose after the password, and the "already logged in another session" question. The invoice and DO request steps are the team's two ODeX guides: their screenshots, with live buttons and fields over only what the process uses. The invoice guide covers the services page, ePayment, Liner Requests, Request New Invoice, BL Status and View Invoice Request, with the invoice to download. The DO request guide ("DO final steps corrected") covers Documents → DO Request, its advertisement, and the DO request itself, with the folder added through Add Row; the list then offers the eDO once issued. New Deposit (with its slip once approved) is a plain page until a guide covers it.
- **CMA CGM RTGS** (`/cma-rtgs`): the RTGS Confirmation page, copied from the real one's HTML (same ids, checks and messages, and the same page after Submit), invoice grid included. Each payment is confirmed on its own: the shipment payment, then the security deposit.
- **A control panel** (`/control`) that plays CMA CGM and Finance: it releases invoices, emails receipts, approves deposits and issues eDOs, and pays the app's payment mails, by a click or by autoplay. Its mails read like the real ones (the RTGS confirmation, the security slip); the invoice-released and eDO mails, which haven't been seen, carry a code line. In `make demo-local` each mail goes to the app's mailbox on this machine and its webhook, so the app reads it like real mail (`backend/local_portals/mail.py`). Otherwise nothing is sent.

The pages look like the real ones, with no demo banner or watermark; the portals listen only on this machine, and the documents they hand out have DEMO in their numbers (INV-DEMO-…). The ODeX screenshots show a real account, so `make assets` keeps them in `backend/assets/private/`, out of git, and `make odex-login-screen` saves the login page's there too. The documents (invoice, receipts, deposit slip, eDO) follow the samples in `Docs/` and carry the B/L numbers, so they pass the DO folder's checks.

`backend/sops/local` has an SOP for each step, filled from the database by `app/portal_params.py`. Its README maps every page field to the field it comes from, and lists what's still needed to move to the real sites. Run a step for a job with `make portal-step JOB=0431 STEP=odex_request_invoice SHOW=1`.

The browser agent can only open this machine unless `DO_BROWSER_ALLOWED_HOSTS` allows more, so a demo can't reach a real site, even by mistake.

## Changing the folder

- `backend/do_folder.yaml` defines the documents, their order and sources, where stamps go on uploads, and the fields. The forms follow automatically. If the file has a mistake, the API refuses to start and says what it is.
- The five letters are drawn in `backend/app/letters.py`, following the team's Word originals. Wording changes go there. So is the NOC for refunding the deposit to the CHA, which goes with the deposit on ODeX rather than in the folder.
- A field added to `do_folder.yaml` with a default starts at that default in the fixed details and in existing jobs, without making built folders out of date.
- Stamp positions on uploads are fractions of the page: `x` and `y` of the stamp's centre from the bottom-left corner, and its `width`.

## Private assets

Stamps carry signatures, so `backend/assets/private/` (stamps and bonds) is kept out of git. `make assets` builds it from `Docs/`, removing the white paper around each stamp so the text under it stays readable. The bonds are valid until December 2027. Without these files the app still runs, but the affected documents show a setup flag and the folder can't be built.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/folder` | Documents, fields and stages the UI renders from |
| GET, PATCH | `/api/profile` | Fixed details used by every letter |
| GET, POST | `/api/shipments` | Every job with its folder state; a new job with the next job number |
| GET | `/api/shipments/{id}` | One job |
| PATCH | `/api/shipments/{id}/values` | Saves the fields sent (autosave) |
| POST | `/api/shipments/{id}/move` | Changes stage; DO folder ready and later need a current build |
| POST, DELETE | `/api/shipments/{id}/slots/{slot}/file` | Attaches or removes a file (multipart field `file`) |
| POST | `/api/shipments/{id}/slots/{slot}/check` | Confirms by eye a file the app couldn't verify |
| GET | `/api/shipments/{id}/slots/{slot}/preview` | The document as it will appear, stamped |
| GET | `/api/shipments/{id}/slots/{slot}/original` | The file exactly as uploaded |
| POST | `/api/shipments/{id}/build` | Confirms the review (`{"confirmed": true}`) and starts the build; poll the job until `building` clears |
| GET | `/api/shipments/{id}/folder.pdf` | The last built PDF |
| GET | `/api/shipments/{id}/tasks` | The job's tasks for ops |
| POST | `/api/shipments/{id}/tasks/odex_do_request/complete` | Approves the DO request on ODeX, or confirms it was raised by hand |
| GET | `/api/shipments/{id}/runs` | The browser agent's runs for the job |
| GET | `/api/shipments/{id}/events` | The job's history |
| POST | `/api/demo/reset` | Reseeds the demo data |
| GET, POST, PUT | `/api/inngest` | Where the Inngest dev server (or Inngest Cloud) calls the workflows |

Slots are named by document key, plus the house bill for per-HBL documents: `mbl`, `hbl:MSEC260803047251`. Interactive docs: http://localhost:8000/docs

## Settings

Optional environment variables: `DO_DATA_DIR`, `DO_DATABASE_URL`, `DO_FOLDER_PATH`, `DO_ASSETS_DIR`, `DO_PRIVATE_DIR`, `DO_STORAGE_DIR`, `DO_DEMO_DOCS_DIR`, `DO_JOB_PREFIX` (default `HXL/IMP`), and `DO_SEED_DEMO_DATA=0` to start without demo data.

Workflows and the browser agent: `INNGEST_DEV` (default on; set `0` in production with `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY`), `INNGEST_SERVE_ORIGIN`, `DO_ODEX_SOP` (default `backend/sops/odex/sop_odex_do_request_new.yaml`), `DO_BROWSER_HEADLESS=0` to watch the browser, `BROWSER_SLOW_MO_MS` to slow it down, `GOOGLE_API_KEY` (only for SOPs with steps that ask the model), and python-brain's `SOP_TIMEOUT_SCALE`, `BROWSER_TEARDOWN_TIMEOUT_S` and `BROWSER_SOP_MAX_SECONDS`. They can all go in `backend/.env`.

The portals: `DO_ODEX_URL` and `DO_CMA_RTGS_URL` (default: the local ones on :8100), `DO_BROWSER_ALLOWED_HOSTS` (comma-separated hosts the agent may open; default `127.0.0.1,localhost`; `*` for any), and for the local ODeX `LOCAL_ODEX_USERNAME`, `LOCAL_ODEX_PASSWORD` and `LOCAL_ODEX_ACCOUNT`.
