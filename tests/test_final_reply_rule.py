"""PRD-22469: the "output for people" rule reaches the builder, and nothing else changes."""

import json

import pytest
from mcp.server.fastmcp import FastMCP

from mspbots_agent_mcp.config import Settings
from mspbots_agent_mcp.final_reply_rule import (
    BUILDER_PARAGRAPH,
    FINAL_REPLY_HEADING,
    FINAL_REPLY_RULE,
    INSTRUCTIONS_PARAM_SENTENCE,
    PROMPT_PARAM_SENTENCE,
    UPDATE_DESCRIPTION_SENTENCE,
    UPLOAD_DESCRIPTION_SENTENCE,
)
from mspbots_agent_mcp.server import create_mcp_server
from mspbots_agent_mcp.tools import agents

# Pinned literal copy of the rule as the ticket gives it (guards the constant against edits).
CARD_RULE = """Write your final reply for a person, not for a program.
Start with one plain sentence: what you did and the result. Example: "Read 10 open Zoho Desk tickets; 3 are unassigned."
Say what you changed in any system: what, where and how many. Or say "No changes made."
If something needs a person, say who, what to do and by when, on its own line.
Lists of 3 or more items with shared fields go in a markdown table. Use at most 6 columns, short headers, and one row per item.
Never show null, undefined, NaN, empty fields, internal flags (has_more, status_code, ids nobody asked for), raw JSON or code. Write "none", "unassigned" or "—" instead, or leave the field out.
Use the names people use: "ticket #109", "Contoso Dental", "Micus Zhang". Never tool names (zohodesk_list_tickets), field keys or GUIDs.
Keep it short. Give the summary and what matters; say how many more items there are instead of pasting everything ("…and 25 more; ask if you want the full list").
Raw data goes in only when the person asked for it. Then put it in one fenced block at the end, under "Raw data (requested)"."""

SENTENCES = [
    UPLOAD_DESCRIPTION_SENTENCE,
    PROMPT_PARAM_SENTENCE,
    UPDATE_DESCRIPTION_SENTENCE,
    INSTRUCTIONS_PARAM_SENTENCE,
]


def test_rule_is_the_card_text_and_has_eight_points_after_the_lead_line():
    assert FINAL_REPLY_RULE == CARD_RULE
    assert len(FINAL_REPLY_RULE.splitlines()) == 9


# TC1
def test_server_instructions_end_with_builder_paragraph_and_rule():
    instructions = create_mcp_server(Settings()).instructions
    assert instructions.endswith(BUILDER_PARAGRAPH)
    assert CARD_RULE in instructions
    assert "word for word" in instructions and FINAL_REPLY_HEADING in instructions


# TC2
@pytest.mark.asyncio
async def test_only_the_two_agent_tools_carry_the_pointer():
    tools = {t.name: t for t in await create_mcp_server(Settings()).list_tools()}
    for sentence in SENTENCES:
        assert len(sentence) <= 200
        assert FINAL_REPLY_HEADING in sentence

    upload = tools["mspbotsagent_upload_agent"]
    update = tools["mspbotsagent_update_agent"]
    assert upload.description.endswith(UPLOAD_DESCRIPTION_SENTENCE)
    assert update.description.endswith(UPDATE_DESCRIPTION_SENTENCE)
    assert PROMPT_PARAM_SENTENCE in upload.inputSchema["properties"]["prompt"]["description"]
    assert INSTRUCTIONS_PARAM_SENTENCE in update.inputSchema["properties"]["instructions"]["description"]

    for name, tool in tools.items():
        if name in ("mspbotsagent_upload_agent", "mspbotsagent_update_agent"):
            continue
        blob = json.dumps(tool.inputSchema) + (tool.description or "")
        assert FINAL_REPLY_HEADING not in blob, name


class _Client:
    def __init__(self):
        self.posts = []
        self.puts = []

    async def post(self, path, json_body=None, *, read_timeout=None, retries=None):
        self.posts.append((path, json_body))
        return {"success": True, "data": {"id": "a1"}}

    async def put(self, path, body):
        self.puts.append((path, body))
        return {"data": {"id": "a1", **body}}


# TC8
@pytest.mark.asyncio
async def test_prompt_without_the_section_is_stored_exactly_as_sent():
    client = _Client()
    mcp = FastMCP("t")
    agents.register(mcp, lambda: client)
    text = "Be helpful."
    await mcp.call_tool("mspbotsagent_upload_agent", {"name": "n", "model": "m", "prompt": text})
    await mcp.call_tool("mspbotsagent_update_agent", {"agent_id": "a1", "instructions": text})
    assert client.posts[0][1]["prompt"] == text
    assert client.puts[0][1]["prompt"] == text
