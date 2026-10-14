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
make assets        # transparent stamps, bonds and letterheads from Docs/
make dev           # API on :8000, UI with hot reload on :5173, Inngest dev server on :8288
make demo          # builds the UI; serves it with the API on :8000, plus the Inngest dev server
make test          # backend tests (the workflows run in process; no dev server needed)
make test-browser  # the browser agent in a real Chromium, against a mock ODeX page
make try-agent     # the browser agent logs in and uploads a dummy file on a public practice site; SHOW=1 to watch
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
| `do-folder-verify` | `customs/do.document.received`, `customs/do.shipment.updated` (once edits settle) | Keeps the "Review and build" task in line with the documents; sends `customs/do.folder.ready` when everything is in place |
| `do-folder-build` | `customs/task.completed` for the folder review | Builds the PDF, moves the job to DO folder ready, opens the ODeX DO request task, sends `customs/do.docs.ready` |
| `do-request-submit` | `customs/task.completed` for the DO request | Raises the DO request on ODeX once per house bill, uploading the folder PDF, then moves the job to DO requested and sends `customs/do.request.submitted` |

Ops act through tasks: building the folder completes the review, and approving the DO request starts the ODeX run. Each request takes an insert-once claim per house bill, so a replayed or repeated run never raises it twice. A browser run that fails isn't retried blindly: the error goes on the task, with the house bills to check on ODeX before approving again.

Until the ODeX SOP is written, the DO request is raised by hand and approving it records that it's done. `backend/sops/odex/README.md` says what the SOP needs; once the file is there, approving runs the browser agent instead.

Uploads and edits tell the engine in the background, so a stopped engine never slows the app. Building and approving need it: if it isn't running they say so, and nothing is left half-started.

## Changing the folder

- `backend/do_folder.yaml` defines the documents, their order and sources, where stamps go on uploads, and the fields. The forms follow automatically. If the file has a mistake, the API refuses to start and says what it is.
- The five letters are drawn in `backend/app/letters.py`, following the team's Word originals. Wording changes go there.
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

Workflows and the browser agent: `INNGEST_DEV` (default on; set `0` in production with `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY`), `INNGEST_SERVE_ORIGIN`, `DO_ODEX_SOP` (default `backend/sops/odex/sop_odex_do_request_new.yaml`), `DO_BROWSER_HEADLESS=0` to watch the browser, `GOOGLE_API_KEY`, and python-brain's `SOP_TIMEOUT_SCALE`, `BROWSER_TEARDOWN_TIMEOUT_S` and `BROWSER_SOP_MAX_SECONDS`. They can all go in `backend/.env`.
