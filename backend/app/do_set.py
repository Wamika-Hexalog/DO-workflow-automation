"""The DO set definition from do_set.yaml, and the rules that turn field values
into page completion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import containers

FIELD_TYPES = frozenset({"text", "textarea", "date", "number", "select", "checkbox", "containers"})

STAGES: tuple[tuple[str, str], ...] = (
    ("pending", "Details pending"),
    ("ready", "Docs ready"),
    ("requested", "DO requested"),
    ("received", "eDO received"),
    ("closed", "Closed"),
)


class DoSetError(ValueError):
    """do_set.yaml has problems. The message lists each one."""


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    scope: str  # "client", "shipment" or "page"
    type: str = "text"
    options: tuple[str, ...] = ()
    code: bool = False

    def to_api(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "scope": self.scope,
            "type": self.type,
            "code": self.code,
        }
        if self.options:
            data["options"] = list(self.options)
        return data


@dataclass(frozen=True)
class PageDef:
    id: str
    title: str
    uses: tuple[str, ...]
    fields: tuple[FieldDef, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return self.uses + tuple(f.key for f in self.fields)


@dataclass(frozen=True)
class PageProgress:
    id: str
    filled: int
    total: int
    status: str  # "done", "part" or "empty"


@dataclass(frozen=True)
class DoSet:
    client_fields: tuple[FieldDef, ...]
    shipment_groups: tuple[tuple[str, tuple[FieldDef, ...]], ...]
    pages: tuple[PageDef, ...]
    fields: dict[str, FieldDef]
    date_format: str = "%d/%m/%Y"

    def use_count(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for page in self.pages:
            for key in page.uses:
                counts[key] = counts.get(key, 0) + 1
        return counts

    def to_api(self) -> dict[str, Any]:
        return {
            "stages": [{"id": stage_id, "name": name} for stage_id, name in STAGES],
            "client_fields": [f.to_api() for f in self.client_fields],
            "shipment_groups": [
                {"title": title, "fields": [f.to_api() for f in group]} for title, group in self.shipment_groups
            ],
            "pages": [
                {"id": p.id, "title": p.title, "uses": list(p.uses), "fields": [f.to_api() for f in p.fields]}
                for p in self.pages
            ],
            "use_count": self.use_count(),
        }

    def value(self, key: str, client_values: dict[str, Any], shipment_values: dict[str, Any]) -> Any:
        source = client_values if self.fields[key].scope == "client" else shipment_values
        return source.get(key)

    def progress(self, client_values: dict[str, Any], shipment_values: dict[str, Any]) -> list[PageProgress]:
        result = []
        for page in self.pages:
            filled = entered = 0
            for key in page.keys:
                field = self.fields[key]
                if is_filled(field, self.value(key, client_values, shipment_values)):
                    filled += 1
                    if field.scope != "client":
                        entered += 1
            total = len(page.keys)
            status = "done" if filled == total else "part" if entered else "empty"
            result.append(PageProgress(page.id, filled, total, status))
        return result

    def field_statuses(self, client_values: dict[str, Any], shipment_values: dict[str, Any]) -> dict[str, str]:
        return {
            key: field_status(field, self.value(key, client_values, shipment_values))
            for key, field in self.fields.items()
        }

    def validate(self, values: dict[str, Any], scopes: set[str]) -> dict[str, str]:
        """Why each value can't be stored, keyed by field. Empty when all are fine."""
        errors: dict[str, str] = {}
        for key, value in values.items():
            field = self.fields.get(key)
            if field is None:
                errors[key] = "is not a field in the DO set"
            elif field.scope not in scopes:
                errors[key] = "belongs to the client profile" if field.scope == "client" else "belongs to a shipment"
            elif (message := validate_value(field, value)) is not None:
                errors[key] = message
        return errors


def is_filled(field: FieldDef, value: Any) -> bool:
    if field.type == "checkbox":
        return value is True
    if field.type == "containers":
        return bool(containers.split(value)) and not containers.problems(value)
    return value is not None and str(value).strip() != ""


def field_status(field: FieldDef, value: Any) -> str:
    if is_filled(field, value):
        return "ok"
    if field.type == "containers" and containers.split(value):
        return "invalid"
    return "missing"


def validate_value(field: FieldDef, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if field.type == "checkbox":
        return None if isinstance(value, bool) else "must be true or false"
    if field.type == "date":
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError):
            return "must be a date in YYYY-MM-DD form"
        return None
    if field.type == "number":
        if isinstance(value, bool):
            return "must be a number"
        try:
            float(value)
        except (TypeError, ValueError):
            return "must be a number"
        return None
    if field.type == "select":
        return None if value in field.options else "must be one of the listed options"
    if not isinstance(value, str):
        return "must be text"
    return "is too long" if len(value) > 4000 else None


def template_value(field: FieldDef, value: Any, date_format: str) -> Any:
    """How a stored value is written into the generated document."""
    if value is None or value == "":
        return ""
    if field.type == "date":
        try:
            return date.fromisoformat(value).strftime(date_format)
        except (TypeError, ValueError):
            return value
    if field.type == "checkbox":
        return "Yes" if value else "No"
    if field.type == "containers":
        return ", ".join(containers.split(value))
    if field.code:
        return str(value).upper()
    return value


def load_do_set(path: Path) -> DoSet:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise DoSetError(f"Could not read {path}: {exc}") from exc

    issues: list[str] = []
    fields: dict[str, FieldDef] = {}

    def make_field(spec: Any, scope: str, where: str) -> FieldDef | None:
        if not isinstance(spec, dict):
            issues.append(f"{where}: expected a key and a label")
            return None
        key = str(spec.get("key", "")).strip()
        label = str(spec.get("label", "")).strip()
        field_type = str(spec.get("type", "text"))
        options = tuple(str(option) for option in spec.get("options") or ())
        if not key or not label:
            issues.append(f"{where}: needs both a key and a label")
            return None
        if key in fields:
            issues.append(f"{where}: key '{key}' is already used by another field")
            return None
        if field_type not in FIELD_TYPES:
            issues.append(f"{where}: '{key}' has unknown type '{field_type}'")
            return None
        if field_type == "select" and not options:
            issues.append(f"{where}: select field '{key}' needs options")
        field = FieldDef(key, label, scope, field_type, options, bool(spec.get("code", False)))
        fields[key] = field
        return field

    client_fields = tuple(
        field
        for i, spec in enumerate(raw.get("client_fields") or [])
        if (field := make_field(spec, "client", f"client_fields[{i}]"))
    )

    groups = []
    for gi, group in enumerate(raw.get("shipment_groups") or []):
        group = group or {}
        title = str(group.get("title", "")).strip() or f"Group {gi + 1}"
        group_fields = tuple(
            field
            for fi, spec in enumerate(group.get("fields") or [])
            if (field := make_field(spec, "shipment", f"shipment_groups[{gi}].fields[{fi}]"))
        )
        groups.append((title, group_fields))

    pages = []
    page_ids: set[str] = set()
    for pi, spec in enumerate(raw.get("pages") or []):
        spec = spec or {}
        where = f"pages[{pi}]"
        page_id = str(spec.get("id", "")).strip()
        title = str(spec.get("title", "")).strip()
        if not page_id or not title:
            issues.append(f"{where}: needs both an id and a title")
            continue
        if page_id in page_ids:
            issues.append(f"{where}: page id '{page_id}' is used twice")
        page_ids.add(page_id)
        uses = tuple(str(key) for key in spec.get("uses") or [])
        for key in uses:
            known = fields.get(key)
            if known is None or known.scope == "page":
                issues.append(f"{where} ({title}): uses '{key}', which is not a client or shipment field")
        page_fields = tuple(
            field
            for fi, fspec in enumerate(spec.get("fields") or [])
            if (field := make_field(fspec, "page", f"{where}.fields[{fi}]"))
        )
        pages.append(PageDef(page_id, title, uses, page_fields))

    if not pages:
        issues.append("pages: the DO set needs at least one page")
    if issues:
        raise DoSetError(f"{path} has problems:\n- " + "\n- ".join(issues))
    return DoSet(client_fields, tuple(groups), tuple(pages), fields, str(raw.get("date_format", "%d/%m/%Y")))
