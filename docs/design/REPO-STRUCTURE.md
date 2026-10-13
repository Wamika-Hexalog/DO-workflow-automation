# Repository structure — decision

| | |
|---|---|
| Status | Decided (v0.2 — monorepo with `backend/` and `frontend/`) |
| Decision | **One repo (monorepo) with `backend/` and `frontend/`.** The backend is the engine copied from `python-brain` with its layout preserved; `backend/src/` is an import root, not a package. |
| Related | [HLD.md](HLD.md) · [LLD.md](LLD.md) · [ENGINE-REUSE.md](ENGINE-REUSE.md) |

## 1. Decisions and why

| # | Decision | Why | Cost accepted |
|---|---|---|---|
| 0 | **Monorepo: `backend/` + `frontend/`** | The dashboard and the API ship together: one PR can change an endpoint and its screen, one CI run covers both, and the API contract can't drift unnoticed | Two toolchains in one repo; CI must run per-folder (path filters) |
| 1 | **Separate repo from `python-brain` (option B)**, not a module inside `python-brain` | Independent deploys and blast radius: a DO bug or portal change can't take down customs filing | Two copies of the engine; fixes must be ported by hand |
| 2 | **Keep `python-brain`'s layout inside `backend/`**: `backend/src/{main.py, agents, api, shared, workflows}` with `backend/src/` on the import path (`from shared.x import …`) | Copied files keep working with almost no edits, and a fix in one repo applies to the other as a near-identical diff | Departs from the `src/<package>` layout the current scaffold uses |
| 3 | **Application, not a library**: `[tool.uv] package = false`, no `[build-system]`, no wheel | Nothing imports this as a dependency; `PYTHONPATH=/app/src` in Docker as the engine does today | None |
| 4 | **Rename `workflows/customs/` → `workflows/common/` (shared helpers) + `workflows/do/` (DO stages)** | `customs` is the wrong domain name here; the rename is a mechanical import rewrite done once at copy time | One-time rewrite; later backports need the same rename |
| 5 | **DO config in `config/*.yaml`** (documents, gates, SLAs), not in code | SOP requirement 6: document list, sources, order and rules must be configurable | A small loader to maintain |
| 6 | **Docs in `docs/` with `design/`, `runbooks/`, `adr/`** | Docs go in their own PRs (a `python-brain` convention worth keeping) | None |
| 7 | **Event names stay `customs/do.<entity>.<action>`** | The shared failure-notify and logging code keys off the `customs/` convention | None |

## 2. Layout

```
DO-workflow-automation/
├── README.md                         # monorepo overview
├── .pre-commit-config.yaml           # repo-wide hooks (backend + frontend)
├── .github/workflows/                # ci.yml (backend job now; frontend job when scaffolded), path-filtered
├── .gitignore
│
├── docs/
│   ├── README.md                     # index
│   ├── design/                       # HLD · LLD · ENGINE-REUSE · REPO-STRUCTURE
│   ├── runbooks/                     # recovery drills per stage (claim delete + re-send event), portal outages
│   └── adr/                          # one short file per later decision (0001-…)
│
├── backend/                          # everything Python — run uv from here
│   ├── README.md
│   ├── CLAUDE.md                     # adapted from python-brain's — commands, architecture in 90 s, gotchas
│   ├── pyproject.toml  uv.lock  .python-version
│   ├── Dockerfile  docker-compose.yml  .dockerignore  .env.example
│   ├── config/                       # business rules, no secrets
│   │   ├── documents.yaml            # the 12 documents: source, order, mandatory, signatory, match rules
│   │   ├── gates.yaml                # which tasks need ops confirmation
│   │   └── sla.yaml                  # timers, chase intervals, park thresholds
│   ├── src/                          # import root
│   │   ├── main.py                   # FastAPI + lifespan + Graph subscription + Inngest registration
│   │   ├── agents/browser/           # COPIED unchanged: core/ nodes/ integrations/ utils/ repository.py
│   │   ├── api/
│   │   │   ├── deps.py
│   │   │   └── routers/              # auth, tasks, cases, documents, webhooks, admin, metrics
│   │   ├── shared/
│   │   │   ├── config.py  events.py  inngest_client.py  log_context.py  logging_config.py  log_redaction.py  http.py
│   │   │   ├── connectors/           # outlook*, graph_subscription, shipthis*, erp_tasks, document_upload, slack
│   │   │   ├── db/
│   │   │   │   ├── connection.py
│   │   │   │   └── models/           # copied: hitl_request, idempotency_key, automation_run, function_run,
│   │   │   │                         #   outbound_email, erp_outbox, user, notification, email, document, task
│   │   │   │                         # new: do_case, do_document, do_folder, do_event   (or `do` on shipment)
│   │   │   ├── services/
│   │   │   │   ├── tasks.py  alerts.py  alert_dedup.py  function_runs.py  auth.py     # copied
│   │   │   │   ├── llm/              # classifier machinery copied; DO rules new
│   │   │   │   └── do/               # NEW: case_service (state transitions), doc_verifier, doc_assembler,
│   │   │   │                         #      doc_generators/, extractors (UTR/MBL/HBL), email_matcher, sla
│   │   │   ├── processors/           # document_reader, extend_parser (copied)
│   │   │   ├── utils/                # pdf_stitcher, pdf_splitter (copied)
│   │   │   └── sops/
│   │   │       ├── odex/             # request_invoice, deposit_new, status_deposit, do_request_new, refund_request
│   │   │       └── cma_rtgs/         # new_request
│   │   └── workflows/
│   │       ├── common/               # task_gate, run_start, notify, llm_credits, run_sop (lifted from duty_structure)
│   │       └── do/
│   │           ├── email/            # ingest (classify→route), do_invoice, do_utr, do_receipts,
│   │           │                     #   do_documents, do_edo, do_chase, hitl_reply, mailbox_sweep
│   │           ├── browser/          # do_request_invoice, do_rtgs_submit, do_deposit, do_deposit_status,
│   │           │                     #   do_request, do_refund
│   │           ├── erp_poll.py       # */15 cron: new CMA CGM shipments with IGM filed → case
│   │           └── sla_sweeper.py    # */15 cron: SLA breaches → alerts + tasks
│   ├── scripts/                      # seed_user.py, seed_test_data.py, check_no_secret_logging.py
│   └── tests/
│       ├── conftest.py
│       ├── unit/                     # state machine, extractors, verifier, assembler, classifier rules
│       ├── workflows/                # Inngest functions with stubbed browser/connectors
│       ├── api/
│       └── fixtures/                 # redacted sample e-mails, PDFs, portal HTML snapshots
│
├── frontend/                         # ops dashboard (see §3)
│   ├── README.md
│   ├── package.json  tsconfig.json  vite.config.ts  .env.example
│   ├── src/
│   │   ├── api/                      # typed client generated from backend OpenAPI (do not hand-edit)
│   │   ├── features/                 # cases/  documents/  tasks/  sla/  auth/
│   │   ├── components/               # shared UI
│   │   ├── routes/  hooks/  lib/
│   │   └── main.tsx
│   └── tests/                        # unit (vitest) + e2e (playwright) against mocked API
│
└── ops/                              # deployment & observability
    ├── observability/                # copied: loki, alloy, grafana, compose files
    └── docker-compose.yml            # backend + mongo + frontend (+ inngest dev) for local full-stack
```

## 3. Frontend

**Purpose:** the ops dashboard the Task Manager API already anticipates (`python-brain` serves `/tasks`, `/shipments`, `/metrics` for one; this repo needs its own for DO).

| Screen | Backed by | Why it exists |
|---|---|---|
| **Cases board** (Kanban by state) | `GET /api/v1/do/cases` | See every MBL and where it is stuck; SLA badges |
| **Case detail** — timeline, HBLs, invoice/UTR/deposit/refund facts, run links | `GET /api/v1/do/cases/{id}` | One place to answer "where is this DO?"; links to Inngest runs and portal screenshots |
| **Document review** (G3) | `GET/POST …/documents`, `…/flag` | Missing / Duplicate / Mismatched flags, replace or override-with-note, preview originals, confirm folder |
| **Approvals** (G2, G4, refund) | `GET /tasks`, `POST /tasks/{id}/action` | Ops approve gated submissions |
| **HITL inbox** | `/hitl` | Unmatched e-mails, low-confidence extractions |
| **SLA / metrics** | `/metrics/dashboard` | Pending invoice/deposit/eDO/refund timers |

**Stack (assumption — confirm):** React + TypeScript + Vite, TanStack Query for server state, a typed client **generated from the backend's OpenAPI schema** (`openapi-typescript` or similar), vitest for unit tests, Playwright for e2e. Auth is the backend's JWT (`/auth/login`, `/auth/me`). Chosen as the least surprising default; if Hexalog's existing ops dashboard uses another stack, match it instead.

**API contract rules**
1. The backend OpenAPI schema is the contract. `frontend/src/api/` is generated from it by a script; CI fails if the generated client is stale.
2. Breaking API changes and the matching frontend change land in **one PR**.
3. The frontend never talks to Inngest, MongoDB, ShipThis or portals — only to the backend API.
4. Documents are served through short-lived signed URLs from the backend, never public bucket paths.

**Deployment:** static build served behind the same reverse proxy as the API (`ops/`), same origin where possible so CORS stays off; local full-stack via `ops/docker-compose.yml`.

## 4. `backend/pyproject.toml` changes from the current scaffold

The current scaffold is a `src/do_workflow_automation` **package** built with `uv_build`. Switch to the application layout when the engine lands:

```toml
[tool.uv]
package = false              # and delete [build-system] and the [project.scripts] entry

[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
mypy_path = "src"
strict = true                # engine files may need per-module overrides at first; track them, don't blanket-ignore

[tool.ruff]
line-length = 100
src = ["src", "tests"]
```

Then remove `backend/src/do_workflow_automation/` and `backend/tests/test_main.py` (placeholders). Dependencies are those of `python-brain`'s `pyproject.toml` minus anything only customs-air needs; keep Playwright, Stagehand, LangGraph/LangChain, Inngest, Beanie/Motor, msal, httpx, structlog, pypdf, weasyprint, pydantic-settings.

## 5. Conventions carried over

- **Branches:** conventional prefixes (`feat/`, `fix/`, `chore/`, `refactor/`, `docs/`); no playful names.
- **Docs in their own PRs**, not bundled with code.
- **Events:** only via `EventNames`; never raw strings. **Step names:** kebab-case, stable across replays.
- **SOP YAML:** every step sets `method: playwright` (or `hitl`) explicitly — omitting it silently invokes the LLM.
- **Tests:** pytest `asyncio_mode=auto`, real local MongoDB, skip when unreachable.
- **Imports (backend):** `from shared.x import …` (never `from src.shared…`).
- **CI is per-folder:** backend job runs on changes under `backend/`, frontend job under `frontend/`; changes to `docs/` run neither. A change to the API schema runs both.
- **Frontend:** strict TypeScript, ESLint + Prettier, no hand-edits under `src/api/` (generated).

## 6. Keeping `backend/` in sync with `python-brain`

Because we copy rather than depend:

1. Every copied engine file carries the source commit in a header comment on first copy: `# copied from python-brain @ <sha>`; the sha is also recorded in `docs/design/ENGINE-REUSE.md`.
2. Engine fixes (browser agent, Outlook client, task gate, claim helpers) are ported by cherry-pick, in a PR labelled `sync:engine`.
3. Anything DO-specific stays under `workflows/do/`, `services/do/`, `sops/odex|cma_rtgs/`, and `config/` so a diff of the remainder against `python-brain` is mostly the engine itself.
4. If drift starts to hurt (more than a handful of ports per quarter), revisit the A/B decision: extract the engine into a private package both repos depend on. Not done now — premature, and the engine isn't packaged that way today.

## 7. First actions on this repo

1. Convert the backend scaffold per §4 (already moved under `backend/`); copy `agents/browser`, `shared/`, `api/`, `workflows/common/`, `Dockerfile`, `docker-compose.yml` into `backend/` and `observability/` into `ops/` from `python-brain`; apply the `customs` → `common` rename; stamp source shas.
2. Delete the customs-only functions/routers/models from `_INNGEST_FUNCTIONS` and the app (`pre_alert*`, `cipl*`, `checklist*`, `cmc_*`, `boe_*`, `eway*`, `fc_ingest`, `ooc/gatepass`, `duty_structure`, `outbound_job`) — they stay in `python-brain`.
3. Get `uv run pytest`, `ruff`, `mypy` green on what remains.
4. Add `config/*.yaml`, `customs/do.*` events, task stages, the DO models.
5. First vertical slice per [ENGINE-REUSE.md](ENGINE-REUSE.md) §6: port `cmc_transmit` → `do_request`, then `ooc_ingest` → `do_edo`.
6. Scaffold `frontend/` (Vite + React + TS), generate the API client from the backend OpenAPI schema, build Cases board + Approvals first, then Document review.
