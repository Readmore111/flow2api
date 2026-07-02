# Token Observability And Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build per-user API keys, Token binding rules, and a professional real-time Token operations dashboard.

**Architecture:** Store user keys in `api_clients`, bindings in `api_client_token_bindings`, and resolve clients during API authentication. Pass the resolved client to `GenerationHandler`, filter Token candidates in `LoadBalancer`, and enrich admin Token responses with live concurrency and recent log data.

**Tech Stack:** FastAPI, Pydantic, SQLite via `aiosqlite`, vanilla admin HTML/JS with Tailwind, Python `unittest`.

---

### Task 1: API Client Storage

**Files:**
- Modify: `src/core/models.py`
- Modify: `src/core/database.py`
- Test: `tests/test_api_clients.py`

- [ ] Write failing tests for creating, listing, updating, disabling, and binding client records.
- [ ] Add `ApiClient` and `ApiClientTokenBinding` models.
- [ ] Add database migrations and CRUD methods for clients and bindings.
- [ ] Verify `python -m unittest tests.test_api_clients -v`.

### Task 2: Authentication And Routing

**Files:**
- Modify: `src/core/auth.py`
- Modify: `src/api/routes.py`
- Modify: `src/services/generation_handler.py`
- Modify: `src/services/load_balancer.py`
- Test: `tests/test_api_client_routing.py`

- [ ] Write failing tests proving a bound client only sees its bound Tokens.
- [ ] Return a resolved API client context from flexible auth.
- [ ] Pass client context through OpenAI and Gemini generation entrypoints.
- [ ] Filter candidate Tokens by client bindings inside the load balancer.
- [ ] Record successful usage on the client.

### Task 3: Real-Time Token Metrics

**Files:**
- Modify: `src/core/database.py`
- Modify: `src/api/admin.py`
- Modify: `src/services/concurrency_manager.py`
- Test: `tests/test_token_realtime_status.py`

- [ ] Write failing tests for live inflight counts and latest request status in `/api/tokens`.
- [ ] Add snapshot helpers on `ConcurrencyManager`.
- [ ] Add latest-log lookup grouped by Token.
- [ ] Enrich `/api/tokens` with live metrics and bound client labels.

### Task 4: Admin Client APIs

**Files:**
- Modify: `src/api/admin.py`
- Test: `tests/test_api_client_admin.py`

- [ ] Write failing tests for `/api/clients` CRUD and binding endpoints.
- [ ] Add admin endpoints for list, create, update, delete/disable, and binding save.
- [ ] Mask API keys in list output while returning full key on create/regenerate.

### Task 5: Admin UI

**Files:**
- Modify: `static/manage.html`

- [ ] Add the new "用户/API Key" tab.
- [ ] Replace the Token table renderer with a professional dashboard renderer.
- [ ] Add client modal and binding controls.
- [ ] Add three-second auto-refresh for visible Token tab.
- [ ] Verify browser-facing JS with syntax checks.

### Task 6: Deployment

**Files:**
- Modify: server files under `/opt/flow2api`

- [ ] Run local Python tests and JS syntax checks.
- [ ] Sync changed files to the VPS.
- [ ] Rebuild and restart Docker Compose.
- [ ] Verify `https://niktokfurniture.com/health`, `/api/tokens`, and `/api/clients`.
