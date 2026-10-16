"""The finance page (app/finance.py, app/invoice_reader.py): each job's DO invoice, read
from the PDF the agent collected from ODeX, with where its payment stands. The invoices
are the local ODeX's, laid out as CMA CGM's import tax invoice; no real invoice is a fixture."""
from app import invoice_reader
from local_portals import cma, documents
from tests.conftest import MBL, new_shipment


def invoice(sizes: list[str], usd_rate: float = cma.USD_RATE) -> bytes:
    charges, taxes = cma.invoice_lines(sizes, usd_rate)
    return documents.invoice_pdf(
        invoice_no="INIUPC00000001", on="2026-10-01", bl_no=MBL, vessel="CMA CGM TAGE", voyage="0FE5LW1MA",
        containers=["TCNU5191327", "CMAU4520173", "TGHU8812463"][: len(sizes)], charges=charges, taxes=taxes,
        usd_rate=usd_rate, request_ref="BL2026100100001",
    )


def test_the_reader_takes_both_tables_and_the_totals_from_the_invoice():
    # Protects: the charges table and the GST table come out row by row, a dollar charge with its rupees, and the
    # totals agree with the rows. Fails if the rows aren't rebuilt from the page, a column is misread, or a table
    # is dropped.
    read = invoice_reader.read(invoice(["40HC", "40HC", "20GP"], usd_rate=100.0))
    assert (read["invoice_no"], read["invoice_date"], read["bl_no"], read["usd_rate"]) == (
        "INIUPC00000001", "2026-10-01", MBL, 100.0
    )
    thc = [c for c in read["charges"] if c["description"].startswith("Terminal Handling")]
    assert [(c["size_type"], c["quantity"], c["basis"], c["amount_inr"]) for c in thc] == [
        ("40HC", 2, "UNI", 34930.0), ("20GP", 1, "UNI", 11640.0)
    ]
    customs = next(c for c in read["charges"] if c["currency"] == "USD" and c["size_type"] == "40HC")
    assert (customs["amount"], customs["amount_inr"]) == (128.0, 12800.0)
    assert len(read["charges"]) == len(read["taxes"]) == 12  # the per-invoice fees come once
    assert read["taxes"][0] == {
        "description": "UP IGST @ 18% THC DEST", "sac": "996711", "state": "HARYANA", "tax": "IH",
        "taxable": 34930.0, "amount": 6287.4,
    }
    # 40HC: 34,930 + 8,900 + 8,500 + 2,200 + 7,300 + 5,400 + 12,800; 20GP: 11,640 + 2,225 + 2,125 + 1,100 + 6,400.
    assert (read["total_excluding_tax"], read["total_tax"], read["total"], read["problems"]) == (
        103520.0, 18633.6, 122153.6, []
    )


def test_the_finance_page_lists_each_jobs_invoice_and_where_its_payment_stands(app, api):
    # Protects: the page's data: every job, with its collected invoice read and its payment from the job, and no
    # invoice until the agent has collected one. Fails if the invoice isn't read from where the agent saves it, or
    # the payment status ignores Finance's UTR.
    s = new_shipment(api)
    waiting = new_shipment(api, mbl_no="GGZ7654321", hbl_nos="", containers="")
    path = app.state.rt.settings.storage_dir / s["id"] / "portal" / f"DO-invoice-{MBL}.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(invoice(["40HC"]))

    jobs = {j["shipment_id"]: j for j in api.get("/api/finance").json()["jobs"]}
    assert (jobs[s["id"]]["invoice"]["total"], jobs[s["id"]]["payment"]["status"]) == (54720.68, "unpaid")
    assert jobs[waiting["id"]]["invoice"] is None
    assert api.get(jobs[s["id"]]["invoice_url"]).content == path.read_bytes()

    api.patch(f"/api/shipments/{s['id']}/values", json={"values": {"payment_utr": "SBIN526000000001"}})
    paid = next(j for j in api.get("/api/finance").json()["jobs"] if j["shipment_id"] == s["id"])
    assert paid["payment"] == {"status": "paid", "utr": "…0001", "date": None}
