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
