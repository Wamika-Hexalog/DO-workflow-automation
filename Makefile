.PHONY: setup assets odex-login-screen api web inngest dev serve demo test test-browser try-agent portals portal-step demo-local

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

# The browser agent in a real Chromium: against a mock ODeX page, and through the
# whole process on the local portals. DO_BROWSER_HEADLESS=0 shows it.
test-browser:
	cd backend && uv run pytest -q -m browser

# Checks the browser agent on a real website: logs in and uploads a dummy file on
# practice.expandtesting.com, a public practice site that prints its own test login.
# SHOW=1 opens the browser so you can watch.
try-agent:
	cd backend && EXAMPLE_SITE_USERNAME=practice EXAMPLE_SITE_PASSWORD='SuperSecretPassword!' \
		DO_BROWSER_ALLOWED_HOSTS=practice.expandtesting.com uv run python scripts/run_sop.py sops/examples/sop_example_login_and_upload.yaml \
		--param file_path=sops/examples/dummy-upload.txt $(if $(SHOW),--show)

# Local versions of ODeX and CMA CGM's RTGS page, and a control panel that plays
# CMA CGM: http://127.0.0.1:8100. Only this machine can open them.
portals:
	cd backend && uv run uvicorn local_portals.main:create_app --factory --host 127.0.0.1 --port 8100

# One step of the DO process for a job, on the local portals, filled from the app's
# database: make portal-step JOB=0431 STEP=odex_deposit_new. SHOW=1 to watch, HBL=
# for one house bill. The steps are listed in backend/sops/local/README.md.
portal-step:
	cd backend && uv run python scripts/run_sop.py --job $(JOB) --step $(STEP) $(if $(HBL),--hbl $(HBL)) $(if $(SHOW),--show)

# The app, the Inngest dev server and the local portals, as one process: the local
# CMA CGM's mail goes to the app, the app runs each portal step in a visible,
# slowed-down browser, and ops approve the payments and the DO request in the app.
demo-local:
	cd frontend && npm run build
	DO_ODEX_SOP=$(CURDIR)/backend/sops/local/odex/do_request_new.yaml DO_BROWSER_HEADLESS=0 BROWSER_SLOW_MO_MS=400 \
		DO_AUTOMATE_PORTALS=1 DO_PORTALS_MAIL_TO=http://127.0.0.1:8000 \
		$(MAKE) -j3 serve inngest portals

# Stamps, bonds and letterhead artwork, built from the Docs folder the team shared.
assets:
	cd backend && uv run python scripts/prepare_assets.py ../Docs

# The local ODeX's login page is a screenshot of the real one, as anyone sees it
# before signing in: this takes it again (nothing is typed or sent) and says if
# ODeX has moved any of its fields.
odex-login-screen:
	cd backend && uv run python scripts/capture_odex_login.py
