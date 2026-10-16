"""
Is a PDF page's text layer actually readable?

Some municipal PDFs have a text layer that extracts as garbage: CID
placeholders "(cid:42)", replacement chars, private-use glyphs, or fonts
remapped within ASCII ("Tkf Bpspvhi"). Those pages need OCR even though
get_text() returns plenty of characters.
"""
from __future__ import annotations

import re

_CID = re.compile(r"\(cid:\d+\)")
_WORD = re.compile(r"[A-Za-z]{3,}")
_VOWELS = set("aeiouyAEIOUY")


def text_is_readable(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    n = len(s)
    if len(_CID.findall(s)) * 8 / n > 0.03:
        return False
    bad = sum(1 for c in s if c == "�" or "" <= c <= ""
              or (ord(c) < 32 and c not in "\n\r\t"))
    if bad / n > 0.02:
        return False
    # Letters, digits, whitespace and common punctuation (incl. §, —, “ ”).
    readable = sum(1 for c in s if c.isalnum() or c.isspace() or c in ".,;:!?'\"()-/$%&#@*+=[]_§—–“”‘’…") / n
    if readable < 0.85:
        return False
    # Fonts remapped within ASCII: words exist but few contain vowels.
    words = _WORD.findall(s)
    if len(words) >= 40:
        with_vowel = sum(1 for w in words if any(ch in _VOWELS for ch in w))
        letters = "".join(words)
        vowel_share = sum(1 for ch in letters if ch in _VOWELS) / len(letters)
        if with_vowel / len(words) < 0.5 and vowel_share < 0.17:
            return False
    return True
