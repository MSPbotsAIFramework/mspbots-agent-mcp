"""The "output for people" rule and the sentences that point at it (PRD-22469).

One source for the server instructions and the upload/update agent tool text, so
they cannot drift apart. Wording only: the server never inserts the rule into a
prompt and never rejects a prompt that lacks it.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

FINAL_REPLY_HEADING = "How to write your final reply"

FINAL_REPLY_RULE = "\n".join(
    [
        "Write your final reply for a person, not for a program.",
        "Start with one plain sentence: what you did and the result. "
        'Example: "Read 10 open Zoho Desk tickets; 3 are unassigned."',
        'Say what you changed in any system: what, where and how many. Or say "No changes made."',
        "If something needs a person, say who, what to do and by when, on its own line.",
        "Lists of 3 or more items with shared fields go in a markdown table. "
        "Use at most 6 columns, short headers, and one row per item.",
        "Never show null, undefined, NaN, empty fields, internal flags (has_more, status_code, "
        'ids nobody asked for), raw JSON or code. Write "none", "unassigned" or "—" instead, '
        "or leave the field out.",
        'Use the names people use: "ticket #109", "Contoso Dental", "Micus Zhang". '
        "Never tool names (zohodesk_list_tickets), field keys or GUIDs.",
        "Keep it short. Give the summary and what matters; say how many more items there are "
        'instead of pasting everything ("…and 25 more; ask if you want the full list").',
        "Raw data goes in only when the person asked for it. Then put it in one fenced block "
        'at the end, under "Raw data (requested)".',
    ]
)

BUILDER_PARAGRAPH = (
    "When you write or replace an agent's instructions, end them with a section headed "
    f'"{FINAL_REPLY_HEADING}" and copy the rule below into it word for word, as the last '
    f"section.\n\n{FINAL_REPLY_RULE}"
)

UPLOAD_DESCRIPTION_SENTENCE = (
    f'The agent\'s instructions must end with the "{FINAL_REPLY_HEADING}" section '
    "from the server instructions."
)
PROMPT_PARAM_SENTENCE = (
    f'Must end with the "{FINAL_REPLY_HEADING}" section from the server instructions.'
)
UPDATE_DESCRIPTION_SENTENCE = (
    f'When replacing instructions, keep the "{FINAL_REPLY_HEADING}" section from the '
    "server instructions as the last section."
)
INSTRUCTIONS_PARAM_SENTENCE = (
    f'Must end with the "{FINAL_REPLY_HEADING}" section from the server instructions; '
    "keep it when replacing."
)


def append_to_doc(sentence: str) -> Callable[[Any], Any]:
    """Decorator: add `sentence` as the last paragraph of a tool's docstring."""

    def decorator(fn: Any) -> Any:
        fn.__doc__ = f"{inspect.cleandoc(fn.__doc__ or '')}\n\n{sentence}"
        return fn

    return decorator
