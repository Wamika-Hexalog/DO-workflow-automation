# DO Workflow

HexaBuild prototype for the customs team: a board of Evoque sea shipments where the team fills in each shipment's DO set once, sees what's missing page by page, and generates the 14-page Word document from a template.

- `backend/` is FastAPI with SQLite. It owns the data, the DO set definition, page completion, stage rules and document generation.
- `frontend/` is React with Vite. It renders the board and forms from the API and saves every edit as you type.

## Run it

You need Python 3.10+ and Node 18+.

```bash
make setup   # virtualenv, Python packages, npm install
make dev     # API on :8000 and the UI with hot reload on http://localhost:5173
make demo    # builds the UI and serves everything from http://localhost:8000
make test    # backend tests
```

For the demo itself, use `make demo`: one process, fonts bundled, no internet needed.

Without make:

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:create_app --factory --reload --port 8000

# in a second terminal
cd frontend && npm install && npm run dev
```

Dummy data loads on first start. "Reset demo data" in the UI, or `POST /api/demo/reset`, puts it back. The SQLite file and generated documents live in `backend/data/`; delete that folder for a clean start.

## When the real DO set arrives

1. Edit `backend/do_set.yaml`: the pages, the fields each page asks for, and which shared fields each page uses. The forms follow automatically. If the file has a mistake, the API refuses to start and says what it is.
2. Save the Word file as `backend/templates/do_set.docx`, with placeholders named after the field keys, such as `{{ bl_no }}` or `{{ igm_date }}`.
3. Run `make check-template`. It lists placeholders that match no field (they would come out blank) and fields the template never uses.
4. Restart the API.

Besides the fields, the template gets `{{ job_no }}`, `{{ today }}` and `containers_list`, for one table row per container. Dates follow `date_format` in `do_set.yaml`.

If a placeholder typed in Word doesn't fill in, Word may have split it while formatting. Retype it in one go.

`make template` rebuilds the placeholder template from `do_set.yaml`.

## API

| Method | Path | What it does |
|---|---|---|
| GET | `/api/do-set` | Pages, fields and stages the UI renders from |
| GET | `/api/shipments` | Every shipment with its values and per-page completion |
| POST | `/api/shipments` | New shipment with the next job number |
| GET | `/api/shipments/{id}` | One shipment |
| PATCH | `/api/shipments/{id}/values` | Saves the fields sent (autosave) |
| POST | `/api/shipments/{id}/move` | Changes stage; refused until all pages are complete |
| POST | `/api/shipments/{id}/generate` | Fills the template; moves Details pending to Docs ready |
| GET | `/api/shipments/{id}/document` | Downloads the last generated DO set |
| GET, PATCH | `/api/client` | Evoque's fixed details |
| POST | `/api/demo/reset` | Reseeds the dummy data |

Interactive docs: http://localhost:8000/docs

## Settings

Optional environment variables: `DO_DATA_DIR`, `DO_DATABASE_URL`, `DO_SET_PATH`, `DO_TEMPLATE_PATH`, `DO_OUTPUT_DIR`, `DO_JOB_PREFIX` (default `HXL/IMP`), and `DO_SEED_DEMO_DATA=0` to start without dummy data.
