"""Compares the placeholders in the Word template with the fields in do_set.yaml.

    python scripts/check_template.py [path/to/template.docx]
"""
from __future__ import annotations

import sys
from pathlib import Path

from docxtpl import DocxTemplate

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.do_set import load_do_set  # noqa: E402
from app.documents import extra_keys  # noqa: E402


def main() -> int:
    template = Path(sys.argv[1]) if len(sys.argv) > 1 else BACKEND_DIR / "templates" / "do_set.docx"
    do_set = load_do_set(BACKEND_DIR / "do_set.yaml")
    used = set(DocxTemplate(str(template)).get_undeclared_template_variables())
    unknown = sorted(used - set(do_set.fields) - extra_keys(do_set))
    unused = sorted(set(do_set.fields) - used)

    print(f"{template.name}: {len(used)} placeholders")
    if unknown:
        print("\nPlaceholders with no matching field. These come out blank:")
        for name in unknown:
            print(f"  {{{{ {name} }}}}")
    if unused:
        print("\nFields the template never uses:")
        for name in unused:
            print(f"  {name}  ({do_set.fields[name].label})")
    if not unknown and not unused:
        print("Every placeholder matches a field, and every field is used.")
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
