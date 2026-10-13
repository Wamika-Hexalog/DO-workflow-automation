from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.containers import check_digit, problem
from app.do_set import DoSetError, load_do_set
from app.main import create_app


@pytest.fixture()
def api(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        do_set_path=BACKEND_DIR / "do_set.yaml",
        template_path=BACKEND_DIR / "templates" / "do_set.docx",
        output_dir=tmp_path / "generated",
        frontend_dist=tmp_path / "no-frontend",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def shipment(api: TestClient, job_suffix: str) -> dict:
    return next(s for s in api.get("/api/shipments").json() if s["job_no"].endswith(job_suffix))


def save(api: TestClient, shipment_id: str, **values):
    return api.patch(f"/api/shipments/{shipment_id}/values", json={"values": values})


def test_container_check_digit():
    assert check_digit("CSQU305438") == 3
    assert problem("CSQU3054383") is None
    assert "check digit" in problem("CSQU3054384")
    assert "4 letters and 7 digits" in problem("CSQ3054383")


def test_do_set_describes_fourteen_pages(api):
    body = api.get("/api/do-set").json()
    assert len(body["pages"]) == 14
    assert [stage["id"] for stage in body["stages"]] == ["pending", "ready", "requested", "received", "closed"]
    assert body["use_count"]["bl_no"] == 10


def test_do_set_errors_name_the_problem(tmp_path):
    bad = tmp_path / "do_set.yaml"
    bad.write_text("pages:\n  - id: p01\n    title: Letter\n    uses: [nope]\n", encoding="utf-8")
    with pytest.raises(DoSetError, match="uses 'nope'"):
        load_do_set(bad)


def test_seeded_board_progress(api):
    done = {s["job_no"][-4:]: s["pages_done"] for s in api.get("/api/shipments").json()}
    assert (done["0412"], done["0415"], done["0417"], done["0419"]) == (12, 7, 0, 0)
    assert all(done[n] == 14 for n in ["0405", "0408", "0398", "0401", "0390", "0393", "0381"])


def test_one_container_fix_and_the_igm_complete_six_pages(api):
    s = shipment(api, "0415")
    assert s["field_status"]["containers"] == "invalid"
    assert "check digit" in s["issues"]["containers"][0]

    first, typo = s["values"]["containers"].split("\n")
    fixed = typo[:10] + str(check_digit(typo[:10]))
    s = save(api, s["id"], containers=f"{first}\n{fixed}").json()
    assert s["pages_done"] == 11 and s["issues"] == {}

    s = save(api, s["id"], igm_no="2384733", igm_date="2026-10-13", igm_line="61").json()
    assert s["pages_done"] == 13


def test_stage_moves_wait_for_all_pages(api):
    early = shipment(api, "0417")
    refused = api.post(f"/api/shipments/{early['id']}/move", json={"to": "ready"})
    assert refused.status_code == 409 and "0 of 14" in refused.json()["detail"]

    ready = shipment(api, "0405")
    moved = api.post(f"/api/shipments/{ready['id']}/move", json={"to": "requested"}).json()
    assert moved["status"] == "requested" and moved["requested_on"]


def test_generate_fills_the_template_and_moves_to_docs_ready(api, tmp_path):
    s = shipment(api, "0412")
    assert api.post(f"/api/shipments/{s['id']}/generate").status_code == 409

    s = save(api, s["id"], p13_till="2026-10-30", p14_place="Gurugram", p14_date="2026-10-14").json()
    assert s["pages_done"] == 14
    s = api.post(f"/api/shipments/{s['id']}/generate").json()
    assert s["status"] == "ready" and s["document"]["filename"].endswith(".docx")

    download = api.get(s["document"]["url"])
    assert download.status_code == 200
    out = tmp_path / "out.docx"
    out.write_bytes(download.content)
    cells = [cell.text for table in Document(str(out)).tables for row in table.rows for cell in row.cells]
    text = "\n".join(cells)
    assert "MEDUH5734012" in text and "30/10/2026" in text and "{{" not in text


def test_values_that_do_not_belong_are_rejected(api):
    s = shipment(api, "0419")
    unknown = save(api, s["id"], nope="x")
    assert unknown.status_code == 422 and "nope" in unknown.json()["detail"]["fields"]
    assert save(api, s["id"], gstin="X").status_code == 422
    assert save(api, s["id"], bl_date="13/10/2026").status_code == 422
    assert save(api, s["id"], shipping_line="Not a line").status_code == 422


def test_client_profile_feeds_every_shipment(api):
    assert shipment(api, "0405")["pages_done"] == 14
    api.patch("/api/client", json={"values": {"gstin": ""}})
    assert shipment(api, "0405")["pages_done"] == 13


def test_new_shipment_takes_the_next_job_number(api):
    s = api.post("/api/shipments", json={}).json()
    assert s["job_no"].endswith("/0420") and s["status"] == "pending" and s["pages_done"] == 0


def test_reset_restores_the_demo_data(api):
    s = shipment(api, "0417")
    save(api, s["id"], igm_no="1")
    api.post("/api/shipments", json={})
    assert api.post("/api/demo/reset").json() == {"ok": True}
    board = api.get("/api/shipments").json()
    assert len(board) == 11 and shipment(api, "0417")["values"].get("igm_no") is None
