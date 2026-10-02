"""Conservative source-text stand-down guards shared by entry parsers."""

import re

_STAND_DOWN = re.compile(
    r"(?<![$#])\b(?:wait|waiting|hold|cancelled|canceled|cancel|invalidated)\b", re.I
)
_NEGATED_ENTRY = re.compile(
    (
        "(?<![$#])\\b(?:do\\s+not|don['’]?t|never|not|no)\\s+(?:enter(?:ing)?|entr"
        "y|buy(?:ing)?|sell(?:ing)?|long(?:ing)?|short(?:ing)?)\\b"
    ),
    re.I,
)


def entry_stands_down(text: str) -> bool:
    return bool(_STAND_DOWN.search(text) or _NEGATED_ENTRY.search(text))
