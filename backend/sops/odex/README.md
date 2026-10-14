# ODeX SOPs

The DO request on ODeX is raised by python-brain's browser agent (`app/agents/browser`),
which follows a YAML SOP in the same format as python-brain's (`src/shared/sops`).

Until `sop_odex_do_request_new.yaml` exists in this folder, the app runs **by hand**:
ops raise the request on ODeX themselves and confirm it in the app. Once the file is
here (or `DO_ODEX_SOP` points at one), approving the DO request runs the agent instead.
Nothing else changes.

## `sop_odex_do_request_new.yaml` (still to write)

Runs once per house bill, after ops approve the "Raise the DO request on ODeX" task.
The SOP sheet's path: ODeX → e-Payment → DO Request → New DO Request, fill the fields
with the **HBL number, not the MBL**, attach the folder PDF through *Add Row*, save.

**Parameters** the workflow passes, read as `params.<name>` (built by `app/portal_params.py`):

| Name | What it is |
|---|---|
| `hbl_no` | The house bill this request is for |
| `mbl_no` | The master bill |
| `job_no` | The Hexalog job number |
| `folder_pdf_path` | The built DO folder PDF. One per MBL, uploaded for every house bill |
| `liner`, `do_type`, `delivery_type`, `containers` | From the fixed details and the job |
| `odex_url` | Where ODeX is (`DO_ODEX_URL`) |

**Login**: a `credentials` block naming `ODEX_USERNAME` and `ODEX_PASSWORD` (set in
`backend/.env`), used with `value_from: credentials.username_env`, as python-brain's
ICEGATE SOP does.

**Result**: an `execute_script` step marked `extract_result: true` that returns
`{"odex_request_ref": "<the reference ODeX shows>"}`. It goes into the job's history.
A step that throws fails the run, and ops are told to check ODeX before approving again.

**End**: the last step must be `- action: end`. Without it the agent repeats the last
step until its iterations run out, which on ODeX could mean submitting again and
again, so the app refuses such an SOP before opening a browser.

**Waiting**: prefer `wait_for: domcontentloaded` on `goto` and wait for the element the
next step needs. Waiting for `load` also waits on every slow third-party asset.

`sops/local/odex/do_request_new.yaml` does all of this on the local ODeX (`make portals`),
with the params `app/portal_params.py` builds from the job. It's the starting point:
copy it here, swap in the real page's selectors and the real login, and allow ODeX's host
in `DO_BROWSER_ALLOWED_HOSTS`. `sops/local/README.md` lists what's still needed.

To try an SOP on its own while writing it, without the app or Inngest:

    cd backend
    uv run python scripts/run_sop.py sops/odex/sop_odex_do_request_new.yaml \
        --param hbl_no=MSEC260803047251 --param folder_pdf_path=<a test PDF> --show

`--show` opens the browser in slow motion. `sops/examples/` has a working example
against a public practice site (`make try-agent SHOW=1`).

## To write it

- An ODeX login for testing.
- Whether ODeX login asks for an OTP or a captcha (ICEGATE's OTP needed its own step).
- One walk-through of a real DO request, to record the page's selectors.
