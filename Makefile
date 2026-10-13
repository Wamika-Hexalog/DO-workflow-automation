.PHONY: setup assets api web inngest dev serve demo test

# uv installs Python 3.12 (backend/.python-version) and every backend package;
# npm installs the UI and the Inngest dev server (pinned in frontend/package.json).
setup:
	@command -v uv >/dev/null || { echo "uv is needed: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
	cd backend && uv sync
	cd frontend && npm install

api:
	cd backend && uv run uvicorn app.main:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev

# Runs the workflows: it calls the API at /api/inngest. Dashboard: http://localhost:8288
inngest:
	cd frontend && npx inngest-cli dev -u http://127.0.0.1:8000/api/inngest --no-discovery

dev:
	$(MAKE) -j3 api web inngest

serve:
	cd backend && uv run uvicorn app.main:create_app --factory --port 8000

demo:
	cd frontend && npm run build
	$(MAKE) -j2 serve inngest

test:
	cd backend && uv run pytest -q

# Stamps, bonds and letterhead artwork, built from the Docs folder the team shared.
assets:
	cd backend && uv run python scripts/prepare_assets.py ../Docs
