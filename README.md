# do-workflow-automation

Automation for the Delivery Order (DO) process (CMA CGM, ocean): browser agent (ODeX), e-mail pipeline, ShipThis ERP, and an ops dashboard.

> **Hackathon prototype.** We are building a slice of the full flow, not all of it. The design docs in [docs/](docs/README.md) describe the **whole** process; this README states what the prototype actually covers.

## Prototype scope

| Stage of the DO process | Prototype | How |
|---|---|---|
| **DO folder** — collect the 12 documents, verify (Missing / Duplicate / Mismatched), operator review, stitch one ordered PDF | **In** | System-generated docs + e-mail attachments; verifier; review task; `stitch_pdfs` |
| **ODeX: raise New DO Request per HBL** and upload the folder PDF | **In** | Browser agent (Playwright SOP), behind an ops approval gate |
| **Invoice → Finance → UTR** — liner invoice e-mail, forward to Finance with the ₹40k deposit, capture the UTR | **In** | Outlook (Graph) ingest + classifier + send; UTR extractor |
| Request DO invoice on ODeX | Out | Case is created manually or from ShipThis |
| CMA RTGS website form + receipts | Out | Done by hand |
| ODeX deposit request + deposit approval | Out | Done by hand; ops marks the **Deposit Approved** task complete to unlock the folder/DO-request stages |
| Refund request + 14-day tracking | Out | Not built |
| eDO receipt + completion | Out | Ops marks the case complete (nice-to-have if time allows) |

**Frontend:** the **full dashboard** is in scope — cases board, case detail/timeline, document review, approvals, HITL inbox, SLA view. Screens for stages that are out of scope still render, showing those stages as manually-completed tasks, so the dashboard is the same one that will serve the full flow.

### Demo path

1. A case exists (manual create or ShipThis) with MBL, HBLs, container, vessel/voyage, consignees, CHA.
2. Liner invoice e-mail arrives → classified → forwarded to Finance with the ₹40k deposit called out → Finance replies with the UTR → captured on the case.
3. *(Manual, off-system: RTGS form, ODeX deposit, deposit approval → ops completes the **Deposit Approved** task.)*
4. Folder opens: system documents are generated, e-mailed HBL/MBL/receipts are collected and matched to the case, the verifier raises flags.
5. Operator clears flags in the dashboard; the folder PDF is built and confirmed.
6. Ops approves the DO request; the browser agent raises the New DO Request on ODeX per HBL (HBL number, not MBL) and uploads the folder.
7. Case timeline shows every step, source file and who approved what.

## Repository

| Folder | What |
|---|---|
| [backend/](backend/) | Python service: FastAPI, Inngest workflows, browser agent, connectors |
| [frontend/](frontend/) | Ops dashboard |
| [docs/](docs/README.md) | HLD, LLD, engine reuse map, repo structure |

The backend reuses the engine from `logistics-platform-1/python-brain` (Inngest + browser agent + Outlook + ShipThis + Task Manager). What is copied, replaced and new is in [docs/design/ENGINE-REUSE.md](docs/design/ENGINE-REUSE.md); the layout is in [docs/design/REPO-STRUCTURE.md](docs/design/REPO-STRUCTURE.md).

## Development

```sh
cd backend && uv sync && uv run pytest        # backend
cd frontend                                   # see frontend/README.md
uv run --project backend pre-commit install   # git hooks (repo-wide)
```

## Status

Design docs are drafted; the backend is a scaffold (the engine has not been copied in yet); the frontend is not scaffolded. See the first actions in [docs/design/REPO-STRUCTURE.md](docs/design/REPO-STRUCTURE.md) §7.
