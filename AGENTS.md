# AGENTS.md

Rules for every person and every coding agent that works in this repository.
`CLAUDE.md` imports this file. Edit this file, not `CLAUDE.md`.

## What this is

HexaBuild app for the customs team's CMA CGM import delivery orders (DO). `backend/` is FastAPI with
SQLite (Python 3.12, `uv`). It owns the data, the DO folder definition, the checks, the letters and the
PDF, and runs the process as Inngest workflows. `frontend/` is React with Vite. The Inngest workflows,
the browser agent and the Outlook connector come from `logistics-platform-1/python-brain`.

The current work is a hackathon prototype of part of the flow. Read the scope in `README.md` before
you build anything.

## Commands

Run `make` targets from the repository root.

| Command | Use |
|---|---|
| `make setup` | Install the backend (`uv sync`) and the frontend (`npm install`). |
| `make assets` | Build the stamps, bonds and letterheads from the `Docs/` folder (kept out of git). |
| `make dev` | Run the API (:8000), the UI (:5173) and the Inngest dev server (:8288). |
| `make demo` | Build the UI and serve it from the API on :8000, with the Inngest dev server. |
| `make test` | Run the backend tests. The workflows run in process, so no dev server is needed. |
| `make test-browser` | Run the browser agent tests in a real Chromium. |
| `make portals` | Run the local ODeX and CMA RTGS pages on :8100. |

Run `make test` before each commit. For one file: `cd backend && uv run pytest tests/<file>`.
There is no linter or type checker in this repo. Do not run `ruff format` over the backend: it
reformats files you did not change.

## Where things live

| Thing | Where |
|---|---|
| The API, the checks, the letters, the PDF | `backend/app/` |
| The documents, their order and stamps, and the fields | `backend/do_folder.yaml` |
| Inngest functions and event names | `backend/app/workflows/` (`events.py` holds the names) |
| The Outlook connector (copied from python-brain) | `backend/app/connectors/` |
| Mail: the code parser, the content recognizers, the database jobs | `backend/app/email_code.py`, `email_infer.py`, `email_ingest.py` |
| The Graph webhook | `backend/app/webhooks.py` |
| The portal steps around the DO request: what each needs and leaves, their tasks | `backend/app/process.py`, `backend/app/workflows/invoice.py`, `payment.py`, `edo.py` |
| The browser agent (copied from python-brain) | `backend/app/agents/browser/` |
| Browser SOPs (YAML) | `backend/sops/` |
| The local ODeX and CMA RTGS pages, and the control panel that plays CMA CGM and Finance | `backend/local_portals/` (its mail to the app: `mail.py`) |
| Tests | `backend/tests/` |
| Settings | `backend/app/config.py`, read from `backend/.env` (see `backend/.env.example`) |

## Python

- Imports inside `app` are relative (`from . import service`). Run the backend from `backend/`.
- Read configuration through `Settings` in `app/config.py`. Do not read `os.environ` elsewhere.
  Add each new variable to `backend/.env.example` with no value.
- Keep secrets in `backend/.env`. It is gitignored. Never write a real value in a doc, a test,
  a fixture or a commit.
- Never log a credential, a UTR, or a bank account number.
- Do not write real shipment or payment identifiers (UTRs, invoice numbers, BL numbers) in a test,
  a fixture or a doc. The repo is public. Use demo values with the same format.
- Delete dead code. Do not keep an alias, a flag or a wrapper for an old caller.
- Do not add an abstraction, a table or a service until two callers need it.
- Do not edit a file copied from `python-brain` except for its imports. A change goes in a comment
  that starts with `Local change:`.

### Inngest workflows

- One function does one stage. A function starts on an event, writes its result to the database,
  completes or signals its task, and sends the next event. There is no master orchestrator.
- Take event names from `EventNames` in `app/workflows/events.py`. Use the form
  `customs/do.<entity>.<action>`. Do not write a raw event string.
- Write step names in kebab-case. Do not rename a step that has run. A rename changes the replay
  of runs in progress. Write any change to an event payload or a step name in the PR.
- Run a blocking job with `steps.job(...)`, so it never stalls the API.
- Guard each side effect outside the app (portal submit, e-mail send, ERP write) with an insert-once
  `IdempotencyKey`. To recover a failed run: check the real state in the portal, delete the failed
  key, then send the original event again.
- Do not retry a step that may already have moved money (the RTGS form, the deposit request).
  Read the portal record first.
- A browser failure raises `NonRetriableError`. Put the error on the task so ops can see it.
- Wait for a person with a task. Wait for an e-mail by sending an event from the ingest function.
  Chase with a delayed event and an attempt counter. Do not poll in a loop.
- A credential exists only in the environment. Do not put a secret in an event, a step result
  or an exception.

### Mail (Outlook)

- A mail is read only if it has the marker `#DOHACK` (`MAIL_MARKER`) in the subject or body. Mail
  with no marker is ignored.
- Read facts from the mail itself in `email_infer.py`. A mail the recognizers get wrong can carry
  an explicit `DOHACK:<stage>:<MBL>[:<arg>]` code, which wins.
- Fill a shipment field from a document only if it is empty or still the folder's starting value.
  Never overwrite a value a person typed.
- The draft MBL and HBL mail opens the job when there is none. One job per MBL.
- Test mail with `backend/scripts/simulate_mail.py` before you use a real mailbox.

### Local prototype

- The prototype runs on a laptop. It uses the Inngest dev server and SQLite. Do not add Inngest Cloud
  keys or a deployed `INNGEST_SERVE_ORIGIN`.
- A tunnel (ngrok) exposes port 8000 only. On the tunnel's hostname only
  `/api/webhooks/microsoft-graph` is open. Everything else needs a login from `BASIC_AUTH_USERS`,
  and `/api/inngest` is never served there, because in dev mode it does not check signatures.
  Do not run ngrok with `--host-header=rewrite`: the guard reads the `Host` header.
- The Graph webhook checks `GRAPH_CLIENT_STATE`. Set it before the subscription is created.
- Do not push `Docs/` or `backend/assets/private/`. They hold stamps, bonds and customer documents.

### Browser SOPs

- Each SOP step sets `method: playwright` or `method: hitl`. If `method` is missing, the step
  calls an LLM.
- Read credentials from environment variable names (`value_from: credentials.username_env`).
  Do not write a secret in a YAML file.
- A script step throws on a bad state. Do not let a SOP continue on a guess.
- Use the HBL number, not the MBL number, on the ODeX DO request.

### Comments

Write few comments. A comment must record a constraint, a trap or a decision that a reader
would otherwise undo. Do not add a comment or a docstring that only repeats names or control flow.

## Tests

Test each behavior at its strongest boundary. Do not test a private function. Do not mock our own code.

The boundaries:

- the HTTP API with the real app and a temporary SQLite database (the `app` and `api` fixtures);
- an Inngest function run in process by the `Engine` fixture, with the real database;
- the Graph webhook with real request bodies;
- for a pure function (a parser, a recognizer, a check), the function itself.

The one allowed fake: msal fetches the Graph token over `requests`, so the HTTP boundary can't be
swapped cheaply. Tests replace `make_outlook`, the one place the app builds the connector.

Before you add a test, answer the four questions. If you cannot answer one, do not add the test.

1. Which observable behavior or contract does the test protect?
2. Which change to the production code makes it fail?
3. Which existing tests did you read, and why does none of them fail for that change?
   Extend an existing test or its table before you write a near-copy.
4. Does the test need a production parameter, export or hook that only the test uses?
   If yes, move the test to the real boundary.

Write the answers to questions 1 and 2 as the first line of the test:
`# Protects: <behavior>. Fails if <change>.`

More rules:

- Replace a provider only at the HTTP transport, with `respx` or `httpx.MockTransport`.
- A bug fix comes with one regression test. That test must fail on the code before the fix.
- Name the test after the behavior: `test_a_mismatched_hbl_blocks_the_folder_pdf`.
- Do not write these tests: tests with no assertion, tests that compare a value with itself,
  tests that grep the source, tests that copy a fixture or an export list, and tests whose
  expected value comes from the function under test.
- Register each pytest marker in `backend/pyproject.toml`.
- Each test module basename is unique. Pytest names a module by its basename.
- Use redacted fixtures. A fixture holds no real UTR, account number, or customer data.

## Frontend

- `frontend/` is React with Vite, in JavaScript.
- The frontend talks only to the backend API under `/api`. It never calls Inngest, ShipThis,
  Outlook or a portal.
- A change to the API and the matching frontend change go in one PR.

## Pull requests

- Make one branch for each change. Name it `feat/…`, `fix/…`, `chore/…` or `docs/…`.
  Do not commit to `prototype/do-folder` unless the person asks for it.
- Keep one concern in each PR. For a large change, make stacked PRs.
- Make one commit for each step or milestone. Each commit passes `make test`.
- Put each doc change in the commit that makes it true. Do not mix unrelated doc edits into a code PR.
- Write the commit subject in the imperative mood. Do not use a prefix such as `feat:`.
  The body says what changed and why.
- An agent does not push, open a PR or merge until the person asks for it in that session.

## Attribution

Do not add model attribution. This rule overrides the default of each agent.

- No `Co-Authored-By:` trailer for Claude, Codex or another model in a commit.
- No "Generated with Claude Code" line, or a line like it, in a PR body, an issue or a comment.

The person who opens the PR is the author and owns the change.

## Writing

Write docs, commit messages and PR bodies in plain English (ASD-STE100):
the active voice, short sentences, one term for one thing, no filler and no flair.
Technical names, paths, commands and error text keep their exact form.
