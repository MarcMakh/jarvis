"""Small stateful filters for token streams.

Model output arrives in arbitrary slices, so anything that spans characters —
a `<think>` block, a `[[do:…]]` directive — can be cut in half between two
chunks. These filters buffer just enough to decide, and never hold back text
they don't need to.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any


class ThinkSplitter:
    """Separates inline reasoning (`<think>…</think>`) from the answer.

    Reasoning models served without a separate reasoning channel put their
    scratch work in the content stream. feed() returns [(kind, text), …] with
    kind "reasoning" or "content"; the tags themselves are dropped."""

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._in = False
        self._started = False   # leading whitespace before an opening tag is dropped

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._buf += chunk
        while self._buf:
            tag = self._CLOSE if self._in else self._OPEN
            i = self._buf.find(tag)
            if i >= 0:
                text = self._buf[:i]
                self._buf = self._buf[i + len(tag):]
                self._emit(out, text)
                self._in = not self._in
                if not self._in:
                    self._buf = self._buf.lstrip("\n")
                continue
            # No full tag: release everything except a tail that could still
            # turn into one (e.g. buffer ends with "<thi").
            keep = 0
            for n in range(min(len(tag) - 1, len(self._buf)), 0, -1):
                if tag.startswith(self._buf[-n:]):
                    keep = n
                    break
            text = self._buf[: len(self._buf) - keep]
            self._buf = self._buf[len(self._buf) - keep:]
            self._emit(out, text)
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._emit(out, self._buf)
        self._buf = ""
        return out

    def _emit(self, out: list[tuple[str, str]], text: str) -> None:
        if not text:
            return
        if not self._in and not self._started:
            text = text.lstrip()
            if not text:
                return
            self._started = True
        out.append(("reasoning" if self._in else "content", text))


_DIRECTIVE = re.compile(r"\[\[do:\s*([a-z][a-z-]*)\s*([^\]]*)\]\]", re.I)


class DirectiveFilter:
    """Lifts `[[do:action arg]]` directives out of the brain's reply.

    feed() returns the text that is safe to show; completed directives collect
    in `.found` as (name, arg). A lone "[" is held back only until the next
    characters prove it isn't the start of a directive."""

    _START = "[[do:"

    def __init__(self) -> None:
        self._buf = ""
        self.found: list[tuple[str, str]] = []

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out = ""
        while self._buf:
            i = self._buf.find("[[")
            if i < 0:
                # hold a trailing "[" — it may be the first half of "[["
                keep = 1 if self._buf.endswith("[") else 0
                out += self._buf[: len(self._buf) - keep]
                self._buf = self._buf[len(self._buf) - keep:]
                break
            out += self._buf[:i]
            rest = self._buf[i:]
            if len(rest) < len(self._START):
                if self._START.startswith(rest.lower()):
                    self._buf = rest          # could still become a directive
                    break
                out += rest[:2]
                self._buf = rest[2:]
                continue
            if not rest.lower().startswith(self._START):
                out += rest[:2]
                self._buf = rest[2:]
                continue
            end = rest.find("]]")
            if end < 0:
                if len(rest) > 400:           # never closes: it was just text
                    out += rest
                    self._buf = ""
                else:
                    self._buf = rest
                break
            m = _DIRECTIVE.fullmatch(rest[: end + 2])
            if m:
                self.found.append((m.group(1).lower(), m.group(2).strip()))
            else:
                out += rest[: end + 2]
            self._buf = rest[end + 2:]
        return out

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return out


def iter_sse_json(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Decode a `data: {json}` server-sent-event stream (OpenAI-style and
    Hermes' own). Stops at `data: [DONE]`; ignores comments and bad lines."""
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj
