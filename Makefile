.PHONY: setup assets api web inngest dev serve demo demo-agent test test-browser try-agent

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

# The whole app, with the browser agent raising DO requests on a mock ODeX page in a
# visible browser: build a folder, approve its DO request, and watch it work.
demo-agent:
	cd frontend && npm run build
	cd backend && uv run python scripts/mock_odex_sop.py
	DO_ODEX_SOP=$(CURDIR)/backend/data/mock_odex/sop_mock_odex_do_request.yaml DO_BROWSER_HEADLESS=0 \
		BROWSER_SLOW_MO_MS=$${BROWSER_SLOW_MO_MS:-700} GOOGLE_API_KEY=$${GOOGLE_API_KEY:-not-used-by-playwright-steps} \
		$(MAKE) -j2 serve inngest

test:
	cd backend && uv run pytest -q

# The browser agent against a mock ODeX page, in a real Chromium. DO_BROWSER_HEADLESS=0 shows it.
test-browser:
	cd backend && uv run pytest -q -m browser

# Checks the browser agent on a real website: logs in and uploads a dummy file on
# practice.expandtesting.com, a public practice site that prints its own test login.
# SHOW=1 opens the browser so you can watch.
try-agent:
	cd backend && EXAMPLE_SITE_USERNAME=practice EXAMPLE_SITE_PASSWORD='SuperSecretPassword!' \
		uv run python scripts/run_sop.py sops/examples/sop_example_login_and_upload.yaml \
		--param file_path=sops/examples/dummy-upload.txt $(if $(SHOW),--show)

# Stamps, bonds and letterhead artwork, built from the Docs folder the team shared.
assets:
	cd backend && uv run python scripts/prepare_assets.py ../Docs
