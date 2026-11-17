# PRD-22270 · Code review

Status: frozen @ d395a89

## Round 1 · 2026-11-17 · reviewed dev/PRD-22270 c2cabc2 (base c654f00)

Verdict: approved

### Findings
1. [suggestion] src/mspbots_agent_mcp/server.py:171-176 — the server instructions also shorten an unrelated Twilio phrase to stay under the 1500-char limit; harmless, but keep unrelated wording edits out of feature changes next time.
2. [suggestion] src/mspbots_agent_mcp/tools/schedule.py:100 — flag-off detection relies on the exact bare 404 message "Not found"; if the endpoint wording changes the agent falls back to the generic not_found envelope. Covered by TC5; revisit if PRD-20855 changes it.

Checked: credentials only from headers, caller ids from headers never from model args, optional headers never 401, tool name exactly schedule_resume, errors structured (422/403 passthrough, 429 limit_reached, flag-off not_available), README Connector section untouched, no Chinese in repo, tests re-run on the branch (101 passed, 8 skipped).

## Round 2 · 2026-11-17 · reviewed dev/PRD-22270 d395a89 (base 5608bd0)

Verdict: approved

### Findings
1. [suggestion] src/mspbots_agent_mcp/api_client.py:198 — `_error_text` is typed `-> Any` and returns `message` unchanged when `error` is absent; a non-string `message` would be formatted as-is. Harmless (existing behaviour), tighten later if needed.

Checked: rework r2 fixes the INT QA failure (flag-off 404 body `{"error":"Not found"}` now reaches schedule.py and maps to not_available; 422/429/403 text reaches the agent); `message` fallback and `errors` suffix preserved so other tools are unaffected; new tests run the real `_handle` (no stubbed AgentError); no credentials touched; no Chinese in repo; round 1 suggestion 2 is now effective; tests re-run on the branch (114 passed, 8 skipped). Changes after f67f5d8 are docs only.
