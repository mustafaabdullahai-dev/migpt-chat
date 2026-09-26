# Session Notes (updated during work)

## Project
Agentic ChatGPT clone at `~/My Projects/chatgpt-clone` — LangChain/LangGraph + Groq + FastAPI backend,
Vite/React/TS/Tailwind frontend.

## Live URLs / processes
- Backend: `localhost:8100` (uvicorn, real Groq + DuckDuckGo, demo OFF).
- Frontend: `localhost:5200` (vite dev).   Production build OK (`npm run build`).
- NGORK IS THE ACTIVE TUNNEL (sole agent on the account URL):
  `https://lingo-suburb-living.ngrok-free.dev` (UI 200 + /api/chat verified).
- TUNNEL CONFLICTS RESOLVED: ngrok free = ONE URL per account. Root cause of the
  conflicts: 2+ agents on the same authtoken (this app + another project) each tried
  to bind `lingo-suburb-living.ngrok-free.dev`; the later agent sends the earlier one a
  "received stop request" and steals the URL. NOW only ONE ngrok agent runs (pid 857872).
  The other project uses its own cloudflared quick tunnel (pid 489939, :5173) — separate URL.
  cloudflared for THIS app was rejected by edge last check (`api.trycloudflare.com` timeouts,
  "Unauthorized: Tunnel not found") — fall back to ngrok when that happens.
- CRITICAL (FIRST RECURRENCE 15:09, Sep 25): ngrok v3.7+ AUTO-BINDS the account's assigned
  static subdomain for ANY `ngrok http <port>` started WITHOUT an explicit URL. So any other
  agent on this account (even `ngrok http 5173`) ALSO claims `lingo-suburb-living.ngrok-free.dev`
  → our URL then has MULTIPLE endpoints → ngrok refuses with the interception page
  "The url ... has multiple endpoints, but they do not all have pooling enabled" (HTTP 400).
  FIX that works: kill ALL ngrok agents, start ONE for :5200. After the 15:12 restart only
  pid 906354 is online and the URL is 200 again. The OTHER project MUST pass its own random URL
  (`--url` w/ a distinct ngrok-free subdomain) or use cloudflared, or it breaks our tunnel.
- Helper: `./scripts/serve.sh` — now `--tunnel=auto` (cloudflared first, ngrok fallback);
  ngrok mode REFUSES to start if another `ngrok http` agent is running (would conflict),
  telling you to use `--tunnel=cloudflare` instead. `--tunnel=ngrok|cloudflare|auto`.
- GOTCHA (vite dev proxy): `frontend/vite.config.ts` proxies `/api` to `VITE_API_TARGET ||
  http://localhost:8100`. If a bare `npm run dev` must keep working, the backend MUST be on
  8100 — do NOT point it at 8000 (that is the OTHER project's backend, wrong 404s).
  Restarting vite WITHOUT the env var proxy-swaps to the wrong server → JSON `{"detail":"Not Found"}`.
- FIXED (markdown render crash, `url.replace is not a function`): `frontend/src/utils/markdown.tsx`
  `URL_RE = /(^|[\s(])https?:\/\/.../gi` had ONLY ONE capture group — the `(` inside the `[\s(]`
  char class ate the intended group 2, so `String.replace` passed the OFFSET (a number) as the
  callback's `url` param → TypeError on every answer whose content contained a bare URL → the
  Message-level ErrorBoundary swallowed the whole assistant block ("Something went wrong
  rendering this block."). FIX: `/(^|[\s(])(https?:\/\/[^\s<>()[\]"'`]+)/gi` (URL now really is
  group 2) + a `typeof url === "string"` guard; also %28/%29-escape parens in citation hrefs.
  Verified headlessly: all 12 previously-crashing stored answers now render (react-markdown +
  rehype-highlight + linkify to static HTML). If an assistant block shows the boundary again,
  it now prints the real `error.message` (boundary updated to display it).
- FIXED (new-chat answers VANISH — "query shown, results hidden", every new chat):
  `frontend/src/store/useChat.ts` `onComplete` migration. New chats stream under the placeholder
  id "new" until `message_complete` returns the real id; the handler did
  `upsertLocal(realId, completed)` then merged the optimistic "new" messages into realId — but
  the merge filter `.filter(m => m.message_id !== completed.message_id)` applied to the
  ALREADY-ADDED realId list REMOVED the freshly-added assistant (its id IS completed.message_id),
  leaving only the query. FIX: keep the filter only on `migrated`; append the existing
  `draft[completed.conversation_id]` untouched. Reproduced headlessly against live SSE: final
  list went from `user[3c]` to `user[3c] -> assistant[50c]`. Existing chats were never hit
  (convId===realId → migration skipped).
- Docker: images built; compose with env ports (`BACKEND_PORT=8200 FRONTEND_PORT=5300 docker compose up`).
  GROQ_MODEL default now `qwen/qwen3.8-27b`.

## Follow-up questions feature + GROQ OTPM gotcha (Sep 25)
- Requested: short auto titles after the first user query AND clickable follow-up suggestions
  after each answer. Titles were already implemented (`app/services/titles.py`, ≤6 words) and
  live-tested exact example. Follow-ups are new:
- Backend: `_finalize` in `app/api/chat.py` now generates follow-ups for the JSON `/api/chat`
  path (was hardcoded `[]`); the SSE path already generated them in `_persist_success`. Both
  build from the answer via `app/services/followups.py::generate_followups` (3 suggestions)
  and travel inside `message_complete` / `ChatResponse.followups`.
- Frontend: `ChatMessage.followups?: string[]` (`types/index.ts`), captured in `onComplete`
  (`useChat.ts`), rendered as clickable "Follow-up" pill buttons in `Message.tsx` (calls
  existing `send(f)`). Typecheck + build clean.
- **GROQ OTPM GOTCHA (found empirically)**: on the on_demand tier the model is limited to
  ~1000 OUTPUT tokens/min. Auxiliary LLM calls (titles/follow-ups/summaries) used `get_llm()`
  which defaults to `max_tokens=4096`, so the request could be REJECTED outright with
  `429 ... output tokens per minute (OTPM): Limit 1000, Requested N` → intermittent empty
  follow-ups and the old long fallback titles. FIX: cap `max_tokens` on small calls
  (title=128, followups=256, summaries already 400) + new `call_with_retry(fn, attempts=3,
  base_delay=0.5)` in `app/services/llm.py` (exponential backoff, WARNS on each retry; JSON
  log has the formatted detail under the `message` key, NOT `msg`) used by
  `generate_title`/`generate_followups`. Retries only survive burst-level 429s, not a truly
  spent per-minute budget → occasional graceful `followups: []` for long synthesis answers is
  expected. Titles first, follow-ups second, so a title earning the spot is the priority.
- Verified live: CAP theorem query → 3 real follow-ups streamed in `message_complete` + short
  title; second query hit the token wall (logged retries, fell back to `[]`) — graceful.
  Backend restarted (:8100, pid 1320510), suite 238 passed. Frontend build clean.

## MARKDOWN / ERROR HANDLING / SECURITY / ENV audit (spec items, Sep 25)
- MARKDOWN: already compliant via react-markdown + remark-gfm (headings, bold, italic, lists,
  tables, links, blockquotes, task lists, strikethrough) + rehype-highlight (code blocks/`inline`)
  + linkify (`[1]` citations → source links, bare URLs autolinked; parens %28/%29-escaped). NO
  rehype-raw → raw HTML is NEVER rendered; headless check confirmed `<script>`/`<img onerror>`/
  `<a javascript:>` come out as ESCAPED TEXT and `[x](javascript:…)` hrefs are stripped to "" by
  ReactMarkdown's defaultUrlTransform. Links get target=_blank + rel=noopener noreferrer.
- ERROR HANDLING additions:
  - NEW global exception handler (`app/main.py::unhandled_exception_handler`): `logger.exception`
    full traceback server-side, client gets generic `{"detail": "Internal server error"}`, never a
    stack trace. Registered for ALL exceptions.
  - NEW streaming retry in `synthesize.synthesize_answer`: on transient LLM failure (rate limit /
    connection) with NO tokens emitted yet, retries up to 3x with exponential backoff (0.5s, 1.0s)
    — skips retry if a partial stream already started (fallback instead); logs each retry warning.
  - Document upload no longer echoes internal exception text to the client (logs `file_name`+err
    server-side; generic "Could not process this file…" response). FIXED a logging bug found by
    the tests: `extra={"filename": …}` is a RESERVED LogRecord attr → KeyError crash in the except
    branch; renamed to `file_name`.
  - Pre-existing graceful paths (verified): search unreachable → honest "couldn't fully verify"
    degrade (no LLM call for dynamic facts); calculator failures → clean explanations
    ("I couldn't identify a calculation…"/convert/formula); Groq unavailable → clean
    "I couldn't reach the language model…" + "(This failure has been logged server-side.)".
- SECURITY audit (all GREEN):
  - prompt injection: synthesize `_SYSTEM_HEAD` + `_HEAD_NO_LIVE` instruct the model to treat
    webpage/document text as UNTRUSTED DATA and ignore embedded instructions; evidence labelled
    "UNTRUSTED DATA — reference only".
  - SSRF: `validate_url` blocks private/loopback/link-local nets + non-http(s) schemes + unfriendly
    DNS; fetch_webpage replays the per-hop SSRF-safe redirect loop (≤5 hops). Unit-tested.
  - malicious webpages: 4 MB download cap + text limit; auto-retry on transient err.
  - arbitrary code execution: calculator = AST-whitelist evaluator (no eval/exec, LLM code only
    as *expression* pairs); python sandbox = docker/restricted w/ import whitelist + rlimits;
    RestrictedPython-compiled exec is the only exec and it's sandboxed.
  - path traversal: `validate_filename` strips dirs & rejects separators (upload); data dirs local.
  - malicious uploads: extension allow-list + magic-byte sniff (`sniff_file_signature`) + 20 MB cap
    (+ empty-file reject). Unit/API-tested.
  - XSS: see MARKDOWN above (headless-verified all four vectors).
  - SQL injection: all DB access via SQLAlchemy ORM/parameterized; no user input in raw SQL.
  - command injection: no os.system/shell=True anywhere; subprocess only in python_analysis with
    fixed argv (docker run/inspect + venv python), user code never reaches a shell.
  - secret leakage: SafeJSONFormatter redacts reserved keys; GROQ keys never logged.
- ENV VARIABLES: all spec items exist in `app/core/config.py` (Pydantic) AND `.env.example`
  (APP_ENV, GROQ_API_KEY, GROQ_MODEL, DATABASE_URL, SEARCH_PROVIDER, MAX_SEARCH_RESULTS,
  MAX_PAGE_SIZE, ENABLE_WEB_SEARCH/ARXIV/WIKIPEDIA/CALCULATOR/DOCUMENT_RAG, SECRET_KEY + more).
  `.gitignore` excludes `.env`, `.env.*.local`. SECRET_KEY is declared but unused (no auth/sessions
  yet). FIXED `.env.example`: GROQ_MODEL was still `llama-3.3-70b-versatile` (not on this account)
  → `qwen/qwen3.8-27b`.
- TESTS: new `tests/test_error_handling.py` (5) — generic 500 detail + server-side log, upload
  no-leak, synthesize retries twice then succeeds (calls==3), no retry after partial stream,
  fallback after exhausted retries. Suite **243 passed**. Backend restarted (:8100, pid 1340623).

## FREE-FIRST ARCHITECTURE + OPTIONAL SEARCH PROVIDERS (spec audit, Sep 25)
- Stack audit (all GREEN, zero paid services mandatory): Groq (free tier) LLM; DuckDuckGo
  search; Wikipedia tool; arXiv tool; local FAISS (`FaissVectorStore`); LOCAL embeddings default
  (`embedding_provider=local`, fastembed ONNX `all-MiniLM-L6-v2` — no API key); SQLite; FastAPI;
  React; LangChain; LangGraph. `GROQ_API_KEY` is the ONLY thing needed for real (non-demo) mode.
- `SearchProvider` abstraction already meets the spec EXACTLY: `class SearchProvider(ABC)` with
  `async def search(self, query: str, limit: int = 8) -> list[SearchResult]` in
  `app/services/search.py`. Implemented/registered: `DuckDuckGoSearchProvider` (free default —
  4 concurrent tiers: html/lite/ddgs/Wikipedia, first-success wins), plus OPTIONAL
  `TavilySearchProvider`, `BraveSearchProvider`, `SerperSearchProvider`, and a bonus
  `GoogleSearchProvider` (official Custom Search JSON API).
- Opt-in logic (`get_search_provider`): demo → Mock; Google auto-selected only when BOTH
  `GOOGLE_SEARCH_API_KEY` AND `GOOGLE_SEARCH_CX` set; Tavily/Brave/Serper selected ONLY when
  `SEARCH_PROVIDER=<name>` AND the matching key is set; otherwise falls back to DuckDuckGo.
  No key + the app keeps working on the free path. Env-driven via `SEARCH_PROVIDER` + keys
  (all in `.env.example`, keys empty).
- NEW `tests/test_search_providers.py` (10): ABC abstract+async signature + default limit=8,
  DDG is the default provider (real mode), demo → Mock, paid providers require key else fall
  back to DDG (parametrized tavily/brave/serper), Google requires both key+cx, and offline
  response-mapping/dedupe unit tests for Tavily/Brave/Serper (fake HTTP client — no network).
  Suite **253 passed**.

## TOOL_REGISTRY + LANGGRAPH NODES + CONDITIONAL EDGES (spec, Sep 25)
- TOOL_REGISTRY: the registry (`app/tools/base.py` ToolSpec/ToolRegistry + `app/tools/registry.py`)
  already registered every spec tool by name (web_search, fetch_webpage, calculator, wikipedia,
  arxiv, deep_research, document_search, unit_converter, get_current_time, decision_analysis; +
  bonus comparison, python_analysis, summarizer, get_current_date). NEW: explicit spec-shaped
  `TOOL_REGISTRY: dict[str, ToolSpec]` export (canonical keys incl. the `time` alias →
  get_current_time) + `relevant_tools(intent, requires_tools, mode)` helper that computes the
  MINIMAL enabled tool subset ("agent receives only tools relevant to the task"): intent→tool
  map + classifier `requires_tools`, `mode=web/deep` forces search tools, `no_web` strips them,
  disabled (feature-toggle) specs drop out.
- NEW graph node `select_tools` (`app/agents/nodes/select_tools.py`): deterministic, runs after
  classify and before routing; writes `state.selected_tools` (added to AgentState). Graph now:
  START → classify → select_tools → (conditional) route_query → calculate | datetime |
  retrieve_documents | summarize | background_research | multi_tool | plan | synthesize;
  research loop plan → search → fetch_sources → analyze_evidence → (conditional evidence_sufficient)
  → synthesize | multi_tool | plan (additional search); calculate/retrieve/synthesize → remember → END.
  Tool EXECUTION stays distributed across the separate nodes (search_web/fetch_sources/calculate/
  multi_tool) — nothing is stuffed into one node.
- Node-name mapping vs spec: classify_query=classify ✓, plan_query=plan ✓, select_tools=NEW ✓,
  search_web=search ✓, fetch_sources ✓, analyze_evidence ✓ (evidence_sufficient = "enough
  evidence? YES/NO" edge) ✓, calculate ✓, retrieve_documents ✓, synthesize_answer=synthesize ✓.
  Named-but-inline: generate_citations=build_citations in synthesize._finalize; verify_sources=
  validate_markers+attach_sources_section in synthesize._finalize; stream_response=the token
  streaming inside synthesize_answer (deltas emitted as they arrive — a separate node would
  buffer the whole answer); decision_analysis=the DECISION_ANALYSIS tool invoked by
  analyze_evidence/multi_tool (route goes through plan for DECISION/COMPARISON intents);
  verify_result=calculator node's internal validation (safe-eval, no/plausible-output guards);
  execute_tools=the deterministic tool invocations inside the leaf nodes above (this is a
  deterministic pipeline, NOT an LLM tool-calling loop). Changing those would add latency +
  risk with no fidelity gain; flagged here instead.
- NEW `tests/test_tool_registry_graph.py` (13): TOOL_REGISTRY contains all 10 spec keys + entries
  are enabled ToolSpecs + "time" alias; relevant_tools subsets (calc-only, datetime, general-chat
  empty, classifier merge, no_web strip, web force, unknown-tools drop / explicit kept); graph
  node set contains the required separate nodes; end-to-end calc path records selected_tools w/o
  web_search; research path records relevant tools + has an answer. Suite **266 passed**. Backend
  restarted (:8100, pid 1377649).

## UI fidelity (matches original ChatGPT homepage source user pasted)
- Uses the REAL official assets extracted from the supplied chatgpt.com HTML reference:
  `ChatGPTLogo.tsx` = exact `data-sidebar-blossom` path (viewBox 0 0 20 20);
  `Wordmark.tsx` = exact `lightweight-home-wordmark-text` wordmark (viewBox 0 0 357 62);
  `favicon.svg` = official logo path in white.
- Current-version palette in `index.css`: dark main bg `#000000`, panel `#171717`, cards
  `#2f2f2f`/`#303030`, border `rgba(255,255,255,0.13)`; light `#fff`, panel `#f9f9f9`,
  cards `#f4f4f4`/`#ececec`, line `#e5e5e5`. Theme toggle in sidebar top-right.
- Landing copy matches source: heading "Where should we begin?" (no big centered logo, like
  current ChatGPT), composer placeholder "Ask ChatGPT", disclaimer "ChatGPT is AI and can make
  mistakes.", mode pills (Auto/Web/Deep/No web), circular white send button in dark.
- Empty state (new chat): composer VERTICALLY CENTERED mid-screen like the real home page —
  heading above it, compact single-row suggestion cards (Calculate/Research/Compare/Summarize)
  below it. Verified via playwright: composer center ≈ viewport center.
- Composer = ONE box (no stacked duplicate look): mode pills (Auto/Web/Deep/No web) moved
  INSIDE the rounded-2xl card above the textarea (textarea now bg-transparent); attach + send
  in footer. Verified: all pills + textarea + attach + send inside a single rounded-2xl card.
- COMPOSER NOW MINIMAL (user request): classic single-line ChatGPT bar only — [+] attach on the
  left, textarea "Ask ChatGPT" in the middle, [↑ send] on the right, all inside ONE rounded-2xl
  box. Mode pills (Auto/Web/Deep/No web) REMOVED from UI (store still exposes mode, backend
  defaults to auto). Disclaimer paragraph removed. Uploaded-file chips show above bar only when
  a file is attached.
- Sidebar: logo + official wordmark, white "New chat" pill, search, grouped history
  (Today/Previous 7 Days/Previous 30 Days/Older), clear chats, user row.
- Cleaned tool-activity noise: status/heartbeat orchestrator steps filtered out at render
  (`ToolActivity.tsx`), so no generic "Status" rows pollute the transcript.

## Test suite
- `backend/tests`, 217 passing (offline mock). Run: `cd backend && .venv/bin/python -m pytest -q`.
  conftest wipes /tmp/chatgpt-test-data + db per session (keeps FAISS index small/deterministic).

## Verified working (real mode)
- /api/health OK (demo_mode:false, llm:groq).
- Real research: "What is the latest LangGraph version?" → 4 real sources, citations, 577-char cited answer.
- "Survey of agentic AI architectures" → ACADEMIC_RESEARCH, all 4 cited sources are arxiv.org papers
  (paper-first prioritization via search_web/needs_papers).
- Document RAG live: upload rag_live.txt → DOCUMENT_QA "how does RAG ranking work" answered with the
  retrieved chunk cited; delete → 204, docs list empty (validates FAISS deadlock + chunk-id fixes).
- Browser E2E (playwright repro at /tmp/opencode/repro/repro.cjs): zero page errors, assistant markdown
  answer rendered, sidebar title auto-generated ("Latest LangGraph Version") via Groq.
- LONG-TERM MEMORY (user profile) verified live: chat1 "My name is Abdullah and I am a software engineer…"
  → chat2 in a BRAND-NEW conversation "What is my name?" → "Yes, I remember! Your name is Abdullah…".
  Persisted rows confirmed in SQLite user_memory ({name, job}); streaming path completes (message_complete).

## Long-term memory feature (added Sep 25)
- What: profile facts survive across conversations. LLM recalls in the SAME chat AND any NEW chat.
- Storage: new `user_memory` table (user_id, key, value, updated_at; unique per user+key).
- Flow: `chat.py` loads `memory_dict(user_id)` into AgentState.memory → `synthesize` injects
  "KNOWN FACTS ABOUT THE USER FROM PERSISTED MEMORY" into the system prompt → after every answer a
  `remember` graph node runs a tiny LLM call that extracts personal facts and `upsert_memory`s them.
- Graph: every terminal path now goes `-> remember -> END` (calculate & synthesize both converge).
- Same-chat context widened too: synthesize now carries up to 40 history messages under an 8000-char
  budget (`_recent_history`), instead of a fixed last-12 window.
- Config: `memory_enabled=True`, `memory_max_facts=20`, `memory_conv_chars=4000`.
- CRUD: `list_memory`, `memory_dict`, `upsert_memory`, `clear_memory` in `db/crud.py`.
- Tests: `tests/test_memory.py` (CRUD upsert/list/clear, prompt injection, history budget, remember
  node safe on non-JSON). Full suite: 64 passed.
- Note: remember node adds one small Groq call per user message (disabled via memory_enabled=False).

## Live data-gathering caption (added Sep 25)
- During research/search runs the assistant now shows ONE ChatGPT-style animated caption (3 pulse dots +
  status text) that advances through the pipeline: "Thinking…" -> "Planning the research…" ->
  "Searching the web (N queries)…" -> "Found N candidate sources" -> "Reading top N sources…" ->
  "Analyzing N evidence sources…" -> "Synthesizing your answer…" -> then the answer streams and the
  caption disappears. Source cards still appear above the text during the gather->synthesize gap.
- Removed the duplicate/optimistic "Thinking…" assistant bubble (was rendering alongside the live
  stream) and stopped rendering per-tool rows as live pills (kept for completed history messages,
  ToolActivity in Message.tsx). Status events now feed `streamingStatus` in the zustand store instead
  of synthetic ToolRun rows.
- Fixed backend pluralization bug: "Searching the web (2 querys)…" -> "queries".
- Verified via Playwright (`/tmp/opencode/repro/caption-probe.cjs`): observed caption sequence
  Thinking…/Planning/Searching (2 queries)/Reading top 4 sources/Searching (4 queries)/Synthesizing,
  0 page errors. Frontend build passes.

## Universal "dynamic" calculator + numpy (added Sep 25)
- The CALCULATION path is now a dynamic resolver (`agents/nodes/calculate.py`) instead of one math
  extractor. It builds a plan, then executes through SAFE tools (AST-whitelist evaluator + pint).
  Order: pure math -> "X% of Y" -> word arithmetic ("2 to the power 10 plus 5") -> unit conversion
  ("convert 100 km to miles", "how many feet in 2 meters") -> geometry/finance formulas (area/volume
  of circle/rect/triangle/sphere/cylinder/cube, simple & compound interest, discount, increase) ->
  numpy list/vector/matrix ops in words (mean/median/std, determinant, inverse, dot product, norm,
  transpose) -> embedded subexpression -> LLM JSON fallback (expression or unit triple).
- REGISTERED numpy inside the safe evaluator (`tools/calculator.py`): numpy 2.5.3 is imported but ONLY
  through an allow-list (plain names mean/median/std/var/det/inv/solve/dot/matmul/norm/rank/eye/
  zeros/ones/linspace/transpose/... AND `np.` / `np.linalg.` prefixes). Arbitrary attributes,
  subscripts, comprehensions, lambdas, attribute access outside np.* remain BLOCKED (no eval/exec —
  numpy scalar/array results are coerced to JSON-safe numbers/lists).
- Classifier (`routers/classifier.py`): added geometry/finance words to CALCULATION; unit-conversion
  words now require a number nearby (avoids misrouting "how many" factual queries).
- Tests: `tests/test_calculator.py` grew to 28 (numpy mean/median/std, dot, det/inv, np.* and
  np.linalg.* paths, unsafe-attr blocked). Full suite: 71 passed.
- Live-verified through /api/chat (conf=1.0, calculator tool, deterministic fast-path — no LLM call):
  mean([1,2,3,4,5])=3, det(matrix)=-2, dot=[32], inv=[[0.5,0],[0,0.25]], norm([3,4])=5,
  std([10,20,30])=8.16497, "17.5% of 85000"=14875, "100 km to miles"=62.1371, simple interest,
  area of circle. Matrix results render as pretty rows.

## Loan amortization / expert multi-step calc (added Sep 25)
- Fixed the "I couldn't identify a calculation" failure for natural-language multi-step prompts.
- New `loan` plan kind: deterministic resolver (`_try_loan`) detects amortization/mortgage/EMI/loan +
  extracts principal, annual rate %, term (years/months), payments/year (monthly default; quarterly/
  semi/annual, weekly, biweekly). Computes the French (fixed-payment) method in pure Python
  (`_loan_schedule`): payment = P·r/(1−(1+r)^−n), then a full period-by-period schedule
  (payment/principal/interest/balance). If >48 periods, shows first 12 + last 12 with an ellipsis row.
- Classifier: amortiz/mortgage/monthly payment/emi/schedule-of now route to CALCULATION.
- LLM fallback hardened: tolerant JSON parsing (fences, trailing commas), supports an optional
  `loan` type alongside expression/unit.
- Live-verified via /api/chat with the user's exact expert template (principal 200000, rate 6.5%,
  French method, 30 years) → payment $1,264.14, total interest $255,088.98, balance reaches $0.00
  in period 360, full markdown table in the answer (conf=1.0, intent=CALCULATION).
- Tests: test_loan_plan_french_method, test_loan_payment_math, misroute guard, no-rate guard.
  Full suite: 75 passed.

## Fix: follow-up messages no longer create a new chat (Sep 25)
- Symptom: typing a follow-up in any existing chat jumped to a brand-new conversation.
- Root cause: frontend `send()` never sent the current conversation id — `streamChat`
  only POSTed `{message, mode, documents}`, so `_prepare` in `chat.py` always created a
  new conversation (conversation_id is only reused when present in the request body).
- Fix: `streamChat()` gained a `conversationId` param (body includes `conversation_id`);
  `useChat.send()` forwards the active `currentId` unless it's `null`/"new" (which is the
  explicit New-chat case, where the backend creates a fresh conversation).
- Verified via Playwright routed API: sending message 1 then a follow-up produced the SAME
  conversation_id in both `/api/chat/stream` responses and the conversations count stayed
  flat (20); message 1 created the conversation, the follow-up appended to it. Build passes.

## Fix: clear-all conversations + disappearing first user message (Sep 25)
- Symptom: cleared all conversations, sent "Hi", got a reply — but the "Hi" bubble vanished
  leaving only the answer, and every cleared conversation reappeared.
- Root cause A (message vanishing): a brand-new chat optimistically stores the user message
  under the placeholder key "new", but the reply arrives stamped with the real
  conversation_id, so the view (which reads messages[currentId]) showed only the assistant
  message. Fixed in `useChat.send()`'s onComplete: when the returned conversation_id differs
  from the placeholder, local messages are migrated from the "new" key to the real id
  (user first, then the completed assistant message).
- Root cause B (conversations returning): the frontend called `DELETE /api/conversations`,
  but the backend had NO clear-all route (only `DELETE /{id}`) → 405, server never deleted;
  the UI merely cleared local state and the next sidebar reload re-fetched everything.
  Added `DELETE /api/conversations` → `delete_all_conversations()` (removes messages +
  sources + tool_runs + feedback + conversations for the user) in backend.
- Verified end-to-end via Playwright (ngrok tunnel): clear returns DELETE 204 and
  conversation count drops to just the new "Hi" thread; after sending "Hi" BOTH the user
  bubble ("Hi") and the assistant reply are visible, and the sidebar shows exactly the one
  new conversation. Suite: 75 passed; frontend build passes.

## LLM-driven decisions for expert/structured calculation queries (Sep 25)
- User request: "I want the LLM to take the input itself and generate decisions and
  calculations accordingly" — i.e., structured expert templates (with placeholders) must
  not be refused; the LLM reasons, picks values, and computes.
- Reworked the calculator's LLM fallback (`_llm_plan`):
  - Now uses `with_structured_output(_CalcPlan)` (expression/unit/loan/none) for reliable
    parsing, with raw-JSON fallback.
  - New decision prompt (`_LLM_PLAN_SYSTEM`): substituted placeholders, chooses sensible
    defaults guided by the query's own examples (loan → 200,000 @ 6.5% / 30y; beam moment
    M=wL²/8; churn = lost/total*100), and REQUIRES complete end-to-end expressions (fixed
    the earlier `2/8` truncation failure), recording every decision in `assumptions`.
  - `assumptions` are appended to math and loan answers ("**Assumptions / decisions:**").
- Deterministic upgrades so common expert formulas skip the LLM entirely:
  - `_try_formula`: added max bending moment of a beam (→ expr `(w*span**2)/8`) and
    customer churn rate (→ `(lost/total)*100`).
  - Fixed `_try_units` false positive: "120 customers lost…" matched `(to)` inside
    "cus-to-mers" → conversions now require the from-unit token to be a real unit word.
- Order of plans: pure math → percent-of → word arithmetic → units → loan → formula →
  numpy → embedded → LLM DECISION → needs_input (last resort, offline only) → none.
- Live-verified via /api/chat: bending moment = 9 (with steps), churn = 1.3333%,
  loan placeholder template → full amortization table with assumptions block,
  unit conversions intact. Suite: 81 passed.

## Resilient search + graceful degradation (product-vision gaps fixed)
- Found the two vision gaps: (a) web answers returned `sources: []`; (b) deep research
  claimed "I couldn't reach the language model." Root cause: DDG intermittently blocks
  this network (connect timeouts / "No results found.") → `evidence: 0` → no citations,
  and the empty-context LLM call then often failed under Groq rate limits.
- Search hardening (`app/services/search.py`, `app/tools/web_search.py`):
  - Query rewriting: strips conversational filler ("what is the best…" → keywords).
  - De-escalation loop (3 levels): full query → drop quoted/last token → first 4 tokens.
  - Tier concurrency: html.duckduckgo.com + lite.duckduckgo.com + ddgs lib + WIKIPEDIA API
    gathered, first non-empty wins, hard 18s budget; staggered tool starts (0.35s).
  - Wikipedia tier proven live: returns real citable articles while DDG is fully blocked.
  - NEW `GoogleSearchProvider` (official JSON API) added to config + .env.example; needs
    BOTH `GOOGLE_SEARCH_API_KEY` (now in backend/.env) and `GOOGLE_SEARCH_CX` (empty —
    auto-selected when both set).
- Zero-evidence guard (`app/agents/nodes/synthesize.py`):
  - `needs_degrade(state)`: research intent + no evidence + no calculation.
  - Dynamic queries (regex on latest/news/scores/date/etc.) → canned honest "I couldn't
    verify this live…" answer, no LLM call, confidence 0.3 (no hallucination).
  - Static queries → LLM still runs with `_HEAD_NO_LIVE` guardrails (general knowledge,
    clearly labelled, no temporal claims, no invented sources).
- Verified live: best-rag-db → DECISION_ANALYSIS, sources=4 (Wikipedia) WITH Sources
  section + [n] citations; langgraph-latest → "I couldn't fully verify this from the
  available sources…" + cited; sports score → honest degradation (conf 0.3).
- Tests: 88 passed (adds tests/test_search_degrade.py).

## Tool system spec (modular registry, web_search, fetch_webpage, deep_research)
- Registry already met the base contract: name/description/args_schema/handler/timeout/error
  handling/logging per tool (app/tools/base.py `ToolSpec`, `ToolRegistry`, `run_spec`), built
  by `settings`-driven toggles in app/tools/registry.py (now 11 tools incl. deep_research).
- `web_search` upgraded (app/tools/web_search.py):
  - New `WebSearchArgs` fields: `queries` (run & merge multiple) + `exclude_domains` (domain
    filtering, exact + subdomain match) + optional `limit`.
  - `_merge` dedupes by URL, applies exclude filters, caps at limit.
- `fetch_webpage` upgraded (app/tools/webpage.py + models/schemas.py):
  - Per-hop SSRF-safe redirect loop (httpx follow_redirects=False; each 3xx target re-runs
    `validate_url` — closes the DNS-rebinding/redirect-to-private hole), max 5 hops.
  - One auto-retry on transient HTTP errors/timeouts; raw download capped at 4 MB before the
    existing text limit.
  - Preserves useful links: `PageContent.links` (up to 12 body links, anchor text, relative URLs
    resolved, nav/social hosts dropped).
  - `validate_url(url, require_resolution=False)` lenient mode added (app/core/security.py) for
    citation validation so a temporary DNS failure doesn't drop engine-returned sources (fetch
    stays strict).
- `deep_research` tool (NEW, app/tools/deep_research.py, registered):
  - Standalone bounded workflow: query decomposition (gap-aware, JSON-parse + fallback) →
    multiple searches → dedupe/rank → fetch top pages → extract evidence → gap-driven follow-up
    loop (max_iterations/max_sources) → citation validation → synthesized answer.
  - Returns structured dict {query, decomposed_queries, iterations, sources, evidence, answer, errors}.
  - Injectable search/fetch/plan/answer_llm for hermetic testing; defaults to real tools.
  - Kept OUT of the auto-router tool lists (planner would misuse it; the graph already runs this
    workflow), but callable + tested.
- Live smoke test (network): web_search 8 results, fetch_webpage title/text/12 links,
  deep_research iterations=1, 4 citable sources, honest synthesis. Backend restarted, calc chat OK.
- Tests: 96 passed (new tests/test_tools_spec.py: 8 tests).

## Sandboxed Python analysis tool (NEW)
- `python_analysis` tool (app/tools/python_analysis.py) — runs a user Python snippet in an ISOLATED
  sandbox for data/CSV analysis, statistics, numerical/scientific computation and matplotlib charts.
  Registered in the registry (category `analysis`, 12th tool), NOT in the auto-router lists.
- Backend ladder (auto-probe, cached 60s): **docker** → **restricted** → **disabled** (tool refuses to
  run if no sandbox). Docker is the default here.
- Shared executor `app/tools/python_sandbox/worker.py` is deliberately self-contained (no app imports)
  so the same file runs as a venv subprocess AND inside the image. Docker image `python-sandbox:latest`
  built from the adjacent Dockerfile.
- Hard isolation (defense in depth):
  - Docker: `--network none --memory 768m --cpus 1 --pids-limit 64`, non-root (USER 65534), tmpdir
    bind-mounted rw (chmod 0o777).
  - RestrictedPython compile + import whitelist (math, statistics, random, re, json, collections,
    itertools, functools, datetime, numpy, pandas, scipy, matplotlib) via guarded `__import__`; ALL
    other modules (os, subprocess, socket, urllib, requests...) raise ImportError.
  - Restricted builtins: no `open`/`eval`/`exec`/`compile`/`input`/`getattr`; `list/dict/set/sum/min/
    max/enumerate/...` restored from safe_builtins (harmless, data-analysis needs them). `breakpoint` nil.
  - Process rlimits in worker.main(): RLIMIT_CPU (cap 20s), RLIMIT_AS (512 MB), RLIMIT_NPROC (64).
  - `_print_` must be the `PrintCollector` CLASS (v8 transforms print → instantiate + `_call_print`);
    collected via the injected `_print` instance. `_inplacevar_` guard provided for `+=` etc.
- API: code (≤40k chars), files dict (≤10 files, ≤200KB each, ≤500KB total, no NUL), timeout_seconds
  (1–120). `result(x)` returns a JSON-able value (numpy/pandas coerced); matplotlib figures captured as
  base64 PNG charts; stdout captured (print works).
- Verified live via the registry dispatch path (`run_spec`): CSV groupby stats + 1 PNG chart in ~2–3s;
  `open('/etc/shadow')` block, `import os/socket/subprocess` block, `os.system` block; `while True` killed
  by CPU limit; 600 MB alloc → MemoryError. Image rebuilt after builtins/PrintCollector fixes.
- Config: `enable_python_analysis=true`, `python_sandbox=docker` (also `restricted`/`disabled`; `auto`
  autodetect). .env.example updated.
- Tests: 102 passed total (+6 in tests/test_python_sandbox.py: validate caps, disabled refusal, restricted
  guard blocks, statistics capture, import whitelist, worker.main() hard CPU limit on an infinite loop).

## Wikipedia search/retrieval tool (background, history, definitions, intro research)
- Product rule: Wikipedia is used for GENERAL BACKGROUND / HISTORICAL INFO / DEFINITIONS / INTRO
  RESEARCH — and it is never treated as the highest-authority source. Every Wikipedia result is
  explicitly marked SECONDARY / encyclopedic and distinguished from primary sources in the answer.
- Tool upgrade (app/tools/wikipedia.py): two modes —
  - `search`: ranked articles (title, url, extract snippet) via list=search/extracts.
  - `page`: best-match article's full intro + key section headings + categories + last-updated
    timestamp via prop=info|extracts|sections|categories. Exposes `IS_SECONDARY=True` + `is_secondary`.
  - Pure parse helpers `_parse_search` / `_parse_page` (unit-testable), `article_url` (quote-escaped).
- Authority model (app/services/citations.py):
  - `source_authority(quality)` -> primary | secondary | community | unknown
    (primary = official docs, primary research, gov, academic; secondary = wikipedia, major
    publication, company docs, technical blog).
  - `source_label(quality)` -> e.g. "encyclopedia (secondary reference)". `build_citation_block`
    now prints a `Type: <label>` line under each source.
  - Wikipedia already ranks lowest among usable kinds (`_QUALITY_RANK` 8) so primary sources sort
    first and end up cited as [1], [2] in research answers.
- Classifier (app/agents/routers/classifier.py): new intent `BACKGROUND_KNOWLEDGE`.
  - Deterministic pre-rule `_is_background()` (runs BEFORE the LLM) catches define/definition of /
    what is / what are / who was / history of / meaning of / overview of / introduction to /
    background on / explain (+ ≥3 words, minus `_BACKGROUND_EXCLUDE` like best/latest/version/vs/
    compare/recommend/your/weather/today/price). Reason: the LLM kept falling back to plain
    GENERAL_CHAT for these; pre-rule makes definitions reliably use Wikipedia.
- New graph node `background_research` (app/agents/nodes/wikipedia.py):
  - Wikipedia search → page retrieval of the best article (intro + sections, tagged
    source_type="encyclopedia") → remaining wiki hits as snippet evidence.
  - Best-effort PRIMARY-source contrast: web_search + fetch_webpage of the first NON-Wikipedia
    result (tagged source_type="webpage"; quality inferred, e.g. docs.python.org -> official docs).
  - Fully offline in demo/mock (uses MockSearchProvider); real mode tolerant of DDG being blocked
    (Wikipedia tier keeps working).
- Graph wiring: route_query BACKGROUND_KNOWLEDGE -> background_research -> synthesize;
  `_RESEARCH_INTENTS` includes BACKGROUND_KNOWLEDGE (honest degrade if evidence empty);
  `_SYSTEM_HEAD` gained a SOURCE AUTHORITY block telling the LLM to label encyclopedia refs as
  background, prefer primary sources for decisive claims, and never present Wikipedia as the
  authoritative word.
- Live smoke (real mode): "What is the history of the Panama Canal?" -> BACKGROUND_KNOWLEDGE;
  answer cites [1] and says information "is drawn from encyclopedic background sources"; Sources
  show `Type: encyclopedia (secondary reference)`. Groq synthesized, wiki tool reliable while DDG
  was blocked (primary-source contrast degraded gracefully, answer stayed honest about it).
- Tests: 116 passed total (+12 in tests/test_wikipedia_tool.py: parse helpers, page structure,
  evidence marked encyclopedia/secondary, authority mapping, citation-block Type labels, node
  contrast path with mocked tools; classifier now expects What-is/history -> BACKGROUND_KNOWLEDGE;
  graph + API tests updated).

## arXiv research-paper tool + document RAG (added Sep 25)
- `app/tools/arxiv.py`: canonical `async def search_arxiv(query, limit=5, sort_by="relevance")`
  returns one dict per paper exactly as specified: `{"title","authors","abstract","published","url"}`
  (published → YYYY-MM-DD, url → arxiv.org abstract page). Pure `parse_entries(xml_text)` +
  `clean_query()` for testability. `ArxivTool.__call__` still emits `SearchResult` (source="arxiv.org")
  for the graph evidence pipeline; `ArxivArgs` gained `limit`.
- Paper prioritization: `app/agents/nodes/search.py::needs_papers(state)` → true for
  ACADEMIC_RESEARCH intent or when `arxiv` is in `requires_tools`. `search_web` runs `search_arxiv`
  on the main query alongside the web searches and stable-sorts arXiv hits FIRST so the research
  loop privileges papers. Skipped in demo/mock (fully offline tests).
- Classifier: new `_TECH_WORDS` (transformer, LLM/LLMs, RAG, retrieval-augmented, vector database,
  agentic, multi-agent, NLP, neural network, diffusion model, fine-tun, semantic search, survey of,
  knowledge graph, ...) with `_TECH_EXCLUDE` ("best/which/latest/vs/how to/price/worth it/news").
  Technical queries → ACADEMIC_RESEARCH with `["arxiv","web_search","fetch_webpage"]`; LLM `_SYSTEM`
  now instructs arxiv-first for AI/ML/CS surveys/architectures/state-of-the-art.
- Docs: JSON spec shape live-verified via export.arxiv.org — intelligent agent query returned
  TITLE/authors/published/url + abstract; chat smoke of "Survey of agentic AI architectures" →
  ACADEMIC_RESEARCH, all four cited sources are arxiv.org papers (papers prioritized).
- Document RAG audit: the Upload→Parse→Clean→Chunk→Embed→FAISS→Retriever pipeline already existed
  for PDF/TXT/Markdown/DOCX/CSV (`app/rag/ingestion.py`, `fastembed` LocalEmbeddings w/
  MockEmbeddings demo fallback, `app.rag.vectorstore` FaissVectorStore, `app/api/documents.py`,
  `document_search` tool + DOCUMENT_QA intent). Audited + fixed two latent bugs (#4, #5 above) and
  added coverage: `tests/test_document_rag.py` uploads+retrieves ALL five formats via the public
  upload API (hand-rolled minimal PDF, python-docx DOCX, markdown, csv, txt) and an end-to-end
  `document_search` tool call.
- Test suite: 205 passed (was 174). conftest now wipes /tmp/chatgpt-test-data + db per session so the
  FAISS index can't accumulate garbage across runs.

## Local vector search + swappable embeddings, time/date, units, decisions (added Sep 25)
- **Item 16 — embedding abstraction (provider agnostic)**:
  `app/rag/embeddings.py::EmbeddingProvider` → `LocalEmbeddings` (fastembed ONNX, free/offline,
  384-d, real-mode default), `RemoteEmbeddings` (OpenAI-compatible `POST /embeddings` via httpx —
  base_url/Authorization Bearer, `{model, input}` payload, maps `data[].embedding` back in order),
  `MockEmbeddings` (deterministic demo). `get_embeddings()` dispatches on `settings.embedding_provider`
  (`local` | `remote` | `mock`), with graceful fallback → MockEmbeddings on any provider failure, and
  `reset_embeddings()` to re-read config. New settings: `embedding_remote_url`,
  `embedding_remote_model` (default text-embedding-3-small), `embedding_remote_api_key`.
- **Item 17 — time/date from the server clock** (never the LLM's knowledge): both
  `get_current_time(timezone)` and now **`get_current_date`** are registered tools (IANA tz via
  `zoneinfo`; unknown tz → ValueError). New DATETIME intent + `_TIME_WORDS` heuristic in the
  classifier (`_TIME_EXCLUDE` filters "how much time/time it takes/..." so durations don't route
  here); router routes DATETIME → new graph node `app/agents/nodes/datetime.py` which extracts the
  timezone (city map + abbreviations + IANA regex, default UTC), calls both tools, and stores
  `datetime_result` in state; synthesize injects `CURRENT DATE/TIME (from the server clock — trust
  this, do not guess)` into the prompt. Classifier `DecisionIntent` enum updated.
- **Item 18 — unit conversion**: already met by `app/tools/units.py` (pint, lazy UnitRegistry,
  units: length/mass/temp/area/volume/speed/time/data/energy/pressure). Added `tests/test_units.py`
  — one parametrized conversion per category + invalid-unit/dimensionality errors + registry smoke.
- **Item 19 — decision analysis**: `build_decision_framework` now emits the full spec shape:
  decision / options / criteria / constraints / evidence / trade_offs / risks / unknowns /
  conditional_recommendations ("If {X} is your priority → which option, and why") / recommendation
  rationale / instruction (never name a single winner; per-priority conditional recs; be honest when
  evidence is thin). `DecisionTool`/`ComparisonTool` changed from `__call__(args: Args)` to
  kwarg-based `__call__(...)` so `run_tool` can invoke them like every other tool. The graph now
  builds `reasoning_scaffold` in `analyze_evidence` when intent is DECISION_ANALYSIS (or COMPARISON):
  calls the tool, attaches the gathered evidence, stores JSON in state → synthesize emits it as
  REASONING FRAMEWORK.
- Live smokes (real Groq, demo OFF): "What is today date?" → DATETIME, answer = server-clock
  "Friday, September 25, 2026"; "What time is it in Karachi?" → correct tz'd local time;
  "Convert 100 celsius to fahrenheit" → 212 °F; "Should I use PostgreSQL or MongoDB...?" →
  DECISION_ANALYSIS with honest per-priority conditional table, no blind single winner.
- Backend restarted on :8100 (double-fork, pid 1117311); /api/health shows tool_count 13 (+get_current_date).
- Test suite: **174 passed** (was 134). New: tests/test_embeddings.py, test_datetime.py, test_units.py,
  test_decision.py.

## Structured comparison, map-reduce summarization, citations UI + quality, intents/router (added Sep 25)
- **20 — Comparison tool**: `app/tools/comparison.py` now parses options from "Compare A vs B vs C"
  (`parse_options()`, non-capturing `vs|versus` split, lead-in words stripped) and produces the
  canonical scaffold: rows = architecture, performance, ecosystem, learning curve, production use,
  community, best use case (Community added), columns = one per option. Instruction forces every
  time-sensitive claim to cite the NUMBERED SOURCES and marks unverifiable cells "Not verified".
  `analyze_evidence` now feeds the parsed options into the scaffold. Live: "Compare LangChain vs
  LlamaIndex vs Haystack" → full markdown table with per-cell [1][2][3] citations + Sources.
- **21 — Map-reduce summarization**: `app/services/summarizer.py` — short inputs (<=6000 chars) are
  summarized directly; long inputs are chunked (4000/300, RecursiveCharacterTextSplitter) → parallel
  map with bounded concurrency → reduce into a single lead + bullets. Never dumps the full source to
  the model. New `summarizer` tool (registry, kind: webpage/pdf/document/search_results/
  conversation/research_collection) + `app/agents/nodes/summarize.py` graph node that gathers text
  from uploaded documents (retrieved chunks), a URL in the query (fetch_webpage → adds citable
  Evidence for a Sources card), this conversation's history, or gathered research evidence, stores
  `state.summary`, and synthesize presents only that summary. Router: SUMMARIZATION w/ docs →
  retrieve_documents → (conditional `after_retrieve`) → summarize; URL/conversation → summarize;
  else → research loop. Live: math-docs URL summarized with one clickable source; 2-turn
  conversation summarized.
- **22 — Citations**: backend pipeline (build_citations/dedupe/rank/validate_markers/attach sources)
  confirmed — never invent, only retrieved sources. Frontend `markdown.tsx` now clickable:
  inline `[1]` / `[1,3]` / `[2-4]` citation markers become links to the matching source (code
  spans/fences excluded), and bare URLs are autolinked. Sources prop threaded through Message +
  ChatWindow (streaming too).
- **23 — Source quality**: 10-tier taxonomy (official documentation, primary research, government,
  academic, major publication, company documentation, technical blog, wikipedia, community
  discussion, unknown) with matching authority labels + ranking; added explicit "company
  documentation" inference (support/kb hubs). Preference order (official docs → research → primary →
  established → secondary) enforced in cite ranking + synthesize SOURCE AUTHORITY prompt.
- **24/25 — Classifier + smart router**: new DATA_ANALYSIS heuristic (`_DATA_WORDS`: "analyze this
  csv/data/file", stats/plot/chart...) → intent + `["document_search","python_analysis"]`; router
  routes DATA_ANALYSIS → retrieve_documents; MULTI_TOOL stays in the research family. Acceptance
  tests cover the spec examples: What is Python? → direct (background); Python's latest version →
  web/current; Calculate 27×391 → calculator; Compare FastAPI/Flask → web research + comparison;
  Summarize this PDF → document RAG; Research latest LangGraph architecture → deep research;
  Analyze this CSV → data analysis.
- Backend restarted (:8100, double-fork, pid 1192132); /api/health tool_count=14 (+summarizer).
- Test suite: **205 passed** (was 174). New: tests/test_comparison.py, test_summarizer.py,
  test_intents_router.py; citations tests extended. Frontend `npm run build` clean.

## Multi-tool agent + async orchestration (item 26, added Sep 25)
- **Prompt spec**: "implement the multi-tool agent: sequence and parallel group tools — including
  grouping independent tools in the same parallel group, then run a reasoning step over the
  results." Matches the research loop's existing asyncio scheduling; item 26 adds an explicit
  combined-tools group for computation + comparison on the same query.
- **`run_tool_plan(steps)`** in `app/agents/nodes/helpers.py`: sequential list of steps; each step
  may be a **list of independent tool dicts → `asyncio.gather` parallel group**. Returns
  `[ToolCallResult(name, status, payload)]`. `_call_one` extracts kwargs (drops `name`) and goes
  through `run_tool` (SSE + error capture) so every run shows in the activity feed.
- **`app/agents/nodes/multi_tool.py`** — `multi_tool_answer(state)`: extracts independent
  `X% of Y` calculator steps straight from the text (regex), else falls back to one
  `plan_calculation` plan (math/formula/units → calculator/unit_converter); builds a `comparison`
  scaffold when `parse_options` yields ≥2 *named* (non-numeric) options and none exists yet; runs
  the whole set as a **single parallel group**; stores `calculation` + `calculations` (all results),
  `reasoning_scaffold` (with gathered web evidence attached), and the successful `tool_runs`.
- **Classifier**: new MULTI_TOOL intent (already in enum) produced by `_is_calcish` (is_calc / % /
  calculate / compute / strong-calc words) AND `_MULTI_FRAMING` (compare/vs/which should/research/
  cost/price of...), checked FIRST so "Calculate X and compare with Y" is not swallowed by CALCULATION.
  `_multi_needs_web` = false only for pure number-vs-number arithmetic combos; named product options
  or research/decision framing `needs_web`. LLM `_SYSTEM` also documents MULTI_TOOL. `_heuristic`
  pure-calc and pure-compare paths still return CALCULATION / COMPARISON unchanged.
- **Router**: MULTI_TOOL → `"multi_tool"` when `needs_web==False`, else `"plan"` (research loop).
- **Graph**: new `multi_tool` node; classify conditional map + `evidence_sufficient` return
  `"multi_tool"` when INTENT==MULTI_TOOL and evidence ready; `multi_tool → synthesize`. Synthesize
  injects ADDITIONAL CALCULATION RESULTS for the extra calcs; `_fallback_answer` now lists all calcs
  if the model is unavailable.
- **Tests**: `tests/test_multi_tool.py` (12) — pct extraction, run_tool_plan parallel/error,
  node runs both calcs, classifier offline/web/named-option cases, router split, evidence_sufficient,
  compute-only + web graph end-to-end. `test_intents_router.py` updated: MULTI routing depends on
  needs_web. **Suite 217 passed (was 205).**
- **Live smokes** (real Groq, demo OFF): "Calculate 20% of 800 and compare with 15% of 900" →
  INTENT MULTI_TOOL, two parallel calculator `tool_runs`, Groq synthesized "20% of 800 (160) is 25
  greater than 15% of 900 (135)"; "Compare FastAPI vs Flask ... calculate 15% of 500 daily requests"
  → MULTI_TOOL, research loop (6 web_search + 12 fetch_webpage) then calculator + comparison group,
  4 cited sources, table + 75 requests.
- Backend restarted (:8100, double-fork, pid 1211655); /api/health tool_count=14, demo_mode:false.

## FAST RESPONSE ARCHITECTURE (latency overhaul)
User directive: async FastAPI, async HTTP, streaming, parallel search, connection reuse, caching,
result dedup, bounded concurrency, lightweight routing, Groq inference, token streaming; simple
questions answer almost immediately; research streams safe progress statuses (no chain-of-thought).
- **Shared HTTP client** (`app/services/http.py`, NEW): one `httpx.AsyncClient` (`Limits(max_connections=32,
  max_keepalive_connections=32)`, timeout 15/connect 5, Chrome UA) reused by every provider/tool
  (DDG tiers `_scrape`/`_wiki`, Tavily/Brave/Serper/Google, `webpage._download` w/ per-request
  `follow_redirects=False` replaying the SSRF-safe redirect loop). `close_http_client()` at shutdown.
- **Fast-tier search** (`search.py`): DDG runs html/lite/ddgs/wiki tiers concurrently and returns on
  FIRST_SUCCESS streak (18s deadline, cancels siblings) instead of waiting for all via `asyncio.gather`.
- **Fast-path classifier** (`classifier.py`): `_fast_classify` (deterministic, NO LLM) intercepts
  every known intent before the LLM — ordering: no_web override → MULTI_TOOL → **exact-arithmetic
  (`_is_calc`) now beats the background/wiki frame** (fixes "what is 15% of 500" → CALCULATION) →
  background → calc/units/time → compare/decision → academic/summary/docs → deep/current/code →
  research/mode-web → trivial smalltalk ("hi/hello/thanks/who are you/…") → GENERAL_CHAT; ambiguous
  queries (None) still go to the LLM classifier, then `_heuristic` (identical demo behavior). Greetings
  skip ALL LLM calls (live stream: status Understanding→Generating, zero tools, ~1s first token).
- **Planner LLM gate** (`plan.py`): `_needs_llm_plan` allows the LLM query-decomposer ONLY for
  DEEP_RESEARCH/ACADEMIC/high-complexity/gap-fill iterations; low/medium research uses deterministic
  queries (`[q, f"{q} 2026"]`, iterations→`comparison/limitations`). Saves one Groq round-trip per query.
- **Bounded concurrency + labels**: `fetch_sources` `asyncio.Semaphore(4)`; statuses aligned to spec —
  "Searching the web…" → "Found N…" → "Reading top N…" → "Analyzing N sources…" → "Comparing
  evidence…" (scaffold builds) → "Gathering additional evidence…" (loop) → "Generating your answer…".
- **Cache fixes + reply cache**: `InMemoryCache` TTL now honored (`_expired` always-False bug) and
  capacity-bounded (evict-oldest FIFO); `RedisCache` switched to blocking client (interface is sync).
  New reply-level cache (`chat.py`) serves repeated **deterministic CALCULATION** queries instantly
  (gated: no docs, no deep/web mode; keyed on normalized query+mode, ttl 1800) — live: 0.89s cold →
  0.50s cached. Never caches datetime/research/personal queries.
- **Streaming fallback fix**: `synthesize._fallback` emitted a fatal `error` event that made
  `gen()` cancel the task before the best-effort answer was persisted → switched to a `status`
  event so the fallback still streams `message_complete`.
- **Crash fix (lxml segfault)**: concurrent `trafilatura.extract` (lxml) in 4 fetch threads
  segfaulted the whole uvicorn process (`etree.cpython-312...so`). Extraction now serialized under an
  `asyncio.Lock` — stable across repeated multi-fetch research streams.
- **Tests**: `tests/test_fast_path.py` (10) — trivial-smalltalk fast path, deterministic intents,
  ambiguous→None, mode override, planner `_needs_llm_plan` gate + deterministic fallback + LLM-used
  path, InMemoryCache TTL/eviction, reply-cache key normalization, and endpoint-level reply-cache
  (FakeGraph: second identical calc never re-runs the graph). **Suite 227 passed (was 217).**
- **Live smoke** (real Groq, demo OFF): greeting instant (GENERAL_CHAT, zero tools, 2.7s end-to-end
  incl. new-conversation title gen); "what is 15% of 500" → CALCULATION (0.89s/0.50s w/ reply cache);
  "Compare Flask vs FastAPI for async workloads" streamed full status chain to COMPLETE with 4 cited
  sources, no crash (DDG blocked → honest "couldn't fully verify" fallback, per spec honesty).
- Backend restarted (:8100, double-fork, pid 1232941); /api/health ok, tool_count=14, demo_mode:false.

## Server-side streaming (SSE event contract)
POST `/api/chat/stream` (SSE; EventSource can't POST, so `api.ts` uses fetch+ReadableStream).
Event contract aligned to spec: `message_start` → (`status`/`tool_start`/`tool_result`/`source_found`) →
`message_delta` (token chunks) → `message_complete` → `done` (+ `error`). Extras kept for the UI:
`status` (safe progress labels), `sources`/`sources_found` (batch source list + count).
- **`message_start`** (NEW): first frame of every stream (normal + reply-cache paths), carries
  `conversation_id`, `user_message_id`, `role`. Frontend `onStart` resets streaming buffers.
- **`source_found`** (NEW): one frame per confirmed source (`source_id/title/url/snippet/quality/
  category`) emitted by evidence gather + wikipedia nodes (`stream.emit_source`); frontend appends
  deduped-by-`source_id`, so source chips appear progressively while tokens stream.
- **Progressive tokens**: synthesize streams model chunks via `message_delta`; frontend appends to
  `streamingText` live. Cached replies also stream delta chunks.
- **Stream-path reply-cache store**: `_store_reply_cache` now also runs after a successful graph
  run in `_run_workflow` (was sync-`chat` only), so the UI's streaming reuse of a deterministic
  CALCULATION is instant when `ENABLE_CACHE=true` (live `.env` has it `false` by design).
- Verified live: "What is LangGraph?" streamed `message_start` → 2 statuses → 3 tool runs → 5×
  `source_found` → `sources` → 286 `message_delta` → `message_complete` → `done` (6s total).
  Frontend `npm run build` clean; **Suite 227 passed.**

## Conversation memory + context management
Per-conversation memory with explicit `metadata`, rolling summaries, and searchable history —
**never** sending a whole long conversation to the model.
- **Schema**: `Conversation` gained `metadata_json` (JSON) + `summary` (Text), exposed as
  `metadata`/`summary/message_count/last_message` on `ConversationOut`. Existing DBs upgraded
  idempotently by `_auto_migrate()` (lightweight ALTER in `base.py`; `init_db` calls it after
  `create_all`). Verified on the live `data/app.db` (columns present via `PRAGMA table_info`).
- **API**: `GET /api/conversations/search?q=` searches across titles AND message contents with a
  snippet window + `snippet_role`, deduped per conversation (LEFT JOIN so title-only matches with
  zero messages still surface). `PATCH /api/conversations/{id}` now accepts `title` and/or
  `metadata` (merged, not replaced) and/or `summary`. `/search` is declared BEFORE `/{id}` in the
  router so "search" never matches a conversation id.
- **Rolling summaries**: `app/services/conversation_summary.py`. `schedule_refresh` fires when a
  conversation hits its first milestone (6 msgs) then every 5 (`conversation_summary_min` /
  `conversation_summary_every` settings); the LLM folds the last 8 turns + previous summary into a
  compact running summary via `revise_summary`, persisted to `Conversation.summary`. Runs
  fire-and-forget in its own session/task (never delays the stream; keeps prior summary on failure).
- **Context management**: `_prepare` loads `conv.summary` into `state.conversation_summary`;
  `_build_messages` injects it as a "CONVERSATION SO FAR" block and shrinks the verbatim recent-window
  (budget 8000→4200, tail 40→6) when a summary exists. Combined with the existing char-budget window
  + long-term user memory, old content is condensed, never blindly replayed.
- **Frontend**: sidebar search box now calls the server endpoint (debounced 250ms) and renders hits
  with snippet subtitles; empty query shows the normal grouped list. `api.ts` `searchConversations`,
  store `searchChats`/`searchResults`, `Conversation.type` extended.
- **Tests**: `tests/test_conversation_memory.py` (11) — metadata merge/roundtrip + corrupt-JSON
  tolerance, stats count/last, search by title/content/no-hit/user-scoping, `_auto_migrate` on a
  legacy table, summary due-gating, API PATCH metadata merge + summary, `/search` endpoint, and
  `_build_messages` summary injection + window shrink. **Suite 238 passed (was 227).**
- **Live smoke** (real Groq): conv PATCH metadata merge (pinned true→false merge w/ device), search
  by content ("capital of France" → Paris snippet w/ role assistant) and by title ("Smoke test", no
  snippet but hit surfaces); multi-turn continuation "Which continent is that country in?" →
  "France is located in **Europe**." (history+summary context). One transient Groq 429 fell back to
  the LLM-unreachable answer; retry succeeded. Backend restarted (:8100, setuid/nohup, pid 1267058);
  health ok, tool_count=14, demo_mode:false. SSE contract intact after changes (stream re-checked).

## Bug history
1. BLACK SCREEN: store status handler built ToolRun with `tool: s.status` but backend `status` event has
   `message` key → `tool` undefined → `ToolActivity` `r.tool.replace()` crashed → React unmounted root.
   FIXED: safe mapping in store + ToolActivity guards + ErrorBoundary everywhere + full message_complete payload.
2. Groq 404: `llama-3.3-70b-versatile` not accessible on this account. Account models: openai/gpt-oss-120b,
   openai/gpt-oss-20b, qwen/qwen3.8-27b, allam-2-7b, whisper, prompt-guard.
   → Default GROQ_MODEL=qwen/qwen3.8-27b (273ms, clean output). Verified via API test.
3. DDG flaky: ddgs 9.16 'auto' backend shuffled google/brave/etc which fail here.
   → backend="duckduckgo" pinned (only engine that works; 1.0s, real results). DDGS(timeout=12).
4. FAISS DELETE DEADLOCK: `FaissVectorStore.remove_document` held `self._lock` then called
   `self.add(...)` which re-acquires the same non-reentrant `threading.Lock` → hung forever on
   any document delete while other documents remain. FIXED: split out `_add_locked(ids, vectors)`;
   `remove_document` rebuild path calls `_add_locked` (lock already held). Live delete now 204.
5. RAG RETRIEVAL ALWAYS EMPTY: `crud.save_chunks` generated a fresh `chunk_id` per chunk and ignored
   `meta["chunk_id"]`, while the vector store registers `document_id::chunk_id`. `retrieve` looked up
   `db.get(DocumentChunk, store_chunk_id)` → not found → every RAG query returned []. FIXED:
   `save_chunks` now uses `meta["chunk_id"]` as the DocumentChunk primary key (falls back to new_id).
   Verified live: DOCUMENT_QA cites the retrieved chunk.
6. LXML SEGFAULT KILLED BACKEND: 4 concurrent `fetch_webpage` → `trafilatura.extract` (lxml) ran on
   several threads at once → `etree.cpython-312...so` segfault took down the whole uvicorn process
   mid-stream (no done/error emitted, listener died). FIXED: serialize the C-lib extraction with a
   module-level `asyncio.Lock` in `WebpageTool._trafilatura_lock()`. Verified stable across repeated
   multi-fetch streams.

## Remaining ideas (not queued unless asked)
- Improve synthesize prompt quality further if desired.
- Runtime config UI (model/search provider toggles) — earlier suggestion, user redirected to real searches.

## EVIDENCE MODEL + HALLUCINATION REDUCTION (spec §43, Sep 25)
- `Evidence` model (`app/models/schemas.py:49`) already matched spec EXACTLY (source_id, title, url,
  snippet, relevant_text, source_type="web", confidence float 0.5 + source_quality bonus); synthesizer
  already builds from `state["evidence"]` not raw results. Real gap = claim-verification stage.
- NEW `app/services/claim_check.py`: `should_verify()` gate (web-type intent AND evidence present AND no
  `[n]` markers in the answer AND not already declaring insufficiency) + `verify_and_revise()` (single LLM
  rewrite via `_PROMPT | llm | JsonOutputParser`, temperature 0, `max_tokens=600`, `call_with_retry(2)`;
  keeps original on failure/empty/balloon >max(len*2,4000); NEVER raises). Exact phrase
  `CANT_VERIFY = "I couldn't verify this from the available sources."` (NOT "fully verify").
- `_MARKER_RE = \[\d+(?:[-,]\d+)*\]` — NO internal whitespace, so `[1, 2]` (code-looking text) does NOT
  count as a citation marker; `[1,3]`/`[2-4]` do.
- Wired into `synthesize_answer` (guard False): `settings.enable_claim_verification` && `should_verify()`
  → `emit_status("Verifying claims…")` + `verify_and_revise()` BEFORE `_finalize` (so sources section still
  attaches). Config flag `enable_claim_verification: bool = True` in `app/core/config.py`; tests disable via
  conftest `ENABLE_CLAIM_VERIFICATION=false`; `.env.example` has `ENABLE_CLAIM_VERIFICATION=true`.
- Prompt wording aligned everywhere to the exact sentence (`_SYSTEM_HEAD` grounding rules, `_HEAD_NO_LIVE`
  "state plainly", `_degrade_dynamic_answer` now opens with the sentence instead of "## I couldn't verify this live").
- NEW `tests/test_claim_check.py` (12 tests): Evidence spec fields+defaults, should_verify trigger matrix,
  already_declares path, verify_and_revise returns revision / keeps original on parse-fail & balloon,
  empty-evidence noop, + 2 synthesize wiring tests (recorded call when enabled, skipped when disabled).
- Suite: **278 passed**. Backend restarted pid 1381491 (health OK). Live check: gated skip fired correctly —
  a query with irrelevant evidence produced the exact `CANT_VERIFY` sentence instead of inventing facts, so
  the revision pass rightly did NOT re-fire (already declared); 3 followups streamed. Test conversations cleaned.

## RESEARCH DEPTH + USER-CONTROLLED WEB SEARCH + FILE UPLOAD UX (spec §§44-46, Sep 25)
- **Modes** were already plumbed end-to-end (`ChatMode = auto|web|no_web|deep`; chat.py threads them; classifier
  `force_mode`; select_tools respects web/deep/no_web). MISSING was a UI selector → added.
- **RESEARCH DEPTH**: NEW `app/agents/depth.py` — `research_depth(intent, complexity, mode)` picks
  quick|standard|deep and `depth_budget(state)` returns bounded per-tier limits. New config knobs
  (config.py + .env.example): `research_quick_max_iterations/sources/searches` (1/3/3 → 1-3 searches),
  `research_standard_max_searches` (6 → 3-6), `research_deep_max_iterations(3)/sources(8)/
  first_queries(4)/followup_queries(3)` (decomposition + multiple searches + loops via the existing
  plan→search→fetch→analyze→plan cycle). Depth set in classify node, stored in state (`depth` key),
  used by plan's `_query_count` (replaces hardcoded 4/2/3) and evidence's loop-stop
  (`evidence_ready` = ranked≥max_sources ∨ iterations≥max_iterations, per tier).
- **WEB SEARCH control**: in `mode==web`, CODE/GENERAL_CHAT now upgrade to WEB_SEARCH (web search
  required); CALCULATION/DATETIME stay deterministic offline; no_web unchanged. Deep forces
  DEEP_RESEARCH except DOCUMENT_QA/CALCULATION/DATETIME. `research_depth` treats deep-mode offline
  intents as standard.
- **FILE UPLOAD UX**: backend already accepted PDF/DOCX/TXT/MD/CSV (security ALLOWED_UPLOAD_EXTENSIONS,
  ingestion `_parse_csv`, magic-byte sniffing, 20MB cap). Frontend Composer was missing CSV + had a
  dead `pending` state. Rewrote: 📎 Paperclip attach (was Plus), accept `.txt,.md,.pdf,.docx,.csv`,
  real in-flight chips showing filenames with type icons (FileSpreadsheet for csv/md, FileText else),
  updated error text ("Only PDF, DOCX, TXT, MD, CSV are supported."), send blocked while uploading.
  Classifier now favors uploaded files over generic academic wording: `_references_doc` expanded with
  "methodology used in this paper"; "this paper/document/file" queries skip the ACADEMIC_RESEARCH
  branch when docs are attached → DOCUMENT_QA; "Summarize this." still SUMMARIZATION (summary branch
  kept above doc branch).
- **Tests**: NEW `tests/test_research_depth.py` (18): depth matrix, per-tier budget bounds (monkeypatched
  settings), query counts (quick single small pass / deep decomposes+loops / standard 3-6), classify
  wiring (web→WEB_SEARCH, deep→DEEP_RESEARCH + offline exempt, no_web→GENERAL_CHAT), evidence loop stop
  per tier, doc classifier phrases. Suite **293 passed**. Backend restarted pid 1392902.
- **Live check**: `Survey of agentic AI architecture patterns` in `mode=deep` produced a 4005-char cited
  survey; log shows a follow-up decomposition query ("comparative analysis of agentic AI frameworks")
  → additional search loops ran. DDG flakiness intermittently errors some queries (pre-existing). Frontend
  build clean. Test conversation removed.

## DOCUMENT RAG ARCHITECTURE (spec §47 items, Sep 25)
- **Audit**: pipeline stages 1-7 + 9-10 already existed — upload API (`documents.py`) → `parse_file`
  (pdf/docx/csv/txt-md) → `_clean_text` → RecursiveCharacterTextSplitter (chunk 1200 / overlap 150) →
  `get_embeddings()` (local/remote/mock) → `FaissVectorStore` (IndexFlatIP on L2-normalized vectors) →
  `retrieve()` → doc context in `_build_context` → LLM. Gaps closed below.
- **Metadata**: ingestion persists `meta_json` per chunk with `document_id, filename, page, chunk_id,
  source`; `source` = the filename stem (`name.pdf` → `name`). `RetrievedChunk` schema gained
  `filename` + `source`; `retriever.py` now routes candidates through metadata-aware structs.
- **Optional reranking (stage 8)**: NEW `app/rag/rerank.py` — `Reranker` ABC per spec (provider-agnostic),
  `NoopReranker` (identity), `HybridReranker` (deterministic offline blend 0.7·cosine + 0.3·lexical
  char-trigram overlap → no model/network). Config-gated: `enable_reranking` (bool) + `rerank_mode`
  (`none|hybrid`) in config.py + .env.example; `get_reranker()` returns None when disabled/none (cached
  via `_RESOLVED` sentinel), `reset_reranker()` for tests. `retriever.py` collects a candidate pool →
  reranks → slices top-k.
- **Citations reference the document + page/chunk**: `citations.py` — `build_document_citations(retrieved)`
  emits numbered `Citation(index, url="", title=filename, source="document", quality="user document",
  page, chunk_id)`; `Citation` model gained `page`/`chunk_id` (defaults keep web citations compatible);
  `build_citation_block` renders doc entries as `[n] title / document · chunk <last6> / Type: uploaded
  file (your content)`. `_build_context` now numbers web evidence and doc chunks in ONE continuous
  `[1..n]` sequence (`DOCUMENT CONTEXT` labeled with the matching source numbers); `_finalize` builds
  web + doc citations combined so `validate_markers(max_index)` and `attach_sources_section` cover both.
- **Tests**: NEW `tests/test_doc_rag_spec.py` (9): retrieved-chunk metadata (document_id/title/page/
  chunk_id/filename/source), persisted chunk meta_json keys, reranker None-when-disabled, pure-lexical
  promotes the lexical hit, empty-query ordering, document citation fields, rendered doc-location block,
  source_label "user document". Fixed a latent NameError in test_graph.py (build_document_citations was
  used but not imported). Suite **302 passed**. Backend restarted pid 1409129.
- **Live check** (`mode=no_web`): uploaded `rag_test.md` → "Per this document, how does the pipeline
  handle uploaded files?" answered with `[1]` citation and a numbered Sources block
  `[1] rag_test.md / document · chunk 29cb1e / Type: uploaded file (your content)`. Doc + conversation
  cleaned up afterwards.

## CACHING + DB + API SURFACE + HEALTH (spec §§48-51, Sep 25)
- **§48 CACHING — abstraction already existed**: `CacheBackend` / `InMemoryCache` / `RedisCache`
  (app/services/cache.py), local runs WITHOUT Redis (redis_url empty → InMemory; `ENABLE_CACHE` gates all).
  Cache-use audit: identical search queries (tools/web_search.py) + fetched webpages (tools/webpage.py) +
  idempotent replies (chat.py) were already wired. This round closed the remaining categories:
  - **Embeddings**: NEW `CachedEmbeddingProvider` (app/rag/embeddings.py) wraps the built provider when
    `enable_cache`; memoizes `embed_query`/`embed_documents` per text (per-text keys), transparent no-op
    when cache disabled. `get_embeddings()` still returns plain MockEmbeddings under demo/tests
    (conftest sets `ENABLE_CACHE=false`) so existing isinstance checks pass.
  - **Document processing**: `parse_file_cached(filename, content)` (app/rag/ingestion.py) — parse output
    cached by sha256(content)+filename (TTL 1h); stored JSON-safe `[[page,text],…]`, revived via `_as_pages`.
    `ingest_bytes` now uses it. Re-uploading identical bytes skips PDF/DOCX/CSV parsing.
  - **Repeated calculations**: calculate node (app/agents/nodes/calculate.py) caches the final result per
    normalized query (`cache_key("calc", …)`, TTL 1d) when `calc is not None`; hit re-hydrates a
    CalculationResult and skips the tool + LLM plan entirely (helper `_calc_cache_get`/`_calc_cache_put`).
  - **Redis-safety hardening**: `RedisCache` now JSON-encodes/decodes values (`s:` raw-string vs `j:` json;
    `_FriendlyEncoder` handles pydantic models/datetime/bytes); `cached_call` gained an optional `decode`
    reviver and the two tools pass idempotent revivers (`_revive_search_results` / `_revive_page`) so
    Redis plain-dict results become models. `.env.example` carries `ENABLE_CACHE` / `REDIS_URL` (already).
    Added `reset_cache()` (mirrors reset_reranker) so tests can flip settings between sessions.
- **§49 DATABASE — already compliant**: SQLite locally via SQLAlchemy; ALL spec tables exist in
  app/db/models.py — users, conversations, messages, sources, documents, document_chunks, tool_runs,
  feedback (plus user_memory long-term). PostgreSQL-compatible typing (String/Text/Integer with sized
  columns, `DateTime(timezone=True)`, FKs, cascade delete-orphan, UniqueConstraint on user_memory).
- **§50 API DESIGN — already compliant**: POST /api/chat, POST /api/chat/stream, GET/POST /api/conversations,
  GET/PATCH/DELETE /api/conversations/{id} (+ /search, /messages), POST /api/search, POST /api/research,
  POST /api/documents/upload, GET /api/documents (+ DELETE/{id}), POST /api/feedback, GET /api/health —
  all Pydantic request/response models.
- **§51 HEALTH** — matches (~) the spec exactly: `{"status","llm","web_search","calculator","document_rag",
  "database"}` with current provider/flags (status "ok"/"degraded" on DB ping). Extra harmless keys
  (tool_count, demo_mode) are kept; no secrets exposed. Live: `{"status":"ok","llm":"groq","web_search":
  true,"calculator":true,"document_rag":true,"tool_count":14,"database":"sqlite","demo_mode":false}`.
- **Tests**: NEW `tests/test_cache_spec.py` (12): string/number/dict round-trip through Redis _pack/_unpack
  (no server needed), pydantic-model encoding, cached_call hit + decode reviver, local default is
  InMemoryCache, identical calc query runs the tool ONCE (and 2nd is served from cache with empty tool_runs),
  parse_file_cached parses once, embeddings embed_query/documents call the inner provider once per unique text,
  cache_key normalization. Note: unit tests that invoke tools must `import app.tools.registry` or the registry
  is empty ("tool disabled"). Suite **314 passed**. Backend restarted pid 1421596.
- **Live check**: `Calculate 17.5% of 85000` twice — identical answer, latency 2.75s → 1.54s (cache hits).
  Test conversations cleaned up. No frontend changes required.

## RESPONSIVE DESIGN (desktop/laptop/tablet/mobile, Sep 26)
- Spec: Desktop → sidebar + conversation; Mobile → drawer sidebar, full-width conversation, bottom composer.
  The existing layout already had a bottom composer (Composer is the last child of the `main` flex-column) and
  a max-w-3xl centered conversation column; the gap was the sidebar, which was a fixed `w-64` that squeezed
  small screens.
- **One `Sidebar` component, two variants** (new optional props `open`/`onClose`):
  - Desktop/tablet (`md:` and up): unchanged static `hidden md:flex w-64` sidebar.
  - Mobile (`< md`): `fixed inset-y-0 left-0 w-[280px] max-w-[85vw]` overlay drawer, `-translate-x-full`
    when closed with a 200ms ease-out slide (Tailwind v4 default breakpoints; md = 768px).
- **Drawer state lives in the zustand store**: `sidebarOpen` + `openSidebar()` / `closeSidebar()`. Auto-close
  on `selectConversation`, `newConversation`, `clearAllConversations` (store-level) so mobile always lands in
  the full-width conversation. Adding a chat never re-opens the drawer.
- **Backdrop**: `fixed inset-0 z-50 bg-black/50 md:hidden` rendered in App when open (drawer z-[60] sits above);
  click-to-close + Escape-to-close (App keydown handler).
- **Mobile menu buttons** (`md:hidden`, `Menu` icon from lucide): added to the conversation header (left of the
  title, which is now `truncate`-safe) and an absolutely-positioned top-left button on the new-chat empty state.
  The drawer also gained an `X` close button next to the theme toggle.
- **Verification**: `npm run build` clean (chunk-size warning pre-existing). Vite dev on :5200 hot-reloads —
  no backend touch needed. Confirmed via classes: `md:` static sidebar + conversation, `< md` overlay drawer +
  full-width conversation + bottom composer.
## TOOL ACTIVITY UI (high-level research status, Sep 26)
- Spec: while researching, show only compact high-level phases — `🔎 Searching the web`, `✓ 6 sources found`,
  `🌐 Reading sources`, `✓ 4 sources analyzed`, `🧠 Synthesizing findings`. No chain-of-thought, no raw tool logs.
- **Phase model** (`ResearchPhase` in types): `['searching', 'reading', 'synthesizing']`, each `active | done`
  with an optional count. Only the LAST phase pulses; completed earlier phases flatten to a `✔ N ...` line.
- **Store reducers** (`useChat.ts`): `applyStatus(status)` collapses raw status strings into phases
  ("Searching the web (N queries)…"→searching, "Reading top N sources…"/"Analyzing…"→reading,
  "Generating your answer…"/"Synthesizing…"→synthesizing); `onActivityToolStart`/`onActivityToolResult` fold
  tool events into the SAME phases (web_search/wiki/arxiv → searching; fetch_webpage → reading; reading count
  bumps on fetch success) so tool granularity is never exposed. Document-statuses ("Searching your documents…")
  and non-research statuses (Calculating…, Verifying claims…, Planning…) bypass the phases and keep the plain
  status line. Counts: searching = unique `source_found` events; reading = successful webpage fetches.
- **`ResearchActivity` component** (new): renders the compact feed — active rows show emoji icon 🎯/🌐/🧠
  (per spec symbols 🔎/🌐/🧠 map to searching/reading/synthesizing) + label + pulse dots; done rows show
  `✔ N sources found` / `✔ N sources analyzed`. Uses `text-[13px]`, success/secondary color.
- **ChatWindow**: `activity` replaces the plain status line while phases exist (fallback "Thinking…" preserved);
  the underlying `streamingStatus` is shown as a small muted line below when phases are active so detail like
  "Verifying claims…" is not lost. Activity resets on each send and clears on done/stop.
- **Verification**: `npm run build` clean (chunk-size warning pre-existing). Vite hot-reloads; no backend change.

## RESPONSE FORMAT (research template vs direct answer, Sep 26)
- Spec: research questions → `## Answer` (concise), `## Key Findings` (bullets), `## Analysis` (detail),
  `## Comparison` (markdown table `| Criterion | A | B |`), `Sources`; simple questions → direct answer,
  "do not force every answer into a long template".
- Implemented as an explicit **RESPONSE FORMAT** block appended to the `_SYSTEM_HEAD` STYLE RULES in
  `app/agents/nodes/synthesize.py`: research/comparison/decision questions must emit the heading template
  in order (Answer → Key Findings → Analysis → Comparison-only-when-comparing) and then STOP — no Sources
  heading (the backend already appends `## Sources` via `attach_sources_section`, dedup-aware). Simple
  questions: direct short paragraph, no headings. Existing STYLE RULES depth guidance retained.
- **Verification**: synthesize-adjacent suites pass (64 tests: graph, search_degrade, comparison, decision,
  security_citations, doc_rag_spec, research_depth). Backend restarted pid 1440993 (`curl /api/health` ok).
  Live format check NOT possible this session: Groq hit daily token quota (429, TPD 200k used ~197k) and DDG
  returned no results for a sample query — both pre-existing operational constraints; the answer fell back to
  the best-effort evidence path. Manual verify once quota resets (tomorrow) with,
  e.g., "Compare React and Vue for a mid-size team" → expect the headered template; then a simple question
  like "What is the capital of France" → expect a direct paragraph. Test conversation cleaned up.

## ADAPTIVE RESPONSE LENGTH + RESPONSE FORMAT (Sep 26)
- Depth tier (`quick | standard | deep`, from `app/agents/depth.py` set by classify) now drives response
  verbosity. NEW `_DEPTH_DIRECTIVES` in `synthesize.py` inject a per-request `VERBOSITY TARGET` directive:
  quick → "2-5 sentences, no headings, absolutely not a 2,000-word essay for 'What is FastAPI?'";
  standard → "normal, focused explanation"; deep → "comprehensive + RESPONSE FORMAT headings". Injected in
  `_build_messages` when `state["depth"]` is present. Live verified: "What is FastAPI?" → depth=quick.
- Previous round's RESPONSE FORMAT block (## Answer / ## Key Findings / ## Analysis / ## Comparison,
  direct answers for simple questions) retained as the deep-tier template.

## OBSERVABILITY (SPEC §59) — structured per-request logs
- NEW `app/services/observability.py`: `RequestSpan` dataclass bound per task via ContextVar; `begin()` /
  `current()` / `record_tool()` / `end()`. `end()` emits ONE structured line
  `event=request.complete` with: request_id, request_type (chat|stream|research), conversation_id, user_id,
  query, intent, depth, mode, model, tools_used (names), tool_latency_ms (per tool), search_count,
  sources_count, tokens (None when provider doesn't expose it), total_latency_ms, status, errors.
- Hooked into `api/chat.py` (JSON + SSE stream) and `api/research.py`. Streaming path observes the SSE queue
  (tool_result → latency, sources/sources_found → sources_count, message_complete → intent/error);
  non-stream path enriches from `state` (evidence+retrieved→sources, tool_runs→tools). `_run_workflow`
  marks span error + appends graph errors; context is ContextVar-propagated into the workflow task.
- **Never log secrets**: `mask_secrets()` masks `key=...`, `password=...`, `token=...`, `bearer <tok>`,
  `sk-/pk-/rk-` and `ghp_` values in the query/tool text; `SafeJSONFormatter` redacts any STRING record key
  whose name contains password/token/secret/key/authorization, but lets numeric metrics (token counts) and
  nulls through. Log lines also carry the active `request_id` via the formatter (lazy import).
- Live verified: stream + JSON both emit `request.complete` (e.g. search_count=3, sources_count=5,
  total_latency_ms=8008; calculator query 890ms, tokens=null). Test conversations cleaned up.

## PERFORMANCE TARGET (SPEC §60) — audited, already compliant
- Simple → low first-token latency: `/api/chat/stream` starts the graph immediately and streams
  `message_delta`; calc replies are cached (`_reply_cache_hit`).
- Search → parallel: `search_web` fires all queries via `asyncio.gather` (staggered 0.35s for DDG);
  academic adds arXiv in the same batch.
- Tools → independent calls in a plan step run via `asyncio.gather` (`run_tool_plan` list-steps).
- Concurrency limits: evidence fetch uses `asyncio.Semaphore(_FETCH_SEMAPHORE_LIMIT=4)`; shared httpx pool
  `max_connections=32`; deep_research tool streams progress; Groq LLM call streams tokens.
- Research → stream progress via SSE status/tool/source events (ToolActivity UI round).

## TESTING (SPEC §61) — new tests + existing coverage map
- NEW `tests/test_response_format.py` (8): research_depth mapping (simple→quick, research→deep, else
  standard), quick→"2-5 sentences"+"not a 2,000-word essay", standard→moderate, deep→comprehensive+
  RESPONSE FORMAT headings, plain query untouched.
- NEW `tests/test_observability.py` (7): mask_secrets credential masking (api_key/password/token/bearer)
  incl. query secret masking in summary, span records tools+latencies+counts, context cleared after end(),
  error status+errors, safe end() with no span.
- Existing suite already covers the §61 unit list: classifier (test_classifier), router (test_intents_router,
  test_fast_path, test_tool_registry_graph), calculator (test_calculator), search provider
  (test_search_providers), URL validator/SSRF (test_security_citations), web extraction (test_units, arxiv,
  wikipedia), citation generator (test_security_citations, test_doc_rag_spec), source dedupe
  (test_comparison, test_decision), document chunking+retriever (test_document_rag, test_doc_rag_spec,
  test_embeddings), LangGraph workflow (test_graph), API endpoints (test_api). Integration paths
  query→router→tools→answer with mocked providers: test_fast_path, test_intents_router, test_graph.
  Mocked providers throughout (conftest: DEMO_MODE/mock LLM + search, ENABLE_CACHE=false, sqlite tmp DB) —
  no API keys required.
- Suite now **329 passed** (was 314). Backend restarted pid 1450485→(live verify) pid active on :8100.

## UNIQUE uuid PER CONVERSATION — each chat is independent
- Gap: new chats streamed under placeholder "new" and frontend jumped to conversations[0] on done.
- Backend: `crud.create_conversation` accepts `conversation_id=None` (client-supplied uuid; fallback `new_id()`
  on collision via rollback+retry). `chat.py _prepare` validates client id IS a `uuid.UUID` (else HTTP 400
  "conversation_id must be a valid UUID"), gets-or-creates the conversation with that exact id; regenerate
  strips last assistant in both branches. `import uuid` added.
- Frontend `send()`: `crypto.randomUUID()` per new chat, sets `currentId` immediately, ALWAYS passes convId to
  streamChat, removed `"new"` placeholder handling and onDone conversations[0] jump.
- Tests (test_api.py, 11 passed): client uuid creates its own conv; follow-up reuses same conv (4 messages);
  invalid uuid rejected 400.

## PER-CHAT ISOLATED MEMORY — "unique memory vector db per uuid"
- Design: each conversation uuid owns its OWN memory. "my name is xyz" in one chat stays in THAT chat and never
  leaks into other chats/tabs. Mechanism = facts dict keyed by conversation_id (additive to user_memory, which
  is now unused for chat context).
- New table `conversation_memory` (id, conversation_id indexed, key, value, updated_at; uq(conversation_id,key)).
  Model `ConversationMemory` in models.py; CRUD in crud.py: `list_conversation_memory`,
  `conversation_memory_dict`, `upsert_conversation_memory`, `clear_conversation_memory`. delete_conversation &
  delete_all_conversations now also purge that chat's memory rows.
- `chat.py` loads `conversation_memory_dict(db, conv.conversation_id)` (was memory_dict(user_id)).
- `remember.py`: prompt copy → "chat-scoped memory for THIS conversation, never shared w/ other chats/tabs";
  saves via upsert_conversation_memory(conversation_id, facts); status "Remembered that for this chat.";
  requires conversation_id, else no-op.
- `synthesize.py`: memory block labeled "REMEMBERED IN THIS CHAT ... scoped to the current chat's unique id
  and is NOT shared with any other chat or tab".
- `research.py`: each call now gets a FRESH internal uuid (`conversation_id="research"` → `uuid.uuid4()` per call)
  so research memory doesn't pool under one shared key.
- Tests rewritten (tests/test_memory.py): upsert/update/clear; ISOLATION (facts in conv A invisible in conv B);
  empty default; synthesize prompt contains chat-scoped memory + NOT shared; remember node safe on non-JSON
  LLM output; remember saves scoped to conversation (A holds fact, B empty). Module fixture calls init_db() so
  direct-DB tests don't depend on ordering.
- Suite now **334 passed**. Backend restarted (pid 1500824 on :8100). Live DB got the new table via create_all
  (inspect confirmed: conversation_memory[id,conversation_id,key,value,updated_at]).
- LIVE VERIFIED (save path): streamed "My name is Mustafa, remember that" (conv b32412c9-9465-4527-ad01-1315aa0afb6c)
  → remember node stored row ('name','Mustafa') under that conv uuid only.
- LIVE BLOCKED (read-back path): Groq daily TPD quota still exhausted → synthesize returns best-effort/error
  replay for LLM answers, so the "same chat knows / new chat doesn't" comparison couldn't be confirmed live.
  Covered by mocked unit tests (isolation + scoped remember). Retry after quota reset.

## "couldn't reach the language model" while summarizing a document — ROOT CAUSE = Groq quota
- User uploaded a doc + asked for a summary → got "I couldn't reach the language model. Please check your
  configuration and try again." NOT a config bug. Logs: `app.summarizer direct summarization failed ... Rate
  limit reached ... tokens per day (TPD): Limit 200000, Used 199648 ... Please try again in 9m17s` (HTTP 429).
  Every LLM call (summarizer, synthesize, classifier, follow-ups) was rejected; upload/ingest/document_search
  are deterministic and worked fine.
- Groq free tier = 200k TPD per model (`qwen/qwen3.8-27b`), rolling window — quota slides out over minutes;
  retry after the logged retry-after, or switch GROQ_MODEL to another model for a whole fresh allowance.
- IMPROVED UX: `synthesize.py` now detects 429/rate_limit/TPD/OTPM and returns a clear message instead of the
  generic config text: "the language model is rate-limited ... wait a few minutes ... or switch GROQ_MODEL",
  plus a rate-limit-aware status line. `_rate_limit_hint()` + `_model_unavailable_message()` + error passed
  through `_fallback_answer`. Suite 334 passed; backend restarted (pid 1515210 on :8100).

## NEW GROQ KEY SWAPPED (Sep 26)
- User pasted a fresh Groq API key; replaced GROQ_API_KEY in backend/.env (no secret values written to SESSION.md).
- Backend restarted (pid 1541765 on :8100). Verified: plain chat "Reply with exactly OK" → content "OK", follow-ups generated (no 429).
- FULL REPRO of the reported bug now PASSES: uploaded sample_doc.txt → "generate the summary from the attached document"
  → proper "## Answer / ## Key Findings" with document citations. Test doc + conversations deleted.
- Note: if the old and new keys belong to the same Groq account/org, the org-level TPD is shared and the limit would still apply — the new key answered immediately, so it's a different account/allowance.

## NGROK HIJACK RECURRENCE (Sep 26) — "postcraft interface" showing at our URL
- User saw the OTHER project's ("postcraft", port 5173) interface at lingo-suburb-living.ngrok-free.dev.
- Root cause: a 2nd ngrok agent `ngrok http 5173` (pid 1547855, NO --url) auto-bound our account's static
  subdomain (known v3.7+ behavior), so the URL served 5173's app (`text_provider gpt-oss-120b, linkedin...`).
  NOT a port conflict on our side (ours: 8100 python, 5200 vite). :5173/:8000 belong to the other project.
- FIX: kill the rogue ngrok agent; keep ours `ngrok http 5200 --name=chatgptlive` (pid 906354).
  URL verified: `/api/health` → our payload (tool_count 14). Other project still reachable via their cloudflared (:5173).
- Rule: the other project must run `--url <its-own-subdomain>` or cloudflared, NEVER a bare `ngrok http <port>`.

## UI ROUND — MiGPT-style Header + Sidebar layout (Sep 26)
- User iterated on look: light "#f7f7f8"-style sidebar (w-[260px], black/5 hovers), then a Header mock
  (h-14 white, PanelLeft toggle, brand + ChevronDown, New chat, More), then asked to compose them like an
  MiGPT layout: layout-level sidebar visibility `{sidebarOpen && <Sidebar/>}` w/ Header onToggleSidebar/onNewChat.
- IMPLEMENTED: store `sidebarCollapsed`/`toggleSidebarCollapsed` REMOVED; added `toggleSidebar` (open/close only,
  no rail — sidebar fully hides on toggle). New `components/Header.tsx` (PanelLeft + ChatGPTLogo/Wordmark +
  ChevronDown brand, New chat, Copy, More). App now: Sidebar(open,onClose) + Header(onToggleSidebar=store.toggleSidebar,
  onNewChat=newConversation) + ChatWindow (header removed from ChatWindow). Sidebar rewrote: one `SidebarContent`
  shared by desktop static (`hidden md:flex` when open) + mobile drawer (fixed, -translate-x-full, backdrop) —
  backdrop moved into Sidebar itself. Theme toggle kept in Sidebar header. Build clean (vite hot-reloads).

## UI ROUND 2 — MiGPT full-layout redesign (Sep 26)
- User supplied a COMPLETE MiGPT-style app layout (sidebar + header + chat area + composer mock w/ Sparkles/bubbles/W
  eb Search) and asked to adopt it fully + keep ALL backend features working.
- Sidebar: MiGPT logo (Sparkles box) + PanelLeftClose, New chat, Search chats (⌘K, debounced→searchChats),
  Projects (placeholder), Recent grouped history w/ per-chat ⌃Rename/Delete dropdown menu wired to
  renameConversation/deleteConversation, Clear conversations, Settings/Help (static), user card + dropdown
  (Profile/Settings/Log out static). Desktop: static w-[270px] lg:flex when open; mobile: fixed drawer + backdrop.
- Header: Menu button (shown only when sidebar closed), "MiGPT" selector, Web Search toggle (Globe, wired to
  mode no_web<->auto), theme Sun/Moon, More.
- ChatWindow: mock empty state (Sparkles 14x14 badge, "What can I help with?", subtitle, suggestion chips) +
  real message list (Message.tsx, markdown/sources/followups/rate) + streaming (ResearchActivity/ToolActivity).
- Composer: rounded-[28px] bordered box w/ shadow; textarea (Enter=send, Shift+Enter=newline); toolbar
  Paperclip (upload .txt/.md/.pdf/.docx/.csv → uploadDocument), Web Search toggle; Send/Stop black circle;
  kept mode pills (Auto/Web/Deep Research/No Web) + upload chips + "MiGPT can make mistakes" footer line.
- Colors mapped to theme tokens (light ≈ the mock hexes; dark mode preserved). Frontend suite: npm run build clean
  (tsc + vite). Live: :5200 serving + ngrok /api/health OK (backend pid 1541765).
