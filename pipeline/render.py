"""Typst rendering for tailored resumes (step 7, PROJECT.md §8). Pure
formatting over an already-resolved document — no model calls, no judgment.
Single column, standard headings, real selectable text, no tables or
graphics, per PROJECT.md's stated preference (the "ATS auto-rejects on
formatting" idea is largely myth, but recruiters do run keyword searches
inside the ATS, which needs real text, not an image of text).

Layout matches a reference resume Paul built by hand (2026-08-17): sans-serif
font, bold role title + right-aligned dates on one line, italic company |
location on the next, all-caps bold section labels with no rule under them.
"""
import re
import subprocess
from pathlib import Path

import pypdf

# Font is a plain sans-serif already installed on this Mac (`typst fonts`),
# not Typst's serif default — the default read as "too fancy" and harder to
# read at resume size (Paul's call, 2026-08-17).
#
# ligatures: false matters beyond looks: Typst's default font shaping forms
# "fi"/"fl"/"ffi" as single combined glyphs, and this is a well-documented
# TeX/Typst-family bug (not specific to this file) where macOS Preview's
# copy-paste doesn't correctly reverse-map ligature glyphs back to their
# component letters — "identified" or "efficient" would copy as garbled or
# missing text even though the PDF's underlying text layer is fully correct
# (confirmed with pypdf's extract_text() before this fix — the ToUnicode
# data was fine, it's specifically a copy/paste-time glyph problem).
# Disabling ligatures forces each letter to render as its own glyph, which
# sidesteps the whole bug class regardless of which viewer someone uses.
_FONT = "Helvetica Neue"

# Compaction levels tried in order until the resume fits on one page — see
# render_resume(). Each step shrinks font/margins/line-spacing slightly;
# stops at the first level that fits, or L2 (still readable, not the
# tightest technically possible) if none do. No level goes below what's
# still a normal resume font size — this fixes overflow, it doesn't let a
# resume secretly become 13 bullets crammed at 7pt.
_STYLE_LEVELS = [
    {"font_pt": 10.0, "margin_x": "1.8cm", "margin_y": "1.5cm", "leading": "0.5em"},
    {"font_pt": 9.5, "margin_x": "1.6cm", "margin_y": "1.2cm", "leading": "0.45em"},
    {"font_pt": 9.0, "margin_x": "1.4cm", "margin_y": "1.0cm", "leading": "0.4em"},
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


def _link(display: str, href: str) -> str:
    """A clickable link whose visible text is the plain display string
    (e.g. "linkedin.com/in/..."), not the full URL. resume.yaml stores
    linkedin/github as bare domain strings with no scheme, so this adds
    https:// for the target while keeping the readable text as-is."""
    url = href if href.startswith(("http://", "https://", "mailto:")) else f"https://{href}"
    return f'#link("{url}")[{esc(display)}]'


def _format_date(ym: str) -> str:
    year, month = ym.split("-")
    return f"{_MONTHS[int(month) - 1]} {year}"


def _format_range(start: str, end: str | None) -> str:
    return f"{_format_date(start)} -- {'Present' if not end else _format_date(end)}"


def _contact_lines(contact: dict) -> list[str]:
    """Two centered lines, matching the reference: location/phone/email on
    one, linkedin/github (both clickable) on the next."""
    line1 = [contact.get(k) for k in ("location", "phone")]
    line1 = [esc(f) for f in line1 if f]
    if contact.get("email"):
        line1.append(_link(contact["email"], f"mailto:{contact['email']}"))

    line2 = []
    if contact.get("linkedin"):
        line2.append(_link(contact["linkedin"], contact["linkedin"]))
    if contact.get("github"):
        line2.append(_link(contact["github"], contact["github"]))

    lines = [" | ".join(line1)]
    if line2:
        lines.append(" | ".join(line2))
    return lines


def _role_block(role: dict) -> str:
    """Bold title/name with dates right-aligned on the first line; italic
    "company | location" on the next if this is an experience entry
    (projects have no company/location, so they go straight to bullets)."""
    dates = _format_range(role["start"], role.get("end"))
    title = role.get("title") or role.get("name")
    lines = [f'*{esc(title)}* #h(1fr) {esc(dates)} \\']

    if role.get("company"):
        subtitle = role["company"]
        if role.get("location"):
            subtitle += f' | {role["location"]}'
        lines.append(f'_{esc(subtitle)}_')

    lines += [f'- {esc(b["text"])}' for b in role["bullets"]]
    return "\n".join(lines)


def _education_block(edu: dict) -> str:
    """Same bold-header-plus-italic-subtitle shape as _role_block, so
    Education reads consistently with Experience/Projects."""
    lines = [f'*{esc(edu["school"])}* #h(1fr) {esc(str(edu["year"]))} \\']
    parts = [edu["degree"]]
    if edu.get("gpa"):
        parts.append(f"GPA {edu['gpa']}")
    subtitle = ", ".join(parts)
    if edu.get("honors"):
        subtitle += "; " + "; ".join(edu["honors"])
    lines.append(f'_{esc(subtitle)}_')
    return "\n".join(lines)


def _typst_source(doc: dict, style: dict) -> str:
    contact = doc["contact"]

    parts = [
        f'#set page(margin: (x: {style["margin_x"]}, y: {style["margin_y"]}))',
        f'#set text(font: "{_FONT}", size: {style["font_pt"]}pt, ligatures: false)',
        f'#set par(leading: {style["leading"]}, justify: false)',
        '#set heading(numbering: none)',
        # All-caps bold label, no rule underneath — matches the reference
        # resume, and is more compact than the earlier underlined version.
        '#show heading: it => [#v(0.55em) #text(weight: "bold")[#upper(it.body)] #v(0.2em)]',
        '',
        '#align(center)[',
        f'  #text(size: 20pt, weight: "bold")[{esc(contact["name"])}] \\',
        '  ' + ' \\\n  '.join(_contact_lines(contact)),
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
        parts.append("\n\n".join(_role_block(r) for r in doc["experience"]))

    if doc["projects"]:
        parts.append("\n== Projects\n")
        parts.append("\n\n".join(_role_block(p) for p in doc["projects"]))

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
        parts.append("\n\n".join(_education_block(e) for e in doc["education"]))

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
