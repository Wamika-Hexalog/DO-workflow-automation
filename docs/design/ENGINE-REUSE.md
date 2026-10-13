# Engine reuse map — what to copy from `logistics-platform-1/python-brain`

Source: `~/Documents/logistics-platform-1/python-brain/src` (read directly, not just its docs). Copied code lands under `backend/` in this monorepo; `frontend/` has no counterpart to copy. The DO project keeps the **engine** identical and replaces only the **use case** (air customs → ocean DO). Companion to [HLD.md](HLD.md) and [LLD.md](LLD.md); where they disagree, this file wins (it was written after reading the code).

## 1. How the engine actually orchestrates

This is the part the first LLD draft got wrong. There is **no master orchestrator**. The pattern is:

```
Graph webhook ──► EmailService:InboundMessage
                     │  one function: fetch → classify → client.send(<typed event>, slim email payload)
                     ▼
        customs/email.<kind>.process        (one small Inngest function per email kind)
                     │  extract → match to record → write Mongo → signal task → client.send(next event)
                     ▼
        customs/browser.<x>.process / customs/task.completed
                     │  claim (insert-once) → _run_sop(...) → record result → alert on failure
                     ▼
        next event …   (each function = one stage; the Mongo record + Task list is the state)
```

| Concern | How `python-brain` does it | File |
|---|---|---|
| State | Fields on the Mongo `Shipment` doc + a seeded `Task` list; **no state-machine object**. Stages advance when a function writes its milestone and emits the next event | `db/models/shipment.py`, `db/models/task.py`, `services/tasks.py` |
| Chaining | `await client.send(inngest.Event(name=EventNames.X, data=with_trace({...}, current_trace_id())))` | `workflows/customs/email/email_ingest.py:810-1030` |
| Waiting on people | Two idioms: **(a)** inline `wait_for_task_approval` (parks the run, up to 7 d) and **(b)** trigger a function on `customs/task.completed` filtered by `task_key` — no timeout to outlive | `task_gate.py`, `cmc_transmit.py` |
| Waiting on e-mail replies | **Not** `wait_for_event`. Reply detected in ingest by `conversation_id` lookup → fires `CHECKLIST_CLIENT_REPLIED`; follow-ups are **delayed events** (`CHECKLIST_FOLLOW_UP_DUE`, 24 h, max 3 attempts) | `checklist_send_email.py`, `checklist_reply.py` |
| Browser | `_run_sop(sop_path, params, job_label, run_id, max_iterations, shipment_id, task_key)` — builds `LangGraphReactAgent`, `SOPReader`, runs, always closes; any failure → `NonRetriableError` | `browser/duty_structure.py:1440` |
| One browser session per portal | `inngest.Concurrency(limit=1, scope="env", key='"staredi-browser"')` passed to `create_function(concurrency=[…])` | `browser/cmc_filing.py:72` |
| Double-submit protection | `claim_mawb_action(scope, key)` inserts `IdempotencyKey(key=f"{scope}:{id}", status="pending", expires_at=…)`; `DuplicateKeyError` = someone else owns it; `fail_mawb_claim` flips to `failed`; **recovery = fix externally, delete the key, re-send the event** | `browser/cmc_filing.py:448, 1037, 1046` |
| Failure | Raise `NonRetriableError`; `send_tech_alert({...})`; `flag_task_failure(shipment_id, task_key, text=build_failure_note(...))`; global `create_failure_notify_function` consumes `inngest/function.failed` | `services/alerts.py`, `services/tasks.py`, `notify.py` |
| Run hygiene | `clear_log_context` / `bind_log_context(service, run_id, fn, …, trace_id)` at the top; `log_run_start`; `record_function_run`; `with_trace` on every emitted event | `log_context.py`, `run_start.py`, `services/function_runs.py` |
| Registration | One `create_*_function(client)` factory per function, listed in `_INNGEST_FUNCTIONS`, served with `inngest.fast_api.serve` | `main.py:224-285` |
| Cron | `create_erp_poll_function` (`*/15`), `mailbox_sweep`, `cmc_sweeper` | `erp_poll.py`, `email/mailbox_sweep.py` |

**Consequence for the DO design:** drop the `do-case` orchestrator with `step.invoke`/`wait_for_event` chains from LLD §4.2. Build one small function per stage, chained by events, state in Mongo + Tasks. The LLD has been updated accordingly.

## 2. Copy as-is (engine — do not redesign)

| Area | Files | Notes |
|---|---|---|
| Browser agent | `agents/browser/core/{agent,graph,state}.py`, `nodes/{decision,tool,self_healing}_node.py`, `integrations/{stagehand_client,llm_client,llm_models,browserbase_live}.py`, `utils/*`, `repository.py` | ~7,000 lines; Playwright-only SOPs → zero LLM calls on the hot path. Copy whole, trim nothing |
| SOP runner | `_run_sop` from `duty_structure.py:1440-1513` | Lift into `workflows/browser/run_sop.py` (it is imported from `duty_structure` today, which is a smell worth fixing in the copy) |
| Inngest bootstrap | `shared/inngest_client.py`, `main.py` shape (lifespan, Graph subscription + 6-hourly renewer, `NormalizePathMiddleware`, CORS, `fast_api.serve`) | Change `app_id` to `do-workflow-automation` |
| Email in | `connectors/outlook.py`, `outlook_helpers.py`, `graph_subscription.py`, `workflows/customs/email/{email_ingest,mailbox_sweep,hitl_reply,helpers,state,constants}.py` | `OutlookClient.send_email(to, subject, body_html, from_address, attachment_paths)` and `.reply_to_message(message_id, body_html)` already return `{id, conversationId}` — exactly what reply tracking needs |
| Classifier | `services/llm/{deterministic_classifier,email_classifier_llm,openrouter_client,health}.py` | Keep the class machinery, replace the **rules** (§3) |
| Task gate / Task Manager | `workflows/customs/task_gate.py`, `services/tasks.py`, `db/models/task.py`, `api/routers/{tasks,auth,shipment}.py`, `services/auth.py` | Replace `Stage` enum + `TASK_SEED_LIST` + `GATED_TASK_KEYS` (§3) |
| Cross-cutting | `config.py` pattern, `events.py` pattern, `log_context.py`, `logging_config.py`, `log_redaction.py`, `http.py`, `services/{alerts,alert_dedup,function_runs,shipment_lock}.py`, `connectors/slack.py`, `workflows/customs/{notify,run_start}.py` | |
| Models | `hitl_request`, `idempotency_key`, `automation_run`, `function_run`, `outbound_email`, `erp_outbox`, `user`, `notification`, `email`, `document` | |
| ERP connector | `connectors/shipthis.py` (`ShipThisClient`), `shipthis_helpers.py`, `erp_tasks.py`, `document_upload.py` | **Sea** methods exist but are thinner than air — see §4 |
| PDF | `utils/pdf_stitcher.py` (`stitch_pdfs`), `services/weasyprint_pdf.py`, `processors/document_reader.py`, `extend_parser.py` | |
| Observability / deploy | `observability/`, `Dockerfile`, `docker-compose.yml`, `scripts/seed_user.py`, `scripts/check_no_secret_logging.py` | |

## 3. Replace (same shape, new use case)

| Piece | Today (customs, air) | DO |
|---|---|---|
| `events.py` constants | `customs/email.pre_alert.process`, `…cipl…`, `…ooc…`, `browser.cmc-filing`… | `customs/do.*` — see LLD §4.1 (keep the `customs/<domain>.<entity>.<action>` convention) |
| Classifier rules (`deterministic_classifier.py`, 2,028 lines) | pre-alert, CI/PL, checklist, OOC, gate pass, FC… | `do_invoice_verified`, `do_payment_utr`, `do_rtgs_receipt`, `do_document` (HBL/MBL/receipts), `do_edo`, plus the existing anti-misroute rails (Priority-0 hashtag, internal-human sender checks) |
| Per-kind e-mail functions | `pre_alert.py`, `cipl_ingest.py`, `ooc_ingest.py`, `checklist*.py`, `fc_ingest.py` | `do_invoice.py`, `do_utr.py`, `do_receipts.py`, `do_documents.py`, `do_edo.py`, `do_chase.py` — copy `ooc_ingest.py` (497 lines, simplest) as the template |
| Browser workflows | `outbound_job.py`, `duty_structure.py`, `cmc_filing.py`, `cmc_transmit.py` | `do_request_invoice.py`, `do_rtgs_submit.py`, `do_deposit.py`, `do_request.py`, `do_refund.py` — copy `cmc_transmit.py` (522 lines) as the template: gate trigger → claim → `_run_sop` → record → alert |
| SOP YAML | `sops/logisys/*`, `sops/staredi/*` | `sops/odex/*`, `sops/cma_rtgs/*` (LLD §8) |
| Concurrency keys | `"staredi-browser"` | `"odex-browser"`, `"cma-rtgs-browser"` |
| `Stage` / `TASK_SEED_LIST` / `GATED_TASK_KEYS` | 10 stages / 24 tasks / 3 gates | DO stages (below) |
| Claim scope + key | `cmc_filing:{mawb}`, `cmc_transmit:{mawb}` | `do_invoice:{mbl}`, `do_rtgs:{mbl}`, `do_deposit:{mbl}:{bl}`, `do_request:{hbl}`, `do_refund:{mbl}` |
| Alert `pipeline` strings | `browser-cmc-transmit` | `browser-odex-*`, `email-do-*` |

### DO stages for `Stage` / `TASK_SEED_LIST`

| Stage | Tasks (gated ★) |
|---|---|
| `invoice` | request_invoice (auto) · invoice_verified (auto) |
| `deposit_payment` | sent_to_finance (auto) · utr_received (auto) · rtgs_submitted (auto) · receipts_received (auto) |
| `deposit_request` | odex_deposit_request ★ · deposit_approved (auto) |
| `do_folder` | documents_collected (auto) · documents_verified ★ (ops review when any flag) · folder_confirmed |
| `do_request` | odex_do_request ★ (one per HBL) · edo_received (auto) |
| `refund` | refund_request ★ · refunded (auto, 14-day SLA) |
| `delivery`/done | terminal |

Gates reuse both idioms: `odex_deposit_request` and `documents_verified` via inline `wait_for_task_approval`; `odex_do_request` via a `customs/task.completed`-triggered function (like `cmc_transmit`), so approval works whenever it happens.

## 4. Genuinely new (nothing to copy)

1. **Ocean/MBL/HBL data on the `Shipment` doc.** `shipment_mode: Literal["air","sea"]` exists, but the model, matcher and ERP helpers are air-shaped (MAWB/HAWB). Add `do` sub-document (`customs.cmc` is the pattern): `mbl_no`, `hbl_nos[]`, `invoice`, `payment{utr}`, `rtgs`, `deposit`, `folder`, `refund`. **Decision to confirm:** keep DO state on the existing `Shipment` (as `python-brain` does) vs. a separate `do_cases` collection (LLD §2). Copying the engine as-is points to the former; the latter is cleaner if this ships as its own service.
2. **ShipThis sea reads/writes.** `get_sea_shipment`, `list_sea_shipments`, `update_sea_shipment`, `upload_sea_document` exist; there is no sea equivalent of `append_air_custom_doc`, `find_air_shipment_by_boe`, or the air document-category map. Add the ones DO needs.
3. **ODeX and CMA RTGS SOPs** — recorded against the live portals.
4. **Document collection / verification / flags** (LLD §6). The closest existing code is `cipl_steps.py` + `document_filter.py`, but the Missing/Duplicate/Mismatched model and the 12-document `documents.yaml` are new.
5. **System-document generators** (Bond, NOC, Authorization Letter, Checklist) — `weasyprint_pdf` is the renderer; templates are new.
6. **UTR / receipt / invoice extractors** and finance-mail template.
7. **Refund 14-day timer** — reuse the delayed-event pattern from `checklist_follow_up`.

## 5. Repo decision — resolved: B

Decided in [REPO-STRUCTURE.md](REPO-STRUCTURE.md): own repo, engine copied with `python-brain`'s layout preserved. The two options considered, for the record:

**Where does this live?** Two ways to "copy the engine":

- **A. New module inside `python-brain`** — DO functions registered next to the customs ones (`_INNGEST_FUNCTIONS`), same Mongo, same Task Manager and dashboard, no copy drift. Least code. Couples DO releases to customs releases.
- **B. Copy the engine into this repo** (what you described) — independent deploy and blast radius; cost is maintaining two copies of ~15k lines of engine.

B chosen. If drift later hurts, the fallback is A or extracting the engine into a shared package (REPO-STRUCTURE §6).

## 6. Suggested first slice (revised)

1. Copy the engine (§2) into this repo, get `uv run pytest` green on the copied tests, strip customs-only functions from `_INNGEST_FUNCTIONS`.
2. Add `customs/do.*` events, `Stage`/`TASK_SEED_LIST`, the `do` sub-document.
3. Port `cmc_transmit.py` → `do_request.py` and write `odex/do_request_new.yaml` — the cleanest end-to-end proof of browser + claim + gate + alert on the new use case.
4. Port `ooc_ingest.py` → `do_edo.py` for the e-mail side.
5. Then the document collection/verification service, then invoice → UTR → RTGS → deposit.
