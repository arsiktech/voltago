# Volta Go MCP server

Volta Go exposes a read-only [Model Context Protocol](https://modelcontextprotocol.io)
endpoint so AI agents can find EV chargers in the loaded catalogue with the same evidence and
honesty rules the apps use.

- **Endpoint:** `POST https://voltago.app/mcp` (Streamable HTTP, JSON responses).
- **Access:** no account or key. Tool calls are rate limited per client IP
  (30 a minute by default).
- **Scope:** read-only. No tool writes community data, starts a charging session
  or takes payment.
- **Coverage:** worldwide directory coordinates/search in this implementation;
  actual countries depend on the loaded dataset. Live feeds retain their regional
  coverage. The worldwide candidate does not establish a production rollout.

## Connect

In the web app, choose **Copy AI prompt** beside **Data honesty** in the pinned
discovery footer. The iPhone app has the same one-tap copy action. Paste the result
into ChatGPT, Claude, Grok or your AI chat. There is no setup popup in Volta Go.

The shared [setup prompt](../contracts/agent_connection_prompt.txt) includes the
remote MCP URL, transport, authentication, Claude Code command and available tools.
It asks the receiving AI to configure the connection or guide manual setup, verify
it with a tool call, and ask where and how you want to charge. AI apps that do not
support remote MCP cannot connect through a pasted prompt; the prompt asks them to
explain that limitation. Copy feedback confirms that the prompt was copied.
If clipboard access fails, Volta Go exposes selectable text for manual copying.

Claude Code:

```bash
claude mcp add --transport http voltago https://voltago.app/mcp
```

Any other MCP client: add a remote (Streamable HTTP) server with the URL
`https://voltago.app/mcp`. No headers or OAuth are needed.

For a local server (`python3 server.py`), use `http://127.0.0.1:8765/mcp`.

### ChatGPT

1. Open [chatgpt.com/plugins](https://chatgpt.com/plugins), select **+**, then
   **Add custom MCP server**.
2. Enter the name `Volta Go` and the URL `https://voltago.app/mcp`. Set
   **Authentication** to **No authentication**, confirm the risk warning, and
   select **Create as a plugin**.
3. Install it from your personal plugins view. In a **Work** chat, type `@`,
   choose Volta Go, and ask, for example, "free CCS2 chargers near Valencia airport".

ChatGPT-specific behaviour:

- Every tool declares `readOnlyHint: true`, `destructiveHint: false` and
  `openWorldHint: false`: the tools read Volta Go's own catalogue and change nothing.
- Tools carry `openai/toolInvocation/invoking` and `invoked` status lines.
- If `find_chargers` gets no `place` and no `lat`/`lon`, it uses the city,
  region and country text from ChatGPT's `openai/userLocation` hint, never its
  coordinates. The result then reports `query.origin:
  "approximate_client_location"` and tells the model to ask for a place for
  nearer results.

This is a private custom connection. Listing Volta Go in the ChatGPT directory
needs a separate review. In particular, OpenAI's plugin guidelines discourage
raw location fields in tool inputs, so the `place`/`lat`/`lon` inputs must be
reconsidered first.

## Tools

| Tool | Use it to |
|---|---|
| `find_chargers` | Search near a place name (`place`) or coordinates (`lat`, `lon`) within `radius_km` (default 5, max 25). Optional filters: `free_only`, `max_price_eur_kwh`, `connector` (`Type 1`, `Type 2`, `CCS1`, `CCS2`, `NACS`, `GB/T AC`, `GB/T DC`, `CHAdeMO`, `Tesla`, `Schuko`), `min_power_kw`, `available_now`, `limit` (max 30). |
| `get_charger` | Read one charger's full evidence by `id`: every source and its claim, tariff windows, connectors, access, parking and community check-ins. Pass `lat` and `lon` too to refresh live status; they are required for chargers known only from a live feed. |
| `get_data_status` | Check dataset build time, record counts by evidence tier, live-source cache state and collection policy before claiming coverage or freshness. |

Each result has a short text summary for the model and the full data in
`structuredContent`.

Directory results do not imply current availability. Check truncation metadata
before claiming an exhaustive nearby inventory. Foreign tariffs retain their
native currency and units; the euro price filter does not perform FX conversion.

## How to read a charger

| Field | Meaning |
|---|---|
| `decision.level` | `go`: live data shows it free or cheap, compatible and available now. `check`: promising, but one live fact needs confirming (price window, parking, old feed). `verify`: good non-live evidence; confirm before driving. `lead`: known place, price or status unconfirmed. `avoid`: do not route there. |
| `evidence.level` | Where the facts come from: `live`, `official`, `official-lead`, `directory`, `community`, `unverified`. |
| `evidence.live_verified` | Only `true` rows have live availability. Connector status and `available_connectors` are blank for every other row. |
| `evidence.route_ready_now` | The strictest check: live, not stale, decision `go` and a free connector now. `available_now: true` keeps only these rows. |
| `evidence.live_status_stale` | A live-feed status older than three hours. |
| `price.price_now_eur_kwh` | The tariff valid now; `lowest_published_eur_kwh` may apply only in a time window (see `get_charger` → `price.windows`). |
| `excluded` | How many rows each filter removed. A filter never guesses: an unknown price, connector or power is excluded, not assumed to match. |

The provider's own app and signs at the site always override this data.

## Example

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "find_chargers",
            "arguments": {"place": "Puerto de la Cruz", "connector": "CCS2", "min_power_kw": 40}}}
```

## Protocol support

- **Current revision (`2026-07-28`):** stateless requests with per-request
  `_meta` and the `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers.
  `server/discover` is supported. Mismatched headers return `400` with
  `HeaderMismatch` (`-32020`); an unsupported version returns `400` with
  `UnsupportedProtocolVersionError` (`-32022`) listing the supported versions.
- **Earlier revisions (`2024-11-05` to `2025-11-25`):** the `initialize`
  handshake works; the server mints no session ID, so every request stands alone.
- `GET` and `DELETE` on `/mcp` return `405`. Batch requests are rejected.
- Browser callers: requests with an `Origin` header are accepted only from HTTPS
  origins, loopback HTTP origins, or origins listed in
  `VOLTA_MCP_ALLOWED_ORIGINS` (comma-separated).

## Operator settings

| Variable | Default | Effect |
|---|---|---|
| `VOLTA_MCP_RATE_LIMIT_RPM` | `30` | Tool calls per minute per client IP. Discovery and listing are not limited. |
| `VOLTA_MCP_ALLOWED_ORIGINS` | empty | Extra exact browser origins allowed besides HTTPS and loopback. |

Tool calls reuse the same caches and upstream budgets as `/api/chargers`, so an
agent cannot force extra live fetches.

Implementation: [`agent_mcp.py`](../agent_mcp.py). Tests:
[`tests/test_agent_mcp.py`](../tests/test_agent_mcp.py).
