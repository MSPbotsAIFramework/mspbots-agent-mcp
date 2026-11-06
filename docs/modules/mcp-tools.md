# Module: MCP tool layer (`src/mspbots_agent_mcp/tools/`)

Accumulated facts. Each entry carries `path:line` plus the commit sha at the time of reading. **When reading, verify that the line still exists and is still correct**;
if it does not match, treat it as "not covered" and call that out in your report. Do not copy it blindly.

## Four traps when adding a new tool (notes after implementing PRD-18216, @ e45ded6)

1. **Tool output no longer has a generic cap and is no longer truncated.**
   Old behavior: `_json.MAX_CHARS = 20_000`, and `dump_json_capped` truncated the largest list field when over the limit; a payload with **no list
   to truncate** fell into the fallback branch, where the whole result was thrown away and only
   `{"truncated":true,"note":"Result too large (26085 chars) ... narrow your query."}` was returned.
   The caller got **zero data**, and many tools have no parameter to narrow with. Removed (2026-09-08):
   `_json.py` now only has `_compact` / `dump_json` (formerly `dump_json_capped`, 33 call sites renamed accordingly) /
   `error_envelope`, with **no MAX_CHARS and no truncation of any kind**. The remaining safety net is the MCP client's own
   limit, which **keeps the beginning** rather than discarding everything.
   **The corollary still holds: tools that return large lists must still truncate themselves and mark it explicitly**, only now it is so that the model can read the output,
   not to get past a generic capper. Measured magnitude (2026-09-03, real tenant): the usage overview's `breakdownTotal`
   is **453**, and with `pageSize=1000` the full output is far over 20000 characters. See `_shape_overview` in `tools/usage.py` for the approach:
   binary-search for the largest number of rows that fits, with a budget of its **own** `_BREAKDOWN_BUDGET = 20_000` (no longer the shared
   `MAX_CHARS`), **measured with the same serializer** (hence it imports `_json._compact`).
   This is currently the **only** place in the whole repo where output is limited, and only `breakdown` is limited.

2. **Tool descriptions have hard assertions; do not bypass them with the exemption table.**
   `tests/test_tools.py:126` asserts <= **500** characters, `:128` asserts the first line <= **100** characters,
   and `:111-112` asserts that `API:` / `GET /` / `POST /` must not appear (to prevent leaking implementation details).
   The exemption table `_LONG_DESCRIPTION_EXCEPTIONS` now has **7 entries** (the original 6 historical tools +
   `mspbotsagent_get_sop_data_sources`, added 2026-09-08: it has to spell out the four values of `connection`,
   including that `"unavailable"` does not mean disconnected, and that agent-first resolution means the tenant's copy can only be seen via `tenantConnected`;
   both are pitfalls that cause false reports; with the full field table (including `tools` / `enabled` / `managed` etc.,
   supplied by Product) the description is 2009 characters).
   **Still do not add new tools to it**: that table is debt, not permission; write down the reason next to it before adding anything.
   The first PRD-18216 docstring was 557 characters and only passed after being squeezed to 483.

3. **Three things `api_client` already does uniformly; do not redo them in tools.**
   Mount prefix: `_APP_PREFIX = "/apps/mb-platform-agent"` at `api_client.py:13`, joined into `_base_url` at `:83` -
   tools only write the latter half of `/api/...`.
   Per-caller credential forwarding: `:87` sends `Authorization: Bearer <caller token>`, `:88` sends `X_Tenant_ID`.
   Dropping unset parameters: `_clean_params` at `:93-96` drops any entry whose value is `None`.
   **Corollary**: always declare optional parameters with a `None` default and **do not rewrite the server-side default in the tool** -
   rewriting creates a second source for the same default that can drift. PRD-18216's "invalid value falls back to the default" works naturally because of this.

4. **The way to call a tool in tests and get its return value is `result[0][0].text`** (`tests/test_tools.py:287`),
   not `result[0].text`. Getting it wrong produces an error that looks like an MCP version difference.

## Three more traps found when adding tools again (notes after the SOP list + sync chat work, @ 3b1a5da)

5. **`tool.description` is the verbatim docstring with no dedent - indentation counts toward the 500 characters.**
   The length asserted at `tests/test_tools.py:139` includes the **8 spaces** in front of each continuation line and the newlines of blank lines.
   The same text measures 479 with `inspect.cleandoc`, but 544 once installed in the tool (difference of 65 = indentation).
   **Corollary**: when squeezing a description, deleting a whole line saves much more than trimming words (a line saves 9 characters for free),
   and estimating length by `cleandoc` will consistently underestimate. The exemption table at `:123` is still debt; both new tools this time were squeezed under 500
   (`list_sops` 493 / `chat_with_sop` 497) and nothing was added to it.

6. **The 1500 limit on `instructions` is already tight; adding a group of tools requires rewriting the old text first.**
   `tests/test_tools.py:160` asserts <= 1500, and before the change it measured **1496** - only 4 characters left.
   To fit the two SOP tools, this time twilio / clear_sop_section / the concepts paragraph were each squeezed a little, ending at 1487.
   **Corollary**: the next person adding a tool group also has to make room first; do not expect to simply append.

7. **Endpoints that run an agent cannot use `api_client`'s default retries and default read timeout.**
   `POST /api/agents/:id/run/wait` responds only after running a whole turn synchronously: the default 30s read timeout cannot cope
   (Aegra's own fallback is 1 hour), and the default 3 retries are even more dangerous - **a resend does not recover a lost response,
   it runs the agent again**. For this reason `post()` at `api_client.py:106` gained two keyword-only overrides, `read_timeout` / `retries`
   (default unchanged for other callers); `tools/sops.py:29` uses `_CHAT_READ_TIMEOUT = 300.0`
   + `retries=0`, and timeout errors are explicitly marked `retryable=false` with a hint to ask again in the same session.
   The other half of the trap is in the response: this endpoint **returns HTTP 200 even when the run fails, with `success: false` in the body**,
   so it never reaches `AgentError`; `tools/sops.py:243` therefore must check `success` explicitly,
   otherwise a failure is read as "succeeded but the agent said nothing".

## Corrections and new facts when adding tools again (notes after the connector tool toggle work, @ HEAD)

8. **Item 5 is outdated: the current `tool.description` is dedented, and length is counted after `cleandoc`.**
   Measured (same batch of docstrings): raw length vs. `description` in `list_tools()`:
   `upsert_agent_permissions` 3596->3156, `get_connectors` 563->499, `set_sop_procedure` 562->498.
   **The corollary flips**: estimating by the raw text (with 8-space indentation) is now a **consistent overestimate**; the correct approach is to write the docstring flush-left and then count, or to read `list_tools()` directly. By this method the two new tools measured
   `list_connector_tools` 488 / `set_connector_tools` 499, both < 500, not added to the exemption table.

9. **Item 8 also clears the old "red light".** The `get_connectors` description is now **499** (after dedent),
   no longer over 500, and `test_tools_list_snapshot` is now fully green (49 passed in this run). The 563 in the old record was the raw length,
   not the `description` length - the same dedent misunderstanding.

10. **The PUT on `agents/:id/connectors/:cid/tools` is like run/wait: a business failure is HTTP 200 + `success:false`.**
    "Agent does not exist" and "tools is empty" both take this path and never reach `AgentError`. `mspbotsagent_set_connector_tools` in `tools/connectors.py`
    therefore explicitly checks `result.get("success") is False` (same as `tools/sops.py`).
    On success it takes the inner `data` (`{agentId,capabilityId,names,enabled,restartRequired,pending}`).

11. **`capabilityId` is one UUID namespace across the whole platform - no new tool is needed to get `:cid`.**
    Measured 2026-09-16 (INT tenant `uc1dnhtl1io4ohbz7t0d3`): the `id` of each row in `/api/capabilities/connectors/catalog` (used by `get_connectors`),
    the `id` in `/api/agents/:id/connectors`,
    and the `id` in `sop-author/data-sources-list` entries are **identical in all three places** for the same connector
    (e.g. halopsa = `a79030ad-...4bddd`). So "interface 1" in the design doc, `GET /api/agents/:id/connectors`,
    **was not wrapped as a tool**: it returns the entire catalog (98 measured) plus a `selected` marker; to get `:cid`, use the existing
    `get_connectors` (tenant level) or `get_sop_data_sources` (agent level, every entry carries `id`). Only "interface 2"
    (list tools + toggle state) and "interface 3" (change toggles) were wrapped.

12. **`instructions` budget: after adding two sentences this time it measured 1453 (<=1500), leaving 47 characters.** The 1487 recorded in item 6 is outdated
    (room was freed after the skills tool group was hidden). The next person adding a tool group still has to measure before adding.

## Not covered

This file currently only covers the "add a new tool" path. Credential injection, the request-header contract of gateway mode, and container deployment have no accumulated fact entries yet.
