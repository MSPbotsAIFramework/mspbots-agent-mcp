# PRD-22238 · Code review

Status: frozen @ round-1

## Round 1 · 2026-11-16 · reviewed dev/PRD-22238 d3c8b6b (base 0380192)

Verdict: approved

### Findings
1. [suggestion] src/mspbots_agent_mcp/tools/agents.py:116 — `_save_ack` falls back to the agent_id argument when the platform omits `id`; fine, no change needed.
2. [suggestion] tests/test_tools.py — `test_tools_list_snapshot` fails on main too (get_connectors description > 500 chars, untouched by this ticket); fix separately.

Checked: the three save tools return only {success,id,updatedAt}; PUT bodies, error envelopes and success:false refusals are unchanged; tests cover all three tools (71 passed, 1 pre-existing failure); README tool table updated; no credentials, no shared state, no Chinese text in the repo.
