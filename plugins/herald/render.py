"""Turning a letter into mail — the Obsidian Archive identity, in an inbox.

A letter is a plain dict (``title`` · ``dateline`` · ``lede`` · ``sections`` · ``footer``); this
module is the only thing that knows how one looks. Both renderings come from the same structure,
so the plain-text part is never an afterthought that drifts from the HTML.

Email is not the web and the design has to concede two things:

* **Inline styles only.** Gmail strips ``<style>`` blocks and every ``class``. The CSS custom
  properties the SPA is built on cannot survive the trip, so the palette is repeated literally
  here — the one place in the repo where duplicating the tokens is correct.
* **Tables, not flexbox.** Enough clients still run layout engines that predate it.

The dotted ledger leaders that carry the UI's card catalog look are the one flourish that
survives intact: a ``border-bottom: 1px dotted`` on a full-width row reads the same everywhere.
"""
from __future__ import annotations

from html import escape
from typing import Any

# The tokens from interfaces/spa/src/styles/tokens.css, hard-coded because mail cannot
# reference them. Dark-only on purpose: a mail client's own theme cannot be queried, and the
# obsidian base is what makes a briefing recognizably Orion at a glance in a crowded inbox.
INK = "#e8e6dc"
MUTED = "#9d9b8d"
BG = "#0e100e"
CARD = "#121512"
LINE = "#242a24"
COPPER = "#d0925f"
FACT = "#85bb9c"
IDEA = "#d6b360"

_SERIF = "Iowan Old Style, Palatino, 'Palatino Linotype', Georgia, serif"
_SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def _row(label: str, value: Any, accent: str = INK) -> str:
    """One ledger line: label, dotted leader, value — the card catalog's signature."""
    return (
        f'<tr><td style="padding:7px 0;border-bottom:1px dotted {LINE};font:14px {_SANS};'
        f'color:{MUTED};">{escape(str(label))}</td>'
        f'<td style="padding:7px 0;border-bottom:1px dotted {LINE};font:14px {_MONO};'
        f'color:{accent};text-align:right;white-space:nowrap;">{escape(str(value))}</td></tr>')


def _section_html(section: dict[str, Any]) -> str:
    heading = escape(section.get("heading", ""))
    parts = [
        f'<h2 style="margin:30px 0 10px;font:600 12px {_SANS};letter-spacing:.14em;'
        f'text-transform:uppercase;color:{COPPER};">{heading}</h2>'
    ]
    if section.get("blurb"):
        parts.append(f'<p style="margin:0 0 12px;font:15px/1.6 {_SERIF};color:{MUTED};">'
                     f'{escape(section["blurb"])}</p>')
    if section.get("rows"):
        cells = "".join(_row(label, value, section.get("accent", INK))
                        for label, value in section["rows"])
        parts.append(f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                     f'style="border-collapse:collapse;">{cells}</table>')
    if section.get("bullets"):
        items = "".join(
            f'<li style="margin:0 0 7px;font:15px/1.55 {_SERIF};color:{INK};">{escape(b)}</li>'
            for b in section["bullets"])
        parts.append(f'<ul style="margin:10px 0 0;padding-left:18px;">{items}</ul>')
    if section.get("note"):
        parts.append(f'<p style="margin:12px 0 0;font:13px/1.55 {_SANS};color:{MUTED};">'
                     f'{escape(section["note"])}</p>')
    return "".join(parts)


def html(letter: dict[str, Any]) -> str:
    """The full HTML part. Self-contained, inline-styled, no remote assets."""
    sections = "".join(_section_html(s) for s in letter.get("sections", []))
    lede = ""
    if letter.get("lede"):
        # The model's prose. Blank lines become paragraphs; nothing else is interpreted, so a
        # stray angle bracket from a note title cannot inject markup into the mail.
        paras = "".join(
            f'<p style="margin:0 0 12px;font:16px/1.65 {_SERIF};color:{INK};">{escape(p.strip())}</p>'
            for p in str(letter["lede"]).split("\n\n") if p.strip())
        lede = (f'<div style="margin:0 0 6px;padding:0 0 4px;border-left:2px solid {COPPER};'
                f'padding-left:16px;">{paras}</div>')

    footer = escape(letter.get("footer", ""))
    return f"""\
<div style="margin:0;padding:24px 12px;background:{BG};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="border-collapse:collapse;background:{BG};">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="border-collapse:collapse;width:600px;max-width:100%;background:{CARD};
                    border:1px solid {LINE};border-top:3px solid {COPPER};">
        <tr><td style="padding:30px 34px 34px;">
          <p style="margin:0 0 4px;font:600 11px {_SANS};letter-spacing:.2em;
                    text-transform:uppercase;color:{COPPER};">{escape(letter.get("eyebrow", "Orion"))}</p>
          <h1 style="margin:0 0 4px;font:400 27px/1.2 {_SERIF};color:{INK};">
            {escape(letter.get("title", ""))}</h1>
          <p style="margin:0 0 22px;font:13px {_SANS};color:{MUTED};">
            {escape(letter.get("dateline", ""))}</p>
          {lede}
          {sections}
          <p style="margin:32px 0 0;padding-top:14px;border-top:1px solid {LINE};
                    font:12px/1.6 {_SANS};color:{MUTED};">{footer}</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</div>"""


def text(letter: dict[str, Any]) -> str:
    """The plain-text part — the same letter, readable in a terminal mail client."""
    out: list[str] = [letter.get("title", ""), "=" * len(letter.get("title", "")),
                      letter.get("dateline", ""), ""]
    if letter.get("lede"):
        out += [str(letter["lede"]).strip(), ""]
    for section in letter.get("sections", []):
        out.append(section.get("heading", "").upper())
        if section.get("blurb"):
            out.append(section["blurb"])
        width = max((len(str(k)) for k, _ in section.get("rows", [])), default=0)
        for label, value in section.get("rows", []):
            out.append(f"  {str(label).ljust(width)} .... {value}")
        for bullet in section.get("bullets", []):
            out.append(f"  - {bullet}")
        if section.get("note"):
            out.append(f"  {section['note']}")
        out.append("")
    out += ["--", letter.get("footer", "")]
    return "\n".join(out)
