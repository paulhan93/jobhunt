"""Typst rendering for tailored resumes (step 7, PROJECT.md §8). Pure
formatting over an already-resolved document — no model calls, no judgment.
Single column, standard headings, real selectable text, no tables or
graphics, per PROJECT.md's stated preference (the "ATS auto-rejects on
formatting" idea is largely myth, but recruiters do run keyword searches
inside the ATS, which needs real text, not an image of text).
"""
import re
import subprocess
from pathlib import Path

import pypdf

# Compaction levels tried in order until the resume fits on one page — see
# render_resume(). Each step shrinks font/margins/line-spacing slightly;
# stops at the first level that fits, or L2 (still readable, not the
# tightest technically possible) if none do. No level goes below what's
# still a normal resume font size — this fixes overflow, it doesn't let a
# resume secretly become 13 bullets crammed at 7pt.
_STYLE_LEVELS = [
    {"font_pt": 10.0, "margin_x": "1.8cm", "margin_y": "1.6cm", "leading": "0.55em"},
    {"font_pt": 9.5, "margin_x": "1.6cm", "margin_y": "1.3cm", "leading": "0.5em"},
    {"font_pt": 9.0, "margin_x": "1.4cm", "margin_y": "1.1cm", "leading": "0.45em"},
]

# Typst markup's reserved characters (backslash handled separately, first,
# so it isn't double-escaped by this pass).
_SPECIAL = re.compile(r'[#*_`$<>@\[\]~^]')

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(text: str) -> str:
    """Escape Typst markup special characters in plain prose text. Resume
    content is free-text (job titles, bullet text) and routinely contains
    characters — '&', '#', '_' — that Typst would otherwise interpret as
    markup instead of literal text."""
    text = text.replace("\\", "\\\\")
    return _SPECIAL.sub(lambda m: "\\" + m.group(0), text)


def _format_date(ym: str) -> str:
    year, month = ym.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def _format_range(start: str, end: str | None) -> str:
    return f"{_format_date(start)} -- {'Present' if not end else _format_date(end)}"


def _contact_line(contact: dict) -> str:
    fields = [contact.get(k) for k in ("location", "email", "phone", "linkedin", "github")]
    return " | ".join(esc(f) for f in fields if f)


def _role_block(role: dict, with_location: bool) -> str:
    dates = _format_range(role["start"], role.get("end"))
    header = f'*{esc(role.get("company") or role.get("name"))}*'
    if with_location and role.get("location"):
        header += f' #h(1fr) {esc(role["location"])}'
    lines = [header + " \\"]
    if role.get("title"):
        lines.append(f'_{esc(role["title"])}_ #h(1fr) _{dates}_')
    else:
        lines.append(f'_{dates}_')
    lines += [f'- {esc(b["text"])}' for b in role["bullets"]]
    return "\n".join(lines)


def _education_line(edu: dict) -> str:
    parts = [f"{edu['degree']}, {edu['year']}"]
    if edu.get("gpa"):
        parts.append(f"GPA {edu['gpa']}")
    line = f"*{esc(edu['school'])}* --- " + esc(", ".join(parts))
    if edu.get("honors"):
        line += esc("; " + "; ".join(edu["honors"]))
    return line


def _typst_source(doc: dict, style: dict) -> str:
    contact = doc["contact"]

    parts = [
        f'#set page(margin: (x: {style["margin_x"]}, y: {style["margin_y"]}))',
        f'#set text(size: {style["font_pt"]}pt)',
        f'#set par(leading: {style["leading"]}, justify: false)',
        '#set heading(numbering: none)',
        '#show heading: it => [#v(0.3em) #text(size: 12pt, weight: "bold")[#it.body] #v(-0.2em) #line(length: 100%, stroke: 0.4pt)]',
        '',
        '#align(center)[',
        f'  #text(size: 18pt, weight: "bold")[{esc(contact["name"])}] \\',
        f'  {_contact_line(contact)}',
        ']',
        '',
    ]

    # Omitted for role families that don't need a pivot narrative — see
    # pipeline/tailor.py's wants_summary(). doc["summary"] is None, not an
    # empty string, when the family opted out.
    if doc["summary"]:
        parts.append("== Summary")
        parts.append(esc(doc["summary"]))

    if doc["experience"]:
        parts.append("\n== Experience\n")
        parts.append("\n\n".join(_role_block(r, with_location=True) for r in doc["experience"]))

    if doc["projects"]:
        parts.append("\n== Projects\n")
        parts.append("\n\n".join(_role_block(p, with_location=False) for p in doc["projects"]))

    if doc["skills"]:
        # List items, not plain lines: Typst merges consecutive plain-text
        # lines into a single paragraph (no visual break) unless each is its
        # own block element — a list marker forces that, a bare newline
        # doesn't. Confirmed by rendering: without this, all three skill
        # groups ran together on one line.
        parts.append("\n== Skills\n")
        parts.append("\n".join(
            f'- *{esc(s["label"])}:* {esc(", ".join(s["items"]))}' for s in doc["skills"]
        ))

    if doc["education"]:
        parts.append("\n== Education\n")
        parts.append("\n".join(f"- {_education_line(e)}" for e in doc["education"]))

    return "\n".join(parts) + "\n"


def _compile(typ_path: Path, pdf_path: Path, doc: dict, style: dict) -> int:
    """Writes the .typ source at this style level, compiles it, returns the
    resulting page count. Raises loudly on a Typst compile error — a resume
    that failed to render must not look like one that succeeded."""
    typ_path.write_text(_typst_source(doc, style))

    result = subprocess.run(
        ["typst", "compile", str(typ_path), str(pdf_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"typst compile failed:\n{result.stderr}")

    return len(pypdf.PdfReader(str(pdf_path)).pages)


def render_resume(doc: dict, out_dir: str, basename: str) -> tuple[str, int]:
    """Writes {basename}.typ and {basename}.pdf under out_dir. Returns
    (pdf_path, page_count) — nothing checked page count before this, so
    "compiled successfully" and "fits on one page" used to be silently
    treated as the same thing.

    If the baseline style overflows past one page, steps through
    _STYLE_LEVELS (smaller font, tighter margins/leading, still a normal
    readable resume) and recompiles until one fits, or until the levels run
    out — it does not drop content or re-call the model to pick fewer
    bullets (that's a judgment call for a human, not something to do
    silently). The caller is responsible for warning if page_count > 1 on
    return; this function doesn't raise for that case, since a rendered
    2-page PDF the human can look at and trim is more useful than no PDF."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    typ_path = out_path / f"{basename}.typ"
    pdf_path = out_path / f"{basename}.pdf"

    page_count = None
    for style in _STYLE_LEVELS:
        page_count = _compile(typ_path, pdf_path, doc, style)
        if page_count <= 1:
            break

    return str(pdf_path), page_count
