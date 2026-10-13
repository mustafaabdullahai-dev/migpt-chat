# Agentic Search & Reasoning Engine

A ChatGPT-style agentic AI platform built on **LangChain + LangGraph + Groq (FastAPI backend)**
with a **React + TypeScript + Tailwind** frontend. It classifies intent, routes to a
LangGraph agent, picks tools (web search, safe math, webpage fetch, Wikipedia, arXiv,
document RAG), streams answers over SSE, and cites sources.

## Features

- **Intent classification + routing** — CALCULATION, WEB_SEARCH, CURRENT_INFORMATION,
  DEEP_RESEARCH, COMPARISON, DECISION_ANALYSIS, DOCUMENT_QA, SUMMARIZATION, CODE, GENERAL_CHAT
- **LangGraph agent** — classify → route → calculate / retrieve-documents / plan-search →
  gather-evidence (iterative) → synthesize with citations
- **10 tools** — safe math (AST-based, no `eval`), web search (DDG/Tavily/Brave/Serper),
  webpage fetch, Wikipedia, arXiv, units conversion, datetime, decision/comparison helpers, document RAG
- **Live web research** with iterative query generation + source dedupe + citation `[n]`
- **RAG** — upload PDF/TXT/DOCX, FAISS vector store, semantic retrieval into the agent
- **SSE streaming** — status, tool activity, sources, message deltas, done
- **Conversations**, titles, follow-ups, feedback, dark/light themes
- **Demo mode** — works with zero config (`DEMO_MODE=true` → mock LLM/search)

## Architecture

```
Frontend (Vite/React/TS/Tailwind) ──/api----▶ FastAPI ──▶ LangGraph agent
      ▲  SSE stream                        │             ├─ services (LLM, search, cache, citations)
      └────────────────────────────────────┘             ├─ tools (calculator, web, wiki, arxiv, docs…)
                                                         ├─ RAG (FAISS + FastEmbed)
                                                         └─ SQLite (conversations, messages, sources)
```

## Quick start (local, demo mode)

Requires Python 3.12+ and Node 20+.

```bash
# 1. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # or: uv pip install -p .venv/bin/python ...
cp .env.example .env                                      # defaults are demo-safe
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Frontend (new terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api → :8000)
```

Open http://localhost:5173 — no API key needed (demo mode).

## Enable live answers (Groq)

```bash
cd backend
# set in .env, then restart uvicorn
DEMO_MODE=false
GROQ_API_KEY=your_key_here    # https://console.groq.com
LLM_PROVIDER=groq
SEARCH_PROVIDER=duckduckgo    # or tavily/brave/serper (add their keys)
```

## Sharing via tunnel (ngrok / Cloudflare)

Run the whole stack on conflict-free ports and expose it publicly:

```bash
./scripts/serve.sh                     # ngrok
./scripts/serve.sh --tunnel=cloudflare # Cloudflare quick tunnel fallback
```

- Backend on **8100**, frontend on **5200** (auto-skipped if busy — set `BACKEND_PORT`/`FRONTEND_PORT`).
- Prints the public URL (UI **and** `/api` share it; Vite proxies API server-side).
- Full-stack endpoint check: `https://<tunnel>/api/health`.

## Testing

```bash
cd backend
source .venv/bin/activate
pytest -q            # 59 tests, fully offline (mock providers)
```

## Docker

```bash
cp .env.example .env          # set GROQ_API_KEY if not demo
docker compose up --build
# http://localhost:5173  (nginx → static app, /api → FastAPI :8000)
```

## API overview

| Method | Path                     | Description                          |
| ------ | ------------------------ | ------------------------------------ |
| GET    | `/api/health`            | Service + tool status                |
| POST   | `/api/chat`              | One-shot answer                      |
| POST   | `/api/chat/stream`       | SSE stream (status/tool/delta/done)  |
| GET    | `/api/conversations`     | List conversations                   |
| GET    | `/api/conversations/{id}/messages` | Messages + sources + tools |
| PATCH  | `/api/conversations/{id}`| Rename                              |
| DELETE | `/api/conversations/{id}`| Delete                              |
| POST   | `/api/research`          | Deep research report                 |
| POST   | `/api/search`            | Raw search results                   |
| POST   | `/api/documents/upload`  | Upload PDF/TXT/DOCX for RAG          |
| GET    | `/api/documents`         | List uploaded documents              |
| POST   | `/api/feedback`          | Rate a response                      |

Interactive docs: http://localhost:8000/docs

## Security notes

- Calculator uses an AST evaluator with a whitelist — never `eval`.
- SSRF guards on URL validation (blocks private/localhost/file/ftp).
- Uploads validated by extension + magic bytes.
- CORS is `*` for dev — scope to a real origin in production.

---

#GenAIwithAM
