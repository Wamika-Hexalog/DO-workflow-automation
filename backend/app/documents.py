"""Fills the Word template with a shipment's values."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate

from . import containers
from .do_set import DoSet, template_value


class TemplateMissing(RuntimeError):
    pass


def extra_keys(do_set: DoSet) -> set[str]:
    """Placeholders the template can use besides the DO set fields. Every
    containers field also gets a <key>_list version for one row per container."""
    lists = {f"{key}_list" for key, field in do_set.fields.items() if field.type == "containers"}
    return {"job_no", "today"} | lists


def build_context(
    do_set: DoSet, client_values: dict[str, Any], shipment_values: dict[str, Any], job_no: str
) -> dict[str, Any]:
    context: dict[str, Any] = {
        key: template_value(field, do_set.value(key, client_values, shipment_values), do_set.date_format)
        for key, field in do_set.fields.items()
    }
    for key, field in do_set.fields.items():
        if field.type == "containers":
            context[f"{key}_list"] = containers.split(shipment_values.get(key))
    context["job_no"] = job_no
    context["today"] = date.today().strftime(do_set.date_format)
    return context


def render(template_path: Path, context: dict[str, Any], output_dir: Path, job_no: str) -> Path:
    if not template_path.is_file():
        raise TemplateMissing(
            f"No template at {template_path}. Run `make template` for a placeholder, or add the real DO set there."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_job = re.sub(r"[^A-Za-z0-9]+", "-", job_no).strip("-")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"{safe_job}-DO-set-{stamp}.docx"
    document = DocxTemplate(str(template_path))
    document.render(context, autoescape=True)
    document.save(str(path))
    return path
