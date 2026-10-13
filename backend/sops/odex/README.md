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

**Parameters** the workflow passes, read as `params.<name>`:

| Name | What it is |
|---|---|
| `hbl_no` | The house bill this request is for |
| `mbl_no` | The master bill |
| `job_no` | The Hexalog job number |
| `folder_pdf_path` | The built DO folder PDF. One per MBL, uploaded for every house bill |

**Login**: a `credentials` block naming `ODEX_USERNAME` and `ODEX_PASSWORD` (set in
`backend/.env`), used with `value_from: credentials.username_env`, as python-brain's
ICEGATE SOP does.

**Result**: finish with an `execute_script` step marked `extract_result: true` that
returns `{"odex_request_ref": "<the reference ODeX shows>"}`. It goes into the job's
history. A step that throws fails the run, and ops are told to check ODeX before
approving again.

The same steps against a mock page are in
`tests/fixtures/mock_odex/sop_mock_odex_do_request.yaml`; `make test-browser` runs them.

## To write it

- An ODeX login for testing.
- Whether ODeX login asks for an OTP or a captcha (ICEGATE's OTP needed its own step).
- One walk-through of a real DO request, to record the page's selectors.
