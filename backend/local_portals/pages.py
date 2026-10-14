"""Page layout shared by the local portals: plain server-rendered HTML, as the real
portals are. The portals listen only on this machine (make portals binds 127.0.0.1),
and the documents they hand out carry DEMO in their numbers (INV-DEMO-…)."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from html import escape

from fastapi.responses import HTMLResponse

SITE_NAMES = {
    "odex": "ODeX",
    "cma_rtgs": "CMA CGM RTGS payments",
    "control": "CMA CGM control panel",
    "home": "Local portals",
}

_STYLE = """
:root { --ink:#1f2328; --muted:#59636e; --line:#d1d9e0; --bg:#f6f8fa; --card:#fff; --accent:#0b5cad;
        --ok:#1a7f37; --bad:#cf222e; --wait:#9a6700; }
* { box-sizing:border-box; }
[hidden] { display:none !important; }
body { margin:0; font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; color:var(--ink);
       background:var(--bg); }
a { color:var(--accent); }
header.site { background:#2f363d; color:#fff; padding:8px 16px; display:flex; flex-wrap:wrap; align-items:center;
              gap:8px 20px; }
header.site .name { font-weight:600; font-size:16px; }
header.site .name small { font-weight:400; color:#c9d1d9; }
header.site .user { margin-left:auto; font-size:13px; }
header.site .user a { color:#fff; }
nav.menu { display:flex; flex-wrap:wrap; gap:2px; }
nav.menu .group { position:relative; }
nav.menu button { background:transparent; border:1px solid transparent; color:#fff; padding:6px 10px;
                  border-radius:6px; cursor:pointer; font:inherit; }
nav.menu button:hover, nav.menu button[aria-expanded="true"] { border-color:#6e7781; }
nav.menu .sub { position:absolute; top:100%; left:0; z-index:5; background:#fff; border:1px solid var(--line);
                border-radius:6px; padding:4px; min-width:190px; box-shadow:0 8px 24px rgba(0,0,0,.15); }
nav.menu .sub a { display:block; color:var(--ink); padding:6px 10px; border-radius:4px; text-decoration:none; }
nav.menu .sub a:hover { background:var(--bg); }
main { max-width:1080px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:16px; margin:24px 0 8px; }
.path { color:var(--muted); font-size:13px; margin:0 0 16px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:12px 0; }
form.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:12px 16px; }
fieldset { grid-column:1/-1; border:1px solid var(--line); border-radius:8px; padding:12px;
           display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:12px 16px; margin:0; }
legend { padding:0 6px; font-weight:600; font-size:13px; }
label.field { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--muted); }
label.field input, label.field select, td select { font:inherit; color:var(--ink); padding:6px 8px;
              border:1px solid var(--line); border-radius:6px; background:#fff; max-width:100%; }
.wide { grid-column:1/-1; }
.actions { grid-column:1/-1; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.btn { display:inline-block; font:inherit; padding:6px 14px; border-radius:6px; border:1px solid var(--line);
       background:#fff; color:var(--ink); cursor:pointer; text-decoration:none; }
.btn.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
.btn.small { padding:3px 10px; font-size:13px; }
.table-wrap { overflow-x:auto; border:1px solid var(--line); border-radius:8px; background:#fff; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
tr:last-child td { border-bottom:0; }
th { font-size:12px; color:var(--muted); font-weight:600; background:var(--bg); white-space:nowrap; }
td.num { text-align:right; white-space:nowrap; }
.status { font-weight:600; white-space:nowrap; }
.status.ok { color:var(--ok); } .status.bad { color:var(--bad); } .status.wait { color:var(--wait); }
.outcome { border-radius:8px; padding:12px 16px; margin:12px 0; border:1px solid; }
.outcome.accepted { background:#dafbe1; border-color:#4ac26b; }
.outcome.rejected { background:#ffebe9; border-color:#ff8182; }
.outcome ul { margin:6px 0 0; padding-left:20px; }
.hint { color:var(--muted); font-size:12px; }
.muted { color:var(--muted); }
.error { color:var(--bad); }
code { font-size:12px; background:var(--bg); padding:1px 4px; border-radius:4px; }
"""

# Opens and closes the drop-down menus, as on the real portals.
_MENU_SCRIPT = """
document.querySelectorAll('nav.menu button').forEach((button) => {
  button.addEventListener('click', () => {
    const sub = document.getElementById(button.id.replace('menu-', 'submenu-'));
    const opening = sub.hidden;
    document.querySelectorAll('nav.menu .sub').forEach((s) => { s.hidden = true; });
    document.querySelectorAll('nav.menu button').forEach((b) => b.setAttribute('aria-expanded', 'false'));
    sub.hidden = !opening;
    button.setAttribute('aria-expanded', String(opening));
  });
});
"""


def esc(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def page(
    title: str,
    body: str,
    *,
    site: str,
    menu: str = "",
    user: str = "",
    script: str = "",
    status_code: int = 200,
    body_attrs: str = "",
    style: str = "",
    header: bool = True,
) -> HTMLResponse:
    name = SITE_NAMES[site]
    scripts = (_MENU_SCRIPT if menu else "") + script
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(name)}</title><style>{_STYLE}{style}</style></head>
<body{f" {body_attrs}" if body_attrs else ""}>
{f'<header class="site"><span class="name">{esc(name)} <small>local</small></span>{menu}{user}</header>' if header else ""}
<main>{body}</main>
{f"<script>{scripts}</script>" if scripts else ""}
</body></html>"""
    return HTMLResponse(html, status_code=status_code)


def menu(groups: Sequence[tuple[str, str, Sequence[tuple[str, str, str]]]]) -> str:
    """A menu bar of drop-downs: (id, label, [(id, label, href), ...]) per group."""
    parts = []
    for group_id, label, links in groups:
        items = "".join(f'<a id="nav-{esc(i)}" href="{esc(href)}">{esc(text)}</a>' for i, text, href in links)
        parts.append(
            f'<div class="group"><button type="button" id="menu-{esc(group_id)}" aria-expanded="false">'
            f'{esc(label)} ▾</button><div class="sub" id="submenu-{esc(group_id)}" hidden>{items}</div></div>'
        )
    return f'<nav class="menu" id="menu">{"".join(parts)}</nav>'


def field(name: str, label: str, *, type: str = "text", value: str = "", hint: str = "", extra: str = "") -> str:
    hint_html = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return (
        f'<label class="field" id="field-{esc(name)}"><span>{esc(label)}</span>'
        f'<input id="{esc(name)}" name="{esc(name)}" type="{esc(type)}" value="{esc(value)}" '
        f'autocomplete="off" {extra}>{hint_html}</label>'
    )


def select(name: str, label: str, options: Iterable[str], *, chosen: str = "", hint: str = "") -> str:
    items = ['<option value="">Select</option>'] + [
        f'<option value="{esc(o)}"{" selected" if o == chosen else ""}>{esc(o)}</option>' for o in options
    ]
    hint_html = f'<span class="hint">{esc(hint)}</span>' if hint else ""
    return (
        f'<label class="field" id="field-{esc(name)}"><span>{esc(label)}</span>'
        f'<select id="{esc(name)}" name="{esc(name)}">{"".join(items)}</select>{hint_html}</label>'
    )


def table(table_id: str, headers: Sequence[str], rows: Sequence[str], empty: str) -> str:
    """``rows`` are ready-made <tr> elements."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join(rows) or f'<tr class="empty"><td colspan="{len(headers)}" class="muted">{esc(empty)}</td></tr>'
    return f'<div class="table-wrap"><table id="{esc(table_id)}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def status(text: str) -> str:
    tone = {"Rejected": "bad"}.get(text) or (
        "wait" if text in ("Submitted", "Pending Approval", "Received", "Pending Verification") else "ok"
    )
    return f'<span class="status {tone}">{esc(text)}</span>'


def outcome(accepted: bool, message: str, *, reference: str = "", problems: Sequence[str] = ()) -> str:
    """The answer to a form. SOPs read it: data-status is "accepted" or "rejected",
    data-reference the request number, data-message what went wrong."""
    state = "accepted" if accepted else "rejected"
    listed = "".join(f"<li>{esc(p)}</li>" for p in problems)
    said = message + (" " + " ".join(problems) if problems else "")
    return (
        f'<div id="outcome" class="outcome {state}" data-status="{state}" data-reference="{esc(reference)}" '
        f'data-message="{esc(said)}"><b>{esc(message)}</b>{f"<ul>{listed}</ul>" if listed else ""}</div>'
    )


def money(value: float) -> str:
    return f"{value:,.2f}"
