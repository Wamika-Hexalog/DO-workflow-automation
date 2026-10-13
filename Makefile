PYTHON ?= python3
VENV := backend/.venv

.PHONY: setup assets api web dev demo test

setup:
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is needed, found " + sys.version.split()[0])'
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

api:
	cd backend && .venv/bin/uvicorn app.main:create_app --factory --reload --port 8000

web:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 api web

demo:
	cd frontend && npm run build
	cd backend && .venv/bin/uvicorn app.main:create_app --factory --port 8000

test:
	cd backend && .venv/bin/python -m pytest -q

# Stamps, bonds and letterhead artwork, built from the Docs folder the team shared.
assets:
	cd backend && .venv/bin/python scripts/prepare_assets.py ../Docs
