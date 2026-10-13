from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.containers import check_digit, problem
from app.folder import FolderError, load_folder
from app.main import create_app
from conftest import (
    HBL,
    MBL,
    RecordingInngest,
    attach,
    attach_everything,
    build,
    make_image,
    make_pdf,
    new_shipment,
    settings_for,
    slot,
)


def test_container_check_digit():
    assert check_digit("CSQU305438") == 3
    assert problem("CSQU3054383") is None
    assert problem("TCNU5191327") is None
    assert "check digit" in problem("CSQU3054384")
    assert "4 letters and 7 digits" in problem("CSQ3054383")


def test_folder_follows_the_excel_order(api):
    body = api.get("/api/folder").json()
    assert [d["key"] for d in body["documents"]] == [
        "bond_consignee", "bond_client", "hbl", "mbl", "security_receipt", "security_odex_receipt",
        "do_receipt", "noc_cha", "endorsed_do", "noc_hexalog", "authorization", "checklist",
    ]
    stamps = {d["key"]: d["stamp"] for d in body["documents"]}
    assert (stamps["hbl"], stamps["mbl"], stamps["noc_cha"], stamps["checklist"]) == (
        "envogue", "hexalog", "jai_shri", None,
    )


def test_folder_errors_name_the_problem(tmp_path):
    bad = tmp_path / "do_folder.yaml"
    bad.write_text(
        "shipment_groups:\n  - fields:\n      - {key: mbl_no, label: MBL}\n      - {key: hbl_nos, label: HBL}\n"
        "documents:\n  - {key: x, title: X, source: generate, uses: [nope]}\n",
        encoding="utf-8",
    )
    with pytest.raises(FolderError, match="uses 'nope'"):
        load_folder(bad)


def test_new_shipment_starts_with_defaults_and_the_next_job_number(api):
    s = api.post("/api/shipments", json={}).json()
    assert s["job_no"].endswith("/0001") and s["status"] == "igm_filed"
    assert s["values"]["cha"] == "JAY SHRI RAM DARBAR LOGISTICS (P) LTD"
    assert s["values"]["letter_date"]
    assert api.post("/api/shipments", json={}).json()["job_no"].endswith("/0002")


def test_house_bill_documents_repeat_for_each_hbl(api):
    s = new_shipment(api, hbl_nos="MSEC260900031273\nMSEC260900031466")
    ids = [x["id"] for x in s["slots"]]
    assert s["docs_total"] == 14
    assert ids[2:4] == ["hbl:MSEC260900031273", "hbl:MSEC260900031466"]
    assert "security_odex_receipt:MSEC260900031466" in ids


def test_generated_letters_wait_for_their_fields(api):
    s = new_shipment(api, voyage="")
    assert slot(s, "noc_cha")["flags"][0]["message"] == "Fill in Voyage."
    assert slot(s, "endorsed_do")["status"] == "ok"  # it doesn't print the voyage


def test_a_file_from_another_shipment_is_flagged(api):
    s = new_shipment(api)
    s = attach(api, s, "mbl", make_pdf("MASTER BILL GGZ9999999"))
    flags = slot(s, "mbl")["flags"]
    assert [f["kind"] for f in flags] == ["mismatch"]
    assert f"MBL {MBL} does not appear" in flags[0]["message"]

    s = attach(api, s, "mbl", make_pdf(f"MASTER BILL {MBL}"))
    assert slot(s, "mbl")["status"] == "ok"


def test_the_hbl_copy_must_name_its_own_house_bill(api):
    s = new_shipment(api, hbl_nos=f"{HBL}\nMSEC260803047403")
    s = attach(api, s, "hbl:MSEC260803047403", make_pdf("HOUSE BILL MSEC-260803047 -251"))
    assert slot(s, "hbl:MSEC260803047403")["flags"][0]["kind"] == "mismatch"


def test_a_scan_without_text_needs_a_check_by_eye(api):
    s = new_shipment(api)
    s = attach(api, s, "do_receipt", make_image(), name="receipt.png")
    assert slot(s, "do_receipt")["flags"][0]["kind"] == "unreadable"
    s = api.post(f"/api/shipments/{s['id']}/slots/do_receipt/check").json()
    assert slot(s, "do_receipt")["status"] == "ok"


def test_the_same_file_twice_is_a_duplicate(api):
    s = new_shipment(api)
    receipt = make_pdf(f"RECEIPT {MBL}")
    s = attach(api, s, "security_receipt", receipt)
    s = attach(api, s, "do_receipt", receipt)
    assert {f["kind"] for f in slot(s, "do_receipt")["flags"]} == {"duplicate"}


def test_a_file_already_used_by_another_job_is_flagged_on_the_later_one(api):
    receipt = make_pdf(f"RECEIPT {MBL} GGZ3207715")
    first = attach(api, new_shipment(api), "do_receipt", receipt)
    second = attach(api, new_shipment(api, mbl_no="GGZ3207715"), "do_receipt", receipt)
    assert "already attached to job" in slot(second, "do_receipt")["flags"][0]["message"]
    first = api.get(f"/api/shipments/{first['id']}").json()
    assert slot(first, "do_receipt")["status"] == "ok"


def test_files_that_are_not_documents_are_refused(api):
    s = new_shipment(api)
    response = api.post(f"/api/shipments/{s['id']}/slots/mbl/file", files={"file": ("notes.txt", b"hello")})
    assert response.status_code == 422 and "not a PDF or an image" in response.json()["detail"]
    fixed = api.post(f"/api/shipments/{s['id']}/slots/bond_client/file", files={"file": ("b.pdf", make_pdf("x"))})
    assert fixed.status_code == 409


def test_build_needs_review_and_every_document(api, engine):
    s = new_shipment(api)
    assert api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": True}).status_code == 409

    s = attach_everything(api, s)
    assert s["ready"] and s["docs_ok"] == 12
    assert api.post(f"/api/shipments/{s['id']}/build", json={"confirmed": False}).status_code == 422

    s = build(api, engine, s)
    assert s["status"] == "folder_ready" and s["folder"]["stale"] is False and s["building"] is False

    pdf = PdfReader(io.BytesIO(api.get(s["folder"]["url"]).content))
    texts = [page.extract_text() for page in pdf.pages]
    # 2 bonds + HBL + 2 MBL sheets + 3 receipts + 5 generated documents
    assert len(texts) == 13
    assert "CONSIGNEE BOND" in texts[0] and "CLIENT BOND" in texts[1]
    assert "HOUSE BILL" in texts[2] and "MASTER BILL" in texts[3] and "DO RECEIPT" in texts[7]
    assert "NO Objection Letter" in texts[8] and "Issue of Delivery order" in texts[9]
    assert "AUTHORIZE TO CHA" in texts[11] and "Please tick the appropriate box" in texts[12]
    # Every MBL sheet carries the stamp; the receipts don't.
    stamped = ["/XObject" in (page.get("/Resources") or {}) for page in pdf.pages[3:8]]
    assert stamped == [True, True, False, False, False]


def test_moving_past_folder_ready_needs_an_up_to_date_folder(api, engine):
    s = attach_everything(api, new_shipment(api))
    refused = api.post(f"/api/shipments/{s['id']}/move", json={"to": "do_requested"})
    assert refused.status_code == 409 and "Build the DO folder" in refused.json()["detail"]

    build(api, engine, s)
    moved = api.post(f"/api/shipments/{s['id']}/move", json={"to": "do_requested"}).json()
    assert moved["status"] == "do_requested" and moved["requested_on"]

    api.patch(f"/api/shipments/{s['id']}/values", json={"values": {"voyage": "0FBI6W1MA"}})
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["folder"]["stale"] is True
    assert api.post(f"/api/shipments/{s['id']}/move", json={"to": "edo_received"}).status_code == 409
    assert api.post(f"/api/shipments/{s['id']}/move", json={"to": "invoice_paid"}).status_code == 200


def test_fixed_details_feed_every_letter(api, engine):
    s = build(api, engine, attach_everything(api, new_shipment(api)))
    api.patch("/api/profile", json={"values": {"do_collector": "MR. RAHUL SINGH"}})
    s = api.get(f"/api/shipments/{s['id']}").json()
    assert s["folder"]["stale"] is True
    letter = api.get(slot(s, "endorsed_do")["preview_url"])
    assert "RAHUL SINGH" in PdfReader(io.BytesIO(letter.content)).pages[0].extract_text()


def test_a_generated_letter_can_be_replaced_by_the_signed_copy(api):
    s = new_shipment(api)
    s = attach(api, s, "endorsed_do", make_pdf(f"ENDORSED DO SIGNED BY CHA {MBL}"))
    endorsed = slot(s, "endorsed_do")
    assert endorsed["source"] == "upload" and endorsed["defined_source"] == "generate"
    assert endorsed["status"] == "ok"

    s = api.delete(f"/api/shipments/{s['id']}/slots/endorsed_do/file").json()
    assert slot(s, "endorsed_do")["source"] == "generate"


def test_a_missing_stamp_blocks_the_folder(tmp_path, private_dir):
    (private_dir / "stamps" / "envogue.png").unlink()
    with TestClient(create_app(settings_for(tmp_path, private_dir), RecordingInngest())) as api:
        s = new_shipment(api)
        setup = [f["message"] for f in slot(s, f"hbl:{HBL}")["flags"] if f["kind"] == "setup"]
        assert setup == ["The Envogue Styles stamp is missing. Run `make assets`."]
        assert slot(s, "mbl")["flags"] == [{"kind": "missing", "message": "Not received yet.", "can_check": False}]


def test_every_change_lands_in_the_audit_trail(api, engine):
    s = attach_everything(api, new_shipment(api))
    s = attach(api, s, "mbl", make_pdf(f"MASTER BILL {MBL} corrected"))
    build(api, engine, s)
    actions = [e["action"] for e in api.get(f"/api/shipments/{s['id']}/events").json()]
    assert actions[:4] == ["moved", "built", "task", "replaced"] and actions[-1] == "created"
    assert actions.count("attached") == 5


def test_demo_data_resets(tmp_path, private_dir):
    with TestClient(create_app(settings_for(tmp_path, private_dir, seed=True), RecordingInngest())) as api:
        board = api.get("/api/shipments").json()
        assert [s["job_no"][-4:] for s in board] == ["0438", "0436", "0434", "0431"]
        api.post("/api/shipments", json={})
        assert api.post("/api/demo/reset").json() == {"ok": True}
        assert len(api.get("/api/shipments").json()) == 4
