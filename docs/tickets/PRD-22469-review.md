Status: frozen @ cf03436

# PRD-22469 · Code review

## Round 1 · 2026-11-25 · reviewed dev/PRD-22469 cf03436 (base main 3b13393)

Verdict: approved

### Findings
1. [suggestion] src/mspbots_agent_mcp/server.py:177 — the instructions cap in tests/test_tools.py was raised to 3200 to fit the rule; acceptable and documented in the test comment.
2. [suggestion] src/mspbots_agent_mcp/final_reply_rule.py:57 — `append_to_doc` mutates the docstring through a decorator; fine for one use, revisit if more tools need it.

Checks: rule text equals the ticket text (pinned literal in tests); one source feeds the instructions and the four sentences (all at most 200 chars); only upload_agent and update_agent changed in tools/list; MCP stores exactly the text sent (TC8); README edits are in the tool tables, outside the Connector section; no Chinese in the diff; `uv run pytest` re-run on the branch: 134 passed, 8 skipped.
