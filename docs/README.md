# Documentation

| Doc | What it answers |
|---|---|
| [design/HLD.md](design/HLD.md) | What are we automating, with which systems, in what shape? Scope, actors, architecture, gates, plan, open questions |
| [design/LLD.md](design/LLD.md) | How does each piece work? Data model, state machine, event chain, SOPs, e-mail rules, document verification, API |
| [design/ENGINE-REUSE.md](design/ENGINE-REUSE.md) | What do we copy from `logistics-platform-1/python-brain`, what do we replace, what is new |
| [design/REPO-STRUCTURE.md](design/REPO-STRUCTURE.md) | Where does everything live (monorepo: `backend/`, `frontend/`, `docs/`, `ops/`), and why |
| `runbooks/` | Recovery drills per stage (added as stages are built) |
| `adr/` | One short file per later decision |

**Read order:** HLD → ENGINE-REUSE → REPO-STRUCTURE → LLD.
If LLD and ENGINE-REUSE disagree, ENGINE-REUSE wins (it was written after reading the engine code).

Source process: `DO PROCESS FLOW E2E.xlsx` (not stored in the repo).
