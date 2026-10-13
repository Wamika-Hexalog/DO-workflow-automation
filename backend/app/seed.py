"""Dummy Evoque shipments for the demo, dated relative to today."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .containers import with_check_digit as box
from .db import Client, Shipment
from .do_set import DoSet

CLIENT_ID = "evoque"
CLIENT_NAME = "Evoque"
CLIENT_VALUES = {
    "importer_name": "Evoque",
    "importer_address": "Plot 22, Sector 8, IMT Manesar, Haryana 122051",
    "iec": "AAFCE4821K",
    "gstin": "06AAFCE4821K1Z3",
    "signatory_name": "Karan Malhotra",
    "signatory_designation": "Head of Logistics",
    "cha_name": "Hexalog Technologies Pvt Ltd",
    "cha_licence": "CB/DEL/0417",
}


def financial_year(today: date) -> str:
    """Indian financial year label, e.g. 26-27 for April 2026 to March 2027."""
    start = today.year if today.month >= 4 else today.year - 1
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def _day(offset: int) -> date:
    return date.today() + timedelta(days=offset)


def _iso(offset: int) -> str:
    return _day(offset).isoformat()


def _page_values(n: str) -> dict[str, Any]:
    return {
        "p01_date": _iso(-1), "p01_ref": f"HXL/DO/{n}",
        "p02_person": "Vikas Kumar", "p02_valid": _iso(30),
        "p03_reason": "Telex release", "p03_stamp": "500",
        "p04_yard": "Line empty yard near the ICD", "p04_free": "14",
        "p05_accept": True,
        "p06_mode": "Bank transfer", "p06_amount": "40000",
        "p07_date": _iso(-1),
        "p08_release": "Telex release", "p08_ref": f"TLX{n}26",
        "p09_cfs": "Line-nominated CFS at the ICD", "p09_mode": "Rail",
        "p10_days": "21", "p10_reason": "Plant shutdown over Diwali week, so destuffing needs extra time.",
        "p11_utr": f"HDFCR5202610{n}", "p11_amount": "38500", "p11_date": _iso(-1),
        "p12_hss": "Not applicable",
        "p13_till": _iso(12),
        "p14_place": "Gurugram", "p14_date": _iso(-1),
    }


def _complete(**values: Any) -> dict[str, Any]:
    return {
        "igm_date": _iso(-1),
        "packages": "22 pallets",
        "gross_weight": "15880",
        "cargo_desc": "Industrial components",
        **values,
    }


def _shipments(page_ids: list[str]) -> list[dict[str, Any]]:
    good = box("MRKU", "228471")
    typo = good[:10] + str((int(good[10]) + 3) % 10)
    return [
        dict(n="0412", owner="Ravi Sharma", status="pending", pages=page_ids[:12], values=dict(
            bl_no="MEDUH5734012", bl_date=_iso(-21), shipping_line="MSC", pol="Shanghai (CNSHA)",
            vessel="MSC Lorena", voyage="IU642W", eta=_iso(1), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Tughlakabad", igm_no="2384719", igm_date=_iso(-2), igm_line="124",
            containers="\n".join([box("MSCU", "482913"), box("MEDU", "731058")]), container_size="40HC",
            packages="26 pallets", gross_weight="18420", cargo_desc="Industrial components",
        )),
        dict(n="0415", owner="Sneha Nair", status="pending", pages=page_ids[:13], values=dict(
            bl_no="263518840", bl_date=_iso(-18), shipping_line="Maersk", pol="Ningbo (CNNGB)",
            vessel="Maersk Halden", voyage="642W", eta=_iso(3), pod="Mundra (INMUN1)",
            delivery_place="ICD Garhi Harsaru",
            containers="\n".join([box("MSKU", "905126"), typo]), container_size="40HC",
            packages="18 pallets", gross_weight="12960", cargo_desc="Industrial components",
        )),
        dict(n="0417", owner="Arjun Verma", status="pending", pages=[], values=dict(
            bl_no="CMDUSHZ2511843", bl_date=_iso(-10), shipping_line="CMA CGM", pol="Busan (KRPUS)",
            vessel="CMA CGM Rivoli", voyage="0FE7RW1MA", eta=_iso(6), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Patparganj",
            containers="\n".join([box("CMAU", "556120"), box("CGMU", "410387"), box("TGHU", "902554")]),
            container_size="20GP",
        )),
        dict(n="0419", owner="Meera Iyer", status="pending", pages=[], values=dict(
            bl_no="HLCUHAM2509871", shipping_line="Hapag-Lloyd", pol="Hamburg (DEHAM)",
            vessel="Lisbon Express", voyage="041E", eta=_iso(9),
        )),
        dict(n="0405", owner="Ravi Sharma", status="ready", pages=page_ids, values=_complete(
            bl_no="ONEYSHAF29871300", bl_date=_iso(-19), shipping_line="ONE", pol="Shanghai (CNSHA)",
            vessel="ONE Harmony", voyage="071W", eta=_iso(2), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Tughlakabad", igm_no="2384102", igm_line="88",
            containers="\n".join([box("ONEU", "128445"), box("TCLU", "330918")]), container_size="40HC",
        )),
        dict(n="0408", owner="Sneha Nair", status="ready", pages=page_ids, values=_complete(
            bl_no="EGLV142500318822", bl_date=_iso(-17), shipping_line="Evergreen", pol="Kaohsiung (TWKHH)",
            vessel="Ever Lumen", voyage="1185W", eta=_iso(4), pod="Mundra (INMUN1)",
            delivery_place="ICD Garhi Harsaru", igm_no="2384650", igm_line="41",
            containers="\n".join([box("EGHU", "904417"), box("EMCU", "152093"), box("EISU", "774610")]),
            container_size="20GP", packages="310 cartons", gross_weight="9875",
        )),
        dict(n="0398", owner="Arjun Verma", status="requested", pages=page_ids,
             dates=dict(requested_on=_day(-1)), values=_complete(
            bl_no="COSU6438290170", bl_date=_iso(-24), shipping_line="COSCO", pol="Qingdao (CNTAO)",
            vessel="Cosco Shenzhen", voyage="052W", eta=_iso(-1), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Tughlakabad", igm_no="2383377", igm_date=_iso(-3), igm_line="212",
            containers=box("CSNU", "618204"), container_size="40HC",
        )),
        dict(n="0401", owner="Meera Iyer", status="requested", pages=page_ids,
             dates=dict(requested_on=_day(0)), values=_complete(
            bl_no="MEDUH5698817", bl_date=_iso(-23), shipping_line="MSC", pol="Singapore (SGSIN)",
            vessel="MSC Tessa", voyage="IU641W", eta=_iso(-2), pod="Nhava Sheva (INNSA1)",
            delivery_place="CFS at port", igm_no="2383120", igm_date=_iso(-4), igm_line="57",
            containers="\n".join([box("MSDU", "204471"), box("MSCU", "993105")]), container_size="40GP",
        )),
        dict(n="0390", owner="Ravi Sharma", status="received", pages=page_ids,
             dates=dict(requested_on=_day(-4), edo_received_on=_day(-2), edo_valid_till=_day(9)), values=_complete(
            bl_no="263504417", bl_date=_iso(-28), shipping_line="Maersk", pol="Ningbo (CNNGB)",
            vessel="Maersk Elba", voyage="638W", eta=_iso(-5), pod="Mundra (INMUN1)",
            delivery_place="ICD Garhi Harsaru", igm_no="2382511", igm_date=_iso(-7), igm_line="19",
            containers=box("MRKU", "557802"), container_size="40HC",
        )),
        dict(n="0393", owner="Sneha Nair", status="received", pages=page_ids,
             dates=dict(requested_on=_day(-3), edo_received_on=_day(-1), edo_valid_till=_day(6)), values=_complete(
            bl_no="HLCUSHA2509342", bl_date=_iso(-26), shipping_line="Hapag-Lloyd", pol="Shanghai (CNSHA)",
            vessel="Kyoto Express", voyage="039W", eta=_iso(-4), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Tughlakabad", igm_no="2382790", igm_date=_iso(-6), igm_line="73",
            containers="\n".join([box("HLBU", "120339"), box("HLXU", "871226")]), container_size="40HC",
        )),
        dict(n="0381", owner="Arjun Verma", status="closed", pages=page_ids,
             dates=dict(requested_on=_day(-15), edo_received_on=_day(-13), edo_valid_till=_day(-5),
                        closed_on=_day(-3)), values=_complete(
            bl_no="CMDUNGB2507719", bl_date=_iso(-40), shipping_line="CMA CGM", pol="Ningbo (CNNGB)",
            vessel="CMA CGM Tage", voyage="0FE5LW1MA", eta=_iso(-16), pod="Nhava Sheva (INNSA1)",
            delivery_place="ICD Patparganj", igm_no="2380044", igm_date=_iso(-18), igm_line="102",
            containers=box("CMAU", "304918"), container_size="20GP",
        )),
    ]


def seed_demo_data(session: Session, do_set: DoSet, job_prefix: str) -> None:
    """Replaces everything in the database with the demo client and shipments."""
    def known(values: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in values.items() if key in do_set.fields}

    session.execute(delete(Shipment))
    session.execute(delete(Client))
    session.add(Client(id=CLIENT_ID, name=CLIENT_NAME, data=known(CLIENT_VALUES)))

    fy = financial_year(date.today())
    for spec in _shipments([page.id for page in do_set.pages]):
        page_values = {k: v for k, v in _page_values(spec["n"]).items() if k[:3] in spec["pages"]}
        session.add(Shipment(
            id=uuid.uuid4().hex[:12],
            job_no=f"{job_prefix}/{fy}/{spec['n']}",
            client_id=CLIENT_ID,
            status=spec["status"],
            owner=spec["owner"],
            data=known({**spec["values"], **page_values}),
            **spec.get("dates", {}),
        ))
    session.commit()
