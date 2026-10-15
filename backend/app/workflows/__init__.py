"""Inngest workflows for the DO process, in python-brain's style: small functions,
each triggered by an event, that write their result to the database and emit the
next event. Registered with the API in main.create_app."""
from __future__ import annotations

import inngest

from ..runtime import Runtime
from .do_request import create_do_request_submit_function
from .edo import create_edo_collect_function
from .email import create_email_inbound_function
from .folder import create_folder_build_function, create_folder_verify_function
from .invoice import create_finance_mail_function, create_invoice_collect_function, create_invoice_request_function
from .payment import (
    create_deposit_request_function,
    create_payments_refresh_function,
    create_rtgs_deposit_function,
    create_rtgs_payment_function,
)
from .reminders import create_digest_function, create_reminders_function


def create_functions(rt: Runtime) -> list[inngest.Function]:
    return [
        create_invoice_request_function(rt),
        create_invoice_collect_function(rt),
        create_finance_mail_function(rt),
        create_payments_refresh_function(rt),
        create_rtgs_payment_function(rt),
        create_rtgs_deposit_function(rt),
        create_deposit_request_function(rt),
        create_folder_verify_function(rt),
        create_folder_build_function(rt),
        create_do_request_submit_function(rt),
        create_edo_collect_function(rt),
        create_email_inbound_function(rt),
        create_reminders_function(rt),
        create_digest_function(rt),
    ]
