"""Inngest event names. Import EventNames everywhere instead of writing strings.

Named as in the DO-workflow-automation design (docs/design/LLD.md §4.1):
customs/do.<entity>.<action>, plus python-brain's customs/task.completed.
Every DO event carries ``shipment_id``.
"""


class EventNames:
    # A file was attached to a slot of the folder: uploaded today, from e-mail later.
    DOCUMENT_RECEIVED = "customs/do.document.received"
    # Fields, the fixed details, a removal or a check by eye changed the folder.
    SHIPMENT_UPDATED = "customs/do.shipment.updated"
    # Every document is in place and checked; the folder waits for ops review.
    FOLDER_READY = "customs/do.folder.ready"
    # The folder PDF is built from what ops confirmed.
    DOCS_READY = "customs/do.docs.ready"
    # The DO request is raised on ODeX for every house bill.
    REQUEST_SUBMITTED = "customs/do.request.submitted"
    # Ops completed a task. Data: shipment_id, task_key, completed_by.
    TASK_COMPLETED = "customs/task.completed"
    # Graph told us a message arrived (python-brain's one legacy-named event).
    EMAIL_INBOUND = "EmailService:InboundMessage"
    # Mail with an injected code (hackathon mock flow). Data: shipment_id, mbl_no, arg, message_id.
    INVOICE_VERIFIED = "customs/do.invoice.verified"
    UTR_RECEIVED = "customs/do.payment.utr-received"
    EDO_RECEIVED = "customs/do.edo.received"


# Graph retries a notification it did not get a 2xx for. Inngest dedups the inbound event on
# message id for 24h. To re-run a message already seen, send force_reprocess with a new string:
# the empty string is the key that already ran, so that send is dropped with no error.
FORCE_REPROCESS_KEY = "force_reprocess"
INGEST_IDEMPOTENCY = f'event.data.message_id + ":" + event.data.{FORCE_REPROCESS_KEY}'
