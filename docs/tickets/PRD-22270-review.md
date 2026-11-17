# PRD-22270 · Code review

Status: frozen @ c2cabc2

## Round 1 · 2026-11-17 · reviewed dev/PRD-22270 c2cabc2 (base c654f00)

Verdict: approved

### Findings
1. [suggestion] src/mspbots_agent_mcp/server.py:171-176 — the server instructions also shorten an unrelated Twilio phrase to stay under the 1500-char limit; harmless, but keep unrelated wording edits out of feature changes next time.
2. [suggestion] src/mspbots_agent_mcp/tools/schedule.py:100 — flag-off detection relies on the exact bare 404 message "Not found"; if the endpoint wording changes the agent falls back to the generic not_found envelope. Covered by TC5; revisit if PRD-20855 changes it.

Checked: credentials only from headers, caller ids from headers never from model args, optional headers never 401, tool name exactly schedule_resume, errors structured (422/403 passthrough, 429 limit_reached, flag-off not_available), README Connector section untouched, no Chinese in repo, tests re-run on the branch (101 passed, 8 skipped).
