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


def _typst_source(doc: dict) -> str:
    contact = doc["contact"]

    parts = [
        '#set page(margin: (x: 1.8cm, y: 1.6cm))',
        '#set text(size: 10pt)',
        '#set par(leading: 0.55em, justify: false)',
        '#set heading(numbering: none)',
        '#show heading: it => [#v(0.3em) #text(size: 12pt, weight: "bold")[#it.body] #v(-0.2em) #line(length: 100%, stroke: 0.4pt)]',
        '',
        '#align(center)[',
        f'  #text(size: 18pt, weight: "bold")[{esc(contact["name"])}] \\',
        f'  {_contact_line(contact)}',
        ']',
        '',
        '== Summary',
        esc(doc["summary"]),
    ]

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


def render_resume(doc: dict, out_dir: str, basename: str) -> str:
    """Writes {basename}.typ and {basename}.pdf under out_dir, returns the
    PDF path. Raises loudly on a Typst compile error rather than returning a
    partial/missing PDF silently — a resume that failed to render must not
    look like one that succeeded."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    typ_path = out_path / f"{basename}.typ"
    pdf_path = out_path / f"{basename}.pdf"
    typ_path.write_text(_typst_source(doc))

    result = subprocess.run(
        ["typst", "compile", str(typ_path), str(pdf_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"typst compile failed:\n{result.stderr}")

    return str(pdf_path)
