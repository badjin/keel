from __future__ import annotations

import re


START_RE = re.compile(r"^<!-- keel:workflow:start [0-9.]+ -->$", re.M)
END = "<!-- keel:workflow:end -->"
_END_RE = re.compile(rf"^{re.escape(END)}$", re.M)


class BlockError(ValueError):
    pass


def _bounds(text: str) -> tuple[int, int] | None:
    starts = list(START_RE.finditer(text))
    ends = list(_END_RE.finditer(text))
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].end():
        raise BlockError("Malformed Keel workflow rules block")
    return starts[0].start(), ends[0].end()


def insert(text: str, body: str, version: str) -> tuple[str, bool]:
    bounds = _bounds(text)
    block = f"<!-- keel:workflow:start {version} -->\n{body.rstrip()}\n{END}"
    if bounds is not None:
        start, end = bounds
        return text[:start] + block + text[end:], False
    added_newline = bool(text) and not text.endswith("\n")
    return text + ("\n" if added_newline else "") + ("\n" if text else "") + block + "\n", added_newline


def remove(text: str, added_newline: bool = False) -> str:
    bounds = _bounds(text)
    if bounds is None:
        return text
    start, end = bounds
    if text[end:end + 1] == "\n":
        end += 1
    prefix = text[:start]
    if prefix.endswith("\n\n"):
        prefix = prefix[:-1]
    if added_newline and not text[end:] and prefix.endswith("\n"):
        prefix = prefix[:-1]
    return prefix + text[end:]
