"""The injected code that stands in for a classifier."""
import pytest

from datetime import date

from app.email_code import fields_for, parse_injected_codes


def _first(body: str, *, prefix: str):  # noqa: ANN202
    codes = parse_injected_codes(body, prefix=prefix)
    return codes[0] if codes else None


def test_a_code_line_selects_the_stage_the_case_and_the_event() -> None:
    code = _first("Hi team\nDOHACK:invoice:ggz3193024\nThanks", prefix="DOHACK")
    assert code is not None
    assert (code.stage, code.mbl_no, code.arg) == ("invoice", "GGZ3193024", None)


def test_the_utr_after_the_case_is_the_argument() -> None:
    code = _first("DOHACK:utr:GGZ3193024:SBIN526012345678", prefix="DOHACK")
    assert code is not None
    assert code.arg == "SBIN526012345678"


def test_a_slot_id_with_a_colon_is_kept_whole_as_the_argument() -> None:
    code = _first("DOHACK:document:GGZ3193024:hbl:MSEC260803047251", prefix="DOHACK")
    assert code is not None
    assert code.arg == "hbl:MSEC260803047251"


def test_a_code_in_an_html_body_is_found_once_tags_and_entities_are_gone() -> None:
    body = "<div>Hello</div><p>DOHACK:&nbsp;edo : GGZ3193024</p><br>Regards"
    code = _first(body, prefix="DOHACK")
    assert code is not None
    assert (code.stage, code.mbl_no) == ("edo", "GGZ3193024")


@pytest.mark.parametrize(
    "body",
    [
        "no code here",
        "DOHACK:unknown-stage:GGZ3193024",
        "see DOHACK:invoice:GGZ3193024 inline",  # not at the start of a line
        "OTHER:invoice:GGZ3193024",
        "DOHACK:invoice:ab",  # too short to be an MBL
    ],
)
def test_mail_without_a_valid_code_is_ignored(body: str) -> None:
    assert _first(body, prefix="DOHACK") is None


def test_every_code_line_of_a_mail_is_returned_in_order() -> None:
    body = "DOHACK:utr:SHZ1234567:100000000001\nDOHACK:utr:SHZ1234567:deposit:100000000002\nFw: RTGS receipt"
    codes = parse_injected_codes(body, prefix="DOHACK")
    assert [(c.stage, c.arg) for c in codes] == [("utr", "100000000001"), ("utr", "deposit:100000000002")]


def test_utr_and_invoice_codes_name_the_shipment_field_they_fill() -> None:
    utr, deposit, invoice, edo = parse_injected_codes(
        "DOHACK:utr:SHZ1234567:100000000001\nDOHACK:utr:SHZ1234567:deposit:100000000002\n"
        "DOHACK:invoice:SHZ1234567:INVDEMO000001\nDOHACK:edo:SHZ1234567",
        prefix="DOHACK",
    )
    today = date.today().isoformat()
    assert fields_for(utr) == {"payment_utr": "100000000001", "payment_date": today}
    assert fields_for(deposit) == {"deposit_utr": "100000000002", "payment_date": today}
    assert fields_for(invoice) == {"do_invoice_no": "INVDEMO000001"}
    assert fields_for(edo) == {}


def test_codes_in_a_forwarded_subject_are_found_and_split_on_the_bar() -> None:
    subject = "FW: DOHACK:utr:SHZ1234567:100000000001 | DOHACK:invoice:SHZ1234567:INVDEMO000001 CMA CGM RTGS"
    codes = parse_injected_codes("", prefix="DOHACK", subject=subject)
    assert [(c.stage, c.arg) for c in codes] == [("utr", "100000000001"), ("invoice", "INVDEMO000001")]


def test_a_code_given_in_both_the_subject_and_the_body_counts_once() -> None:
    code = "DOHACK:edo:SHZ1234567"
    assert len(parse_injected_codes(code, prefix="DOHACK", subject=f"Fw: {code}")) == 1


def test_a_word_that_merely_ends_in_the_prefix_is_not_a_code_in_the_subject() -> None:
    assert parse_injected_codes("", prefix="DOHACK", subject="XDOHACK:utr:SHZ1234567:1") == []
