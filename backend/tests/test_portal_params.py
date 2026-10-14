"""What the browser agent types into each portal page comes from the database
(app/portal_params.py): each step gets its job's fields, the fixed details and
the job's documents, and won't start while any of them is missing."""
from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader

from app import portal_params, service
from app.portal_params import (
    DEPOSIT,
    DEPOSIT_STATUS,
    DO_REQUEST,
    REQUEST_INVOICE,
    RTGS_DEPOSIT,
    RTGS_SHIPMENT,
    MissingData,
)
from conftest import HBL, MBL, attach, attach_everything, build, make_pdf, new_shipment

HBL_2 = "MSEC260803047403"

PAID = dict(
    do_invoice_no="inv-demo-0431", do_invoice_amount="54720.68", payment_utr="demoutr0431inv",
    deposit_utr="DEMOUTR0431DEP", payment_date="2026-10-01",
)


def params(api, shipment: dict, step: str) -> dict[str, dict[str, str]]:
    return portal_params.params_job(api.app.state.rt, shipment["id"], step)


def set_values(api, shipment: dict, **values) -> dict:
    response = api.patch(f"/api/shipments/{shipment['id']}/values", json={"values": values})
    assert response.status_code == 200, response.text
    return response.json()


def set_fixed(api, **values: str) -> None:
    assert api.patch("/api/profile", json={"values": values}).status_code == 200


def test_each_step_says_what_it_still_needs(api):
    with pytest.raises(MissingData) as missing:
        params(api, new_shipment(api, containers=""), REQUEST_INVOICE)
    assert str(missing.value) == "Requesting the DO invoice on ODeX needs Container nos. first."
    s = new_shipment(api)
    with pytest.raises(MissingData) as missing:
        params(api, s, RTGS_SHIPMENT)
    assert str(missing.value) == (
        "Confirming the shipment payment on CMA CGM's RTGS page needs UTR for the shipment payment, Bank account no. "
        "that pays CMA CGM (fixed details), DO invoice amount (₹), Date of transfer and DO invoice no. first."
    )
    with pytest.raises(MissingData, match="needs UTR for the security deposit, Bank account no. that pays CMA CGM "
                       "\\(fixed details\\) and Date of transfer first"):
        params(api, s, RTGS_DEPOSIT)
    with pytest.raises(MissingData, match="UTR for the security deposit, Date of transfer and the Security Receipt in the DO folder"):
        params(api, s, DEPOSIT)
    with pytest.raises(MissingData, match="Raising the DO request on ODeX needs a built DO folder first"):
        params(api, s, DO_REQUEST)
    # Missing data stops a workflow at once, with the message on the task.
    assert issubclass(MissingData, service.NotReady)


def test_the_invoice_request_and_the_payments_come_from_the_job(api):
    set_fixed(api, do_mailbox="do-desk@example.com", payer_account_no="123456789012")
    s = set_values(api, new_shipment(api, containers="TCNU5191327\nCMAU9987728"), **PAID)
    assert params(api, s, REQUEST_INVOICE) == {
        MBL: {
            "liner": "CMA CGM",
            "mbl_no": MBL,
            "containers": "TCNU5191327, CMAU9987728",
            "email": "do-desk@example.com",
            "job_no": s["job_no"],
            "odex_url": "http://127.0.0.1:8100/odex",
        }
    }
    # CMA CGM's RTGS page is filed twice: the shipment payment, then the security deposit.
    assert params(api, s, RTGS_SHIPMENT)[MBL] == {
        "company_name": "HEXALOG GLOBAL SUPPLY CHAINS PVT LTD",
        "contact_name": "Kishan Rawat",
        "contact_number": "8802757004",
        "emails": "do-desk@example.com",
        "utr": "DEMOUTR0431INV",
        "bank_account": "123456789012",
        "amount": "54720.68",
        "transfer_date": "01-10-2026",
        "security_deposit": "",
        "invoice_no": "INV-DEMO-0431",
        "bl_no": MBL,
        "cma_rtgs_url": "http://127.0.0.1:8100/cma-rtgs",
    }
    deposit = params(api, s, RTGS_DEPOSIT)[MBL]
    assert (deposit["security_deposit"], deposit["invoice_no"], deposit["bl_no"]) == ("yes", "", MBL)
    assert (deposit["utr"], deposit["amount"]) == ("DEMOUTR0431DEP", "40000.00")


def test_a_deposit_refunded_to_the_cha_carries_hexalogs_noc(api):
    s = set_values(api, new_shipment(api), **PAID)
    s = attach(api, s, "security_receipt", make_pdf(f"SECURITY RECEIPT 40,000 BL {MBL}"))
    deposit = params(api, s, DEPOSIT)[HBL]
    assert deposit["hbl_no"] == HBL and deposit["amount"] == "40000" and deposit["utr"] == "DEMOUTR0431DEP"
    assert deposit["refund_to"] == "Third Party"
    assert deposit["third_party_name"] == "JAY SHRI RAM DARBAR LOGISTICS (P) LTD"
    # Named for what it is: the portal shows the file's name to CMA CGM.
    assert Path(deposit["receipt_file"]).name == f"Security Receipt {MBL}.pdf"
    assert Path(deposit["receipt_file"]).read_bytes().startswith(b"%PDF-")
    noc = " ".join(PdfReader(deposit["noc_file"]).pages[0].extract_text().split())
    assert "NO OBJECTION" in noc and f"B/L Number: {MBL}" in noc and "Rs.40,000/-" in noc
    assert "in favour of M/s JAY SHRI RAM DARBAR LOGISTICS (P) LTD" in noc and "ABJCS8010P" in noc

    # A refund to the consignee who paid needs no NOC.
    s = set_values(api, s, deposit_refund_to="Consignee")
    deposit = params(api, s, DEPOSIT)[HBL]
    assert deposit["third_party_name"] == "" and deposit["noc_file"] == ""


def test_the_do_request_needs_an_up_to_date_folder(api, engine):
    s = build(api, engine, attach_everything(api, new_shipment(api)))
    request = params(api, s, DO_REQUEST)[HBL]
    assert request["hbl_no"] == HBL and request["mbl_no"] == MBL
    assert (request["do_type"], request["delivery_type"]) == ("E-DO", "Factory De-stuff")
    assert Path(request["folder_pdf_path"]).is_file()

    set_values(api, s, voyage="0FBI5W1MB")
    with pytest.raises(service.NotReady, match="changed after it was built"):
        params(api, s, DO_REQUEST)


def test_per_house_bill_steps_run_for_each_house_bill(api):
    s = new_shipment(api, hbl_nos=f"{HBL}\n{HBL_2}")
    runs = params(api, s, DEPOSIT_STATUS)
    assert list(runs) == [HBL, HBL_2]
    assert runs[HBL]["slip_save_path"].endswith(f"/portal/{HBL}-security-deposit-slip.pdf")
    assert runs[HBL_2]["slip_save_path"].endswith(f"/portal/{HBL_2}-security-deposit-slip.pdf")

    rt = api.app.state.rt
    with rt.sessionmaker() as session:
        shipment = service.load_shipment(session, s["id"])
        with pytest.raises(service.NotReady, match="is not one of the house bills"):
            portal_params.params_for(rt, session, shipment, DEPOSIT_STATUS, "MSEC000000000000")


def test_every_step_has_a_local_sop_the_agent_can_run_without_a_model():
    from app.agents.browser.utils.sop_reader import SOPReader

    for step in portal_params.STEPS.values():
        reader = SOPReader(str(step.sop))
        assert reader.read_sop(), step.sop
        steps = reader.get_steps()
        assert steps[-1]["action"] == "end", step.sop
        assert all(s["method"] == "playwright" for s in steps[:-1]), step.sop
