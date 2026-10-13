.PHONY: setup assets api web dev demo test

# uv installs Python 3.12 (backend/.python-version) and every backend package.
setup:
	@command -v uv >/dev/null || { echo "uv is needed: https://docs.astral.sh/uv/getting-started/installation/"; exit 1; }
	cd backend && uv sync
	cd frontend && npm install

api:
	cd backend && uv run uvicorn app.main:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 api web

demo:
	cd frontend && npm run build
	cd backend && uv run uvicorn app.main:create_app --factory --port 8000

test:
	cd backend && uv run pytest -q

# Stamps, bonds and letterhead artwork, built from the Docs folder the team shared.
assets:
	cd backend && uv run python scripts/prepare_assets.py ../Docs
