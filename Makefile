PYTHON ?= python3
VENV := backend/.venv

.PHONY: setup api web dev demo test template check-template

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

template:
	cd backend && .venv/bin/python scripts/build_placeholder_template.py

check-template:
	cd backend && .venv/bin/python scripts/check_template.py
