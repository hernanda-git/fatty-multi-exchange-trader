"""Pure Telegram HTML budgeting for internally generated, escaped cards."""

import re

_HTML_TOKEN = re.compile(r"<[^>]*>|&(?:[A-Za-z]+|#[0-9]+|#x[0-9A-Fa-f]+);|.", re.S)


def bounded_html(text: str, limit: int = 4000) -> str:
    """Truncate without splitting entities, tags or UTF-16; close retained tags.

    Supported markup is the application's generated b/i/code/pre subset, not
    arbitrary untrusted HTML. Counting markup too leaves margin below Telegram's
    4096-unit parsed-text limit.
    """
    parts: list[str] = []
    open_tags: list[str] = []
    units = 0
    reserved = 0
    for match in _HTML_TOKEN.finditer(text):
        token = match.group()
        opening = re.fullmatch(r"<(b|i|code|pre)>", token)
        closing_tag = re.fullmatch(r"</(b|i|code|pre)>", token)
        next_reserved = reserved
        if opening:
            next_reserved += len(f"</{opening[1]}>")
        elif closing_tag and open_tags and closing_tag[1] == open_tags[-1]:
            next_reserved -= len(token)
        width = sum(2 if ord(character) > 0xFFFF else 1 for character in token)
        if units + width + next_reserved > limit:
            break
        parts.append(token)
        units += width
        reserved = next_reserved
        if opening:
            open_tags.append(opening[1])
        elif closing_tag and open_tags and closing_tag[1] == open_tags[-1]:
            open_tags.pop()
    parts.extend(f"</{tag}>" for tag in reversed(open_tags))
    return "".join(parts)
