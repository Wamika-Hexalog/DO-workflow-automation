"""The DO folder definition from do_folder.yaml, and the rules for field values."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from . import containers

FIELD_TYPES = frozenset({"text", "textarea", "date", "number", "select", "checkbox", "containers", "list"})
SOURCES = frozenset({"fixed", "upload", "generate"})
MATCH_KEYS = frozenset({"mbl_no", "hbl", "containers"})

# The CMA CGM delivery order process, from IGM filing to the eDO.
STAGES: tuple[tuple[str, str], ...] = (
    ("igm_filed", "IGM filed"),
    ("invoice_paid", "Invoice paid"),
    ("deposit_approved", "Deposit approved"),
    ("folder_ready", "DO folder ready"),
    ("do_requested", "DO requested"),
    ("edo_received", "eDO received"),
)
STAGE_IDS = tuple(stage_id for stage_id, _ in STAGES)
# From this stage on, a shipment needs a built, up-to-date DO folder.
FOLDER_STAGE = "folder_ready"

_LINES = re.compile(r"[\n,;]+")


class FolderError(ValueError):
    """do_folder.yaml has problems. The message lists each one."""


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    scope: str  # "profile" or "shipment"
    type: str = "text"
    options: tuple[str, ...] = ()
    code: bool = False
    optional: bool = False
    default: Any = None

    def to_api(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "scope": self.scope,
            "type": self.type,
            "code": self.code,
            "optional": self.optional,
        }
        if self.options:
            data["options"] = list(self.options)
        return data


@dataclass(frozen=True)
class StampDef:
    key: str
    file: str
    name: str


@dataclass(frozen=True)
class Placement:
    """Where a stamp goes. Uploads use the page fractions; letters only the stamp."""

    stamp: str
    pages: str = "first"  # "first" or "all"
    x: float = 0.5
    y: float = 0.5
    width: float = 0.18


@dataclass(frozen=True)
class DocDef:
    key: str
    title: str
    source: str
    per_hbl: bool = False
    match: tuple[str, ...] = ()
    stamp: Placement | None = None
    file: str | None = None
    pages: tuple[int, ...] = ()
    uses: tuple[str, ...] = ()

    def to_api(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "source": self.source,
            "per_hbl": self.per_hbl,
            "stamp": self.stamp.stamp if self.stamp else None,
            "uses": list(self.uses),
        }


@dataclass(frozen=True)
class Slot:
    """One place in the folder: a document, and for per-HBL documents, its HBL."""

    doc: DocDef
    hbl: str | None

    @property
    def id(self) -> str:
        return f"{self.doc.key}:{self.hbl}" if self.hbl else self.doc.key


@dataclass(frozen=True)
class Folder:
    profile_fields: tuple[FieldDef, ...]
    shipment_groups: tuple[tuple[str, tuple[FieldDef, ...]], ...]
    fields: dict[str, FieldDef]
    stamps: dict[str, StampDef]
    documents: tuple[DocDef, ...]
    date_format: str = "%d/%m/%Y"

    @property
    def shipment_fields(self) -> tuple[FieldDef, ...]:
        return tuple(field for _, group in self.shipment_groups for field in group)

    def doc(self, key: str) -> DocDef | None:
        return next((d for d in self.documents if d.key == key), None)

    def slots(self, shipment_values: dict[str, Any]) -> list[Slot]:
        """Every document of the folder in PDF order, per-HBL documents once per HBL.
        Before any HBL is entered, a per-HBL document holds one empty slot."""
        hbls = split_list(shipment_values.get("hbl_nos"))
        result = []
        for doc in self.documents:
            if doc.per_hbl:
                result.extend(Slot(doc, hbl) for hbl in hbls or [None])
            else:
                result.append(Slot(doc, None))
        return result

    def value(self, key: str, profile_values: dict[str, Any], shipment_values: dict[str, Any]) -> Any:
        source = profile_values if self.fields[key].scope == "profile" else shipment_values
        return source.get(key)

    def defaults(self, scope: str, today: date) -> dict[str, Any]:
        result = {}
        for field in self.fields.values():
            if field.scope == scope and field.default is not None:
                result[field.key] = today.isoformat() if field.default == "today" else field.default
        return result

    def field_statuses(self, profile_values: dict[str, Any], shipment_values: dict[str, Any]) -> dict[str, str]:
        return {
            key: field_status(field, self.value(key, profile_values, shipment_values))
            for key, field in self.fields.items()
        }

    def validate(self, values: dict[str, Any], scope: str) -> dict[str, str]:
        """Why each value can't be stored, keyed by field. Empty when all are fine."""
        errors: dict[str, str] = {}
        for key, value in values.items():
            field = self.fields.get(key)
            if field is None:
                errors[key] = "is not a field of the DO folder"
            elif field.scope != scope:
                errors[key] = "belongs to the fixed details" if field.scope == "profile" else "belongs to a shipment"
            elif (message := validate_value(field, value)) is not None:
                errors[key] = message
        return errors

    def to_api(self) -> dict[str, Any]:
        return {
            "stages": [{"id": stage_id, "name": name} for stage_id, name in STAGES],
            "folder_stage": FOLDER_STAGE,
            "profile_fields": [f.to_api() for f in self.profile_fields],
            "shipment_groups": [
                {"title": title, "fields": [f.to_api() for f in group]} for title, group in self.shipment_groups
            ],
            "documents": [d.to_api() for d in self.documents],
            "stamps": {key: stamp.name for key, stamp in self.stamps.items()},
        }


def split_list(value: object) -> list[str]:
    """Values from free text: one per line, or separated by commas."""
    if not value:
        return []
    return [re.sub(r"\s+", "", part).upper() for part in _LINES.split(str(value)) if part.strip()]


def is_filled(field: FieldDef, value: Any) -> bool:
    if field.type == "checkbox":
        return value is True
    if field.type == "containers":
        return bool(containers.split(value)) and not containers.problems(value)
    if field.type == "list":
        return bool(split_list(value))
    return value is not None and str(value).strip() != ""


def field_status(field: FieldDef, value: Any) -> str:
    if is_filled(field, value):
        return "ok"
    if field.type == "containers" and containers.split(value):
        return "invalid"
    return "optional" if field.optional else "missing"


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


def print_value(field: FieldDef, value: Any, date_format: str) -> str:
    """How a stored value is printed in a generated letter."""
    if value is None or value == "":
        return ""
    if field.type == "date":
        try:
            return date.fromisoformat(value).strftime(date_format)
        except (TypeError, ValueError):
            return str(value)
    if field.type == "checkbox":
        return "Yes" if value else "No"
    if field.type == "containers":
        return ", ".join(containers.split(value))
    if field.type == "list":
        return ", ".join(split_list(value))
    if field.code:
        return str(value).upper()
    return str(value)


def load_folder(path: Path) -> Folder:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise FolderError(f"Could not read {path}: {exc}") from exc

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
        field = FieldDef(
            key,
            label,
            scope,
            field_type,
            options,
            bool(spec.get("code", False)),
            bool(spec.get("optional", False)),
            spec.get("default"),
        )
        if field.default not in (None, "today") and (message := validate_value(field, field.default)):
            issues.append(f"{where}: default for '{key}' {message}")
        fields[key] = field
        return field

    profile_fields = tuple(
        field
        for i, spec in enumerate(raw.get("profile_fields") or [])
        if (field := make_field(spec, "profile", f"profile_fields[{i}]"))
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

    for required in ("mbl_no", "hbl_nos"):
        if required not in fields:
            issues.append(f"shipment_groups: the folder needs a '{required}' field to match documents")

    stamps: dict[str, StampDef] = {}
    for key, spec in (raw.get("stamps") or {}).items():
        spec = spec or {}
        if not spec.get("file"):
            issues.append(f"stamps.{key}: needs a file")
            continue
        stamps[str(key)] = StampDef(str(key), str(spec["file"]), str(spec.get("name") or key))

    documents = []
    doc_keys: set[str] = set()
    for di, spec in enumerate(raw.get("documents") or []):
        spec = spec or {}
        key = str(spec.get("key", "")).strip()
        title = str(spec.get("title", "")).strip()
        where = f"documents[{di}]" + (f" ({key})" if key else "")
        if not key or not title:
            issues.append(f"{where}: needs both a key and a title")
            continue
        if key in doc_keys:
            issues.append(f"{where}: document key '{key}' is used twice")
        doc_keys.add(key)
        source = str(spec.get("source", ""))
        if source not in SOURCES:
            issues.append(f"{where}: source must be fixed, upload or generate, not '{source}'")
            continue

        placement = None
        if stamp_spec := spec.get("stamp"):
            stamp_key = str(stamp_spec.get("stamp", ""))
            if stamp_key not in stamps:
                issues.append(f"{where}: stamp '{stamp_key}' is not listed under stamps")
            pages = str(stamp_spec.get("pages", "first"))
            if pages not in ("first", "all"):
                issues.append(f"{where}: stamp pages must be first or all")
            try:
                placement = Placement(
                    stamp_key,
                    pages,
                    float(stamp_spec.get("x", 0.5)),
                    float(stamp_spec.get("y", 0.5)),
                    float(stamp_spec.get("width", 0.18)),
                )
            except (TypeError, ValueError):
                issues.append(f"{where}: stamp x, y and width must be numbers")
            if placement and not all(0 < v < 1 for v in (placement.x, placement.y, placement.width)):
                issues.append(f"{where}: stamp x, y and width are fractions of the page, between 0 and 1")

        match = tuple(str(m) for m in spec.get("match") or ())
        for m in match:
            if m not in MATCH_KEYS:
                issues.append(f"{where}: can match on mbl_no, hbl or containers, not '{m}'")
        per_hbl = bool(spec.get("per_hbl", False))
        if "hbl" in match and not per_hbl:
            issues.append(f"{where}: matching on hbl needs per_hbl: true")

        uses = tuple(str(k) for k in spec.get("uses") or ())
        for k in uses:
            if k not in fields:
                issues.append(f"{where}: uses '{k}', which is not a field")
        if source == "generate" and not uses:
            issues.append(f"{where}: a generated document needs uses, the fields it prints")

        file = spec.get("file")
        pages_list: tuple[int, ...] = ()
        if source == "fixed":
            if not file:
                issues.append(f"{where}: a fixed document needs a file")
            try:
                pages_list = tuple(int(p) for p in spec.get("pages") or ())
            except (TypeError, ValueError):
                issues.append(f"{where}: pages must be page numbers, starting at 1")
            if any(p < 1 for p in pages_list):
                issues.append(f"{where}: pages start at 1")

        documents.append(
            DocDef(key, title, source, per_hbl, match, placement, str(file) if file else None, pages_list, uses)
        )

    if not documents:
        issues.append("documents: the folder needs at least one document")
    if issues:
        raise FolderError(f"{path} has problems:\n- " + "\n- ".join(issues))
    return Folder(
        profile_fields, tuple(groups), fields, stamps, tuple(documents), str(raw.get("date_format", "%d/%m/%Y"))
    )
