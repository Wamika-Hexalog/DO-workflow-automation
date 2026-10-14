"""Inngest workflows for the DO process, in python-brain's style: small functions,
each triggered by an event, that write their result to the database and emit the
next event. Registered with the API in main.create_app."""
from __future__ import annotations

import inngest

from ..runtime import Runtime
from .do_request import create_do_request_submit_function
from .email import create_email_inbound_function
from .folder import create_folder_build_function, create_folder_verify_function


def create_functions(rt: Runtime) -> list[inngest.Function]:
    return [
        create_folder_verify_function(rt),
        create_folder_build_function(rt),
        create_do_request_submit_function(rt),
        create_email_inbound_function(rt),
    ]
