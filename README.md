# DO Workflow

HexaBuild app for the customs team's CMA CGM import delivery orders. For each MBL the team collects the DO folder, the app checks every document belongs to the shipment, stamps them, and builds the single PDF that is uploaded with the DO request on ODeX.

- `backend/` is FastAPI with SQLite. It owns the data, the folder definition, the checks, the letters and the PDF.
- `frontend/` is React with Vite. It renders the board and forms from the API and saves every edit as you type.

The process follows `DO PROCESS FLOW E2E.xlsx`: its "DO Folder Document Collection" sheet sets the documents, their order and their stamps.

## Run it

You need Python 3.10+ and Node 18+, and the `Docs/` folder the customs team shared (stamps, bonds and sample documents) at the top of the repo.

```bash
make setup   # virtualenv, Python packages, npm install
make assets  # transparent stamps, bonds and letterheads from Docs/
make dev     # API on :8000 and the UI with hot reload on http://localhost:5173
make demo    # builds the UI and serves everything from http://localhost:8000
make test    # backend tests
```

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
| POST | `/api/shipments/{id}/build` | Builds the PDF after review (`{"confirmed": true}`) |
| GET | `/api/shipments/{id}/folder.pdf` | The last built PDF |
| GET | `/api/shipments/{id}/events` | The job's history |
| POST | `/api/demo/reset` | Reseeds the demo data |

Slots are named by document key, plus the house bill for per-HBL documents: `mbl`, `hbl:MSEC260803047251`. Interactive docs: http://localhost:8000/docs

## Settings

Optional environment variables: `DO_DATA_DIR`, `DO_DATABASE_URL`, `DO_FOLDER_PATH`, `DO_ASSETS_DIR`, `DO_PRIVATE_DIR`, `DO_STORAGE_DIR`, `DO_DEMO_DOCS_DIR`, `DO_JOB_PREFIX` (default `HXL/IMP`), and `DO_SEED_DEMO_DATA=0` to start without demo data.
