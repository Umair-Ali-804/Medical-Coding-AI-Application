"""Text normalization that preserves meaning and keeps offsets stable afterwards.

Cleaning happens once, before anything else; every downstream offset (sections,
entities, evidence) refers to the cleaned text stored on the document.
"""

from __future__ import annotations

import re
import unicodedata

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_MULTI_SPACE = re.compile(r"[ \t ]+")
_MULTI_BLANK = re.compile(r"\n{3,}")
_PAGE_FOOTER = re.compile(r"^\s*(page\s+\d+(\s+of\s+\d+)?)\s*$", re.IGNORECASE | re.MULTILINE)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_REPLACEMENTS = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "•": "-",
    "": "-",
    "·": "-",
}


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    text = _CTRL.sub("", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _PAGE_FOOTER.sub("", text)
    text = "\n".join(_MULTI_SPACE.sub(" ", line).strip() for line in text.split("\n"))
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()
