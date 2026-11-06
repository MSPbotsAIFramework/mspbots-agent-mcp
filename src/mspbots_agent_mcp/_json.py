"""Token-economical JSON serialization for tool return values.

Compact (no indent) and non-ASCII-preserving. Deliberately **not** size-capped.

There used to be a 20,000-char cap here: it trimmed the largest list field
when it could, but a payload with no list to trim fell through to a branch
that threw the whole result away and returned a "too large, narrow your
query" notice instead — so a 26,085-char answer reached the caller as
nothing at all, with no way to narrow anything. Returning the payload in
full loses strictly less: MCP clients apply their own limit, and theirs
keeps the beginning of the data instead of discarding all of it.

A tool that returns a big list should still trim it itself and say so with
an explicit marker, so a model can tell it saw a partial list — see
`tools/usage.py`. That is the tool's job now, not this module's.
"""

import json
from typing import Any


def _compact(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def dump_json(data: Any) -> str:
    """Serialize data compactly and in full."""
    return _compact(data)


def error_envelope(code: str, message: str, retryable: bool) -> str:
    return _compact({"error": {"code": code, "message": message, "retryable": retryable}})
