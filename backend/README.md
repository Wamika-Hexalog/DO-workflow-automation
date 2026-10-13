# backend

FastAPI + Inngest + MongoDB service for DO workflow automation (browser agent, e-mail pipeline, ShipThis connector, Task Manager API).

Requires [uv](https://docs.astral.sh/uv/). Run everything from this directory.

```sh
uv sync                      # install deps into .venv
uv run pytest                # tests + coverage
uv run ruff check . && uv run ruff format .
uv run mypy
```
