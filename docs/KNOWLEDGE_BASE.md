# Knowledge Base

This file stores durable project knowledge that should survive across Codex sessions. Update it after each work session when something reusable is learned.

## Standing Rules

- Always update `docs/WORKLOG.md` at the end of a work session.
- Always update this file, `docs/KNOWLEDGE_BASE.md`, when new reusable project knowledge, operations notes, architecture decisions, or gotchas appear.
- Never write plaintext admin passwords, API keys, ST values, AT values, Google cookies, proxy credentials, or private SSH material into docs.
- Mask secrets when they must be referenced, for example `sk-...abcd` or `****`.
- The user usually works in Chinese. Use Chinese for user-facing answers unless the user asks otherwise.

## Important Paths and Hosts

- Local repository: `C:\Users\10339\Desktop\codex\flow2api`
- Production VPS IPv4: `15.204.119.78`
- Production domain: `https://niktokfurniture.com`
- Production app path: `/opt/flow2api`
- Production compose file: `/opt/flow2api/docker-compose.prod.yml`
- Main production containers: `flow2api`, `flow2api-caddy`
- Production currently builds the headed service image as `flow2api:cloud-headed` through `Dockerfile.headed`; ordinary app-code updates only need the `flow2api` service recreated and should leave `flow2api-caddy` running.

## S-UI VLESS Reality Node

- S-UI `1.2.2` is installed on the production VPS at `/usr/local/s-ui`.
- The S-UI database is `/usr/local/s-ui/db/s-ui.db`.
- The VLESS Reality inbound tag is `vless-reality-8443` and listens publicly on TCP `8443`.
- Current Reality target/SNI is `www.microsoft.com`; current client fingerprint is `chrome`.
- S-UI panel and subscription services are bound to localhost only:
  - Panel: `127.0.0.1:2095`
  - Subscription: `127.0.0.1:2096`
- UFW should allow `8443/tcp` and explicitly deny public `2095/tcp` and `2096/tcp`.
- Generated node links, UUIDs, Reality keys, panel usernames/passwords, and subscription material live under `/root/s-ui-secret/` and must not be copied into repository docs.
- To manage the S-UI panel, use an SSH tunnel such as `ssh -L 2095:127.0.0.1:2095 ubuntu@15.204.119.78`, then read the panel path from the root-only panel metadata file on the server.
- If editing S-UI `1.2.2` SQLite rows directly, JSON columns such as `tls.server`, `tls.client`, `inbounds.addrs`, `inbounds.out_json`, `inbounds.options`, `clients.config`, `clients.inbounds`, and `clients.links` must be stored as BLOB bytes, not TEXT, or S-UI fails scanning into `json.RawMessage`.
- For a plain TCP VLESS inbound in S-UI/sing-box `1.2.2`, omit the `transport` block. Setting `transport: {"type":"tcp"}` causes `unknown transport type: tcp`.
- Plain TCP/HTTP port probes against the Reality port can create `REALITY: processed invalid connection` log entries. That only proves the port reached the inbound; it is not a full client authentication test.

## Deployment Runbook

Use this pattern for small production updates:

1. Copy changed files to a temporary path on the server.
2. Back up the files being replaced under `/opt/flow2api/backups/<timestamp>/`. If the backup directory has restrictive ownership, use passwordless `sudo` for `mkdir` and `cp`.
3. Copy files into `/opt/flow2api/app`.
4. Rebuild and recreate containers. If only application code/static files changed, recreating just `flow2api` is enough and leaves Caddy running:

```bash
cd /opt/flow2api
docker compose -f docker-compose.prod.yml up -d --build --force-recreate flow2api
```

5. Verify:

```bash
curl -sS https://niktokfurniture.com/health
docker compose -f /opt/flow2api/docker-compose.prod.yml ps
docker compose -f /opt/flow2api/docker-compose.prod.yml logs --tail=100 flow2api
```

## Deployment Gotchas

- Production `/opt/flow2api/app` is a git checkout of `origin/main`. Prefer `git fetch && git reset --hard origin/main` to align it to a pushed commit, but first back up: `tar` the worktree into `/opt/flow2api/backups/<ts>/` (exclude `data/ tmp/ venv/ .git`) and/or `git stash push -u`. Production `config/setting.toml`, `data/`, and `tmp/` are volume-mounted and NOT git-tracked, so `reset --hard` does not touch runtime config or data.
- The server worktree may hold an older uncommitted divergence from a prior manual deploy. Compare the real semantic diff with `git diff --ignore-cr-at-eol origin/main` (Windows-authored commits add CRLF noise that inflates the plain diff) before overwriting.
- Startup DB migration ordering: on an existing database, `main.py` must call `check_and_migrate_db()` (adds missing tables/columns) BEFORE `init_db()` (builds indexes). `init_db` uses `CREATE TABLE IF NOT EXISTS`, so it never adds columns to an existing table; building an index on a not-yet-migrated column (e.g. `idx_tokens_owner_client_id` on `tokens.owner_client_id`) crashes startup with `sqlite3.OperationalError: no such column` and loops the container. Fresh DBs are unaffected because `init_db`'s CREATE TABLE already contains the column.

## DNS Notes

- The domain is `niktokfurniture.com`, bought at NameSilo.
- The production apex domain should point to the VPS IPv4 `15.204.119.78`.
- `www` should route to the apex domain.
- Before changing DNS, check whether the domain is using NameSilo-managed nameservers or another DNS provider. NameSilo can show existing resource records even when edits are disabled by the active nameserver choice.
- Preserve unrelated mail records such as MX unless the user explicitly wants to move email service.

## Admin and API Access

- Admin username has been `admin` during this setup.
- Do not store the admin password or full API keys in the repository.
- The legacy global API key still works for public API calls.
- New per-user accounts and API keys are managed in the admin UI under `用户管理`.
- Newly created user API keys are shown once at creation time; store them outside the repo.

## Current Product Direction

- The service is becoming a central Flow API pool for roughly 20 users and a growing pool of Google Flow accounts.
- The default image model requested by the user is now `gemini-3.1-flash-image-square`.
- Default generation preferences requested by the user:
  - image-to-image is the main workflow
  - square aspect ratio
  - output count 1
  - users can override model, ratio, and count manually
- Each user should have an independent API key, usage statistics, and optional usage limits.
- Usage accounting should focus on successful generations.
- Users need private prompt templates, and admins need public prompt templates.

## Account Pool and Token Routing

- Token records represent Google Flow accounts/sessions.
- Token records have a custom `token_group` field. Use it for operational grouping such as product line, account tier, customer allocation, or backup pool.
- `token_group` defaults to `default` and is intentionally lightweight instead of a separate group table.
- Token `image_concurrency` and `video_concurrency` are hard per-Token concurrency limits. Values greater than `0` cap simultaneous jobs for that media type; missing or non-positive limits are treated as unlimited but still tracked as in-flight.
- Generation requests acquire the hard slot in `GenerationHandler.handle_generation` after load-balancer Token selection and before AT refresh/project/generation work.
- Slot waits use `flow.image_slot_wait_timeout` and `flow.video_slot_wait_timeout` from config. If the wait times out, return HTTP `429` and do not call the upstream Flow generation endpoint.
- Always release slots from a `finally` path. Leaked slots would make the admin UI show false live load and could block future jobs.
- For production stability, prefer Token image concurrency `1` or `2` and scale throughput by adding more healthy accounts instead of running many simultaneous image jobs through one Google account.
- Tokens have a soft failure cooldown field, `cooldown_until`. When a request log finalizes as a Token-level failure (`status_code >= 500`, or non-local-concurrency `429`), `TokenManager.cooldown_token_after_failure()` sets a random 60-180 second cooldown.
- Load balancing must skip active Tokens whose `cooldown_until` is still in the future. This applies even when a request explicitly pins a Token with `x-flow2api-token-id`.
- Failure cooldown is not the same as manual/long-term disable. Do not set `is_active=false` for ordinary transient failures; keep `is_active`, `ban_reason`, and `banned_at` for manual disable, 429 long bans, and AT refresh failures.
- Local concurrency-full failures (`status_text="concurrency_full"`) should not trigger failure cooldown because they mean the Token is busy, not unhealthy.
- ST is used to refresh AT; AT stability is central to production reliability.
- Pure session-mode Tokens can only be kept fresh while the stored ST remains accepted by Google/Flow. They cannot be made truly permanent without a durable login refresh path.
- Protocol-mode Tokens with valid Google Cookies are the preferred long-running account-pool mode because the background refresher can renew ST and then AT.
- A Token disabled with `ban_reason="at_refresh_failed"` means all AT/ST refresh attempts failed. Protocol-mode keepalive is allowed to retry and re-enable this reason after a successful refresh.
- Manual refresh failures are stored in `last_st_refresh_result` and surfaced by `/api/tokens/{token_id}/refresh-at`.
- If ST-to-AT succeeds but `/credits` returns upstream HTTP 401 `invalid authentication credentials`, the account/session is not accepted by the Flow API even though the session endpoint returned an AT. Re-import that account with a fresh logged-in session or protocol cookies.
- Chrome extension `Flow2API Captcha Worker` version `1.0.1` can import both the Labs ST and allowlisted Google Cookies. When it finds required Google auth cookies, it sends `protocol_mode="protocol"` and `google_cookies` to `/api/plugin/update-token`.
- Existing installed extension copies must be reloaded in `chrome://extensions` after host-permission changes. Chrome may ask the user to approve the new `*.google.com` permission.
- Account pool capacity can grow continuously by adding more Tokens.
- API clients can be bound to specific Tokens through `api_client_token_bindings`.
- Binding types:
  - `all`: image and video requests
  - `image`: image requests only
  - `video`: video requests only
- When an API client has bindings for a generation type, load balancing must only choose from the bound active Tokens.
- If all bound Tokens are unavailable, the request should fail clearly instead of falling back to the shared default pool.
- API clients with no explicit bindings have an empty Token pool; they must not fall back to the admin/global pool.
- Test-page Token selection uses the `x-flow2api-token-id` request header.
- Requested Token selection must be applied after API-client binding filters, so a user cannot use the header to escape their assigned Token pool.
- If the requested Token is unavailable or not allowed, return a clear no-token error instead of silently falling back.

## Key Code Areas

- Admin API and admin auth: `src/api/admin.py`
- Public OpenAI/Gemini-compatible API routes: `src/api/routes.py`
- Database schema and migrations: `src/core/database.py`
- Data models: `src/core/models.py`
- Token selection and routing: `src/services/load_balancer.py`
- Generation orchestration and request logging: `src/services/generation_handler.py`
- Admin UI: `static/manage.html`
- User test page: `static/test.html`
- Browser extension import work: `extension/`

## Current API Client Feature State

- `ApiClient` stores per-user console login metadata (`username`, bcrypt `password_hash`, `role`), API key metadata, personal plugin connection token, active state, daily limit, total limit, success counts, and last-used timestamp.
- `ApiClientTokenBinding` stores which Token IDs a user API key may use.
- Request logs include `api_client_id` and `api_client_name`.
- Admin endpoints under `/api/clients` support listing, creating, updating, detail lookup with the full API Key, deleting, and replacing Token bindings.
- API-client list responses should keep `api_key` masked even for short custom keys. Full keys should only be returned by create/detail admin responses.
- Admin Token list returns operational fields such as live image/video inflight counts, remaining concurrency, bound clients, and latest request status.
- The `用户管理` UI can create users with login username/password, show masked keys, view details with the full key, copy the full key, delete users, toggle active state, show success counts, and open the visual Token binding modal.

## User Console Scoping

- Admin and ordinary users share `/login`, `/manage`, and `/test`, but `/api/session` identifies the active role.
- Ordinary users log in with `ApiClient.username` and password, then use the generated `ApiClient.api_key` for API calls.
- Ordinary users must not see `/api/clients`; user-management endpoints stay admin-only.
- Ordinary users only see Tokens whose `tokens.owner_client_id` matches their client ID. Admin sees all Tokens, including user-imported Tokens.
- Ordinary users only see request logs and dashboard statistics derived from their owned Tokens.
- User plugin configuration is read-only from the console: connection URL, personal plugin connection token, and API Key are generated by the server/admin flow.
- When a user imports a Token through the plugin using their personal plugin token, the Token is assigned `owner_client_id` and bound to that user with generation type `all`.
- Admin/global plugin imports keep `owner_client_id` empty.

## Error Statistics

- Treat `request_logs` as the authoritative source for error totals.
- Dashboard `today_errors` and `total_errors` are derived from `request_logs.status_code >= 400`.
- Per-Token `error_count`, `today_error_count`, and `last_error_at` are also derived from `request_logs`, grouped by `token_id`.
- Dashboard total errors include failed rows where no Token was selected (`token_id IS NULL`); per-Token rows exclude those unassigned failures.
- `token_stats.error_count` and `token_stats.today_error_count` are legacy/derived values and may drift from historical logs. Do not use them for user-facing error totals.
- Keep `token_stats.consecutive_error_count` for auto-ban/consecutive-failure behavior because it models current Token health rather than historical request-log totals.

## Retryable Generation Error Contract

- Flow2API error responses should expose machine-readable retry metadata under `error`.
- Current fields:
  - `status_code`: HTTP status used by non-stream JSON responses.
  - `code`: stable reason code such as `concurrency_full`, `no_token_available`, `token_cooling_down`, `token_at_invalid`, or `account_tier_unsupported`.
  - `retryable`: boolean telling downstream automation whether the request can be retried.
  - `retry_after_ms`: recommended client-side wait before retrying.
  - `status_text`: optional human/system status label matching the reason code.
- Retryable examples:
  - `429 concurrency_full`: local per-Token hard slot is full. Retry after the hint.
  - `503 no_token_available`: no matching active Token is currently usable. Retry later.
  - `503 token_cooling_down`: all matching Tokens are in failure cooldown. Retry after the hint.
  - `503 token_at_invalid`: selected Token failed AT validation/refresh. Retry later so another healthy Token may be selected.
  - `500/502/504`: upstream/internal temporary generation failure unless a more specific non-retryable code is returned.
- Non-retryable examples:
  - `400` invalid request/model payload.
  - `403 account_tier_unsupported`: the account/model combination cannot work without changing configuration.
- Streaming OpenAI-compatible responses may still be HTTP 200 while carrying `data: {"error": ...}`. Downstream clients must inspect SSE error payloads and preserve the retry metadata.

## Test Page State

- The test page is at `https://niktokfurniture.com/test`.
- `/test` is protected by the admin session guard. If unauthenticated, it redirects to `/login`.
- The page defaults to `gemini-3.1-flash-image-square`.
- The page uses the admin API key from `/api/admin/config` when the browser has an admin session.
- Token options are loaded from `/api/tokens` and grouped by `token_group`.
- Specific Token testing is sent through `x-flow2api-token-id`.
- The test page supports 1-4 image outputs by issuing sequential single-image generation requests from one reference image and prompt; each backend request still uses `n: 1`.
- The default prompt template is for product image-to-image optimization and must preserve the product body, color, material, shape, and details.
- The test page stores the saved default model and saved prompt templates in browser `localStorage` under `flow2api.test.settings.v1`.
- Test page result preview should handle Markdown images, HTML images/videos, plain media URLs, Flow `flow-content.google` URLs without file extensions, relative URLs, data image URLs, OpenAI non-stream `message.content`, Gemini `fileData` / `inlineData` image parts, and common JSON image fields.

## External Automation Integration

- The user's AliExpress automation app lives at `C:\Users\10339\Desktop\codex\ali auto\aliexpress测试版`.
- Its image pipeline entry points are:
  - `src/aliexpress-auto-publish/image-pipeline.js`
  - `src/aliexpress-auto-publish/ai-image-client.js`
- That automation currently has an OpenAI Images-style provider path. Flow2API should be consumed through the OpenAI-compatible chat endpoint:
  - Base URL: `https://niktokfurniture.com/v1`
  - Endpoint: `/chat/completions`
  - Model: `gemini-3.1-flash-image-square`
  - Request content: text prompt plus `image_url` entries, where local product images can be sent as `data:image/...;base64,...`.
  - Optional Token pinning: send `x-flow2api-token-id: <token_id>`.
- For a future code integration, add a `flow2api_chat` provider branch in `ai-image-client.js` rather than forcing Flow2API through `/images/edits`, because Flow2API image-to-image is exposed through chat completions.
- The page supports file selection, drag-and-drop, and clipboard paste for reference images.

## Testing Baseline

Useful targeted checks after API-client or token-routing changes:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_api_client_pool tests.test_api_client_admin_auth tests.test_plugin_account_import -v
.\venv\Scripts\python.exe -m py_compile src\core\models.py src\core\database.py src\services\load_balancer.py src\services\generation_handler.py src\api\routes.py src\api\admin.py
node -e "const fs=require('fs'); const html=fs.readFileSync('static/manage.html','utf8'); const matches=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]; for (const [i,m] of matches.entries()) { new Function(m[1]); } console.log('checked_scripts='+matches.length);"
node tests\test_test_page_preview.js
git diff --check
```

Useful checks after test-page or Token-group changes:

```powershell
.\venv\Scripts\python.exe -m unittest tests.test_api_client_pool tests.test_api_client_admin_auth tests.test_plugin_account_import -v
.\venv\Scripts\python.exe -m py_compile src\core\models.py src\core\database.py src\services\load_balancer.py src\services\generation_handler.py src\api\routes.py src\api\admin.py
node -e "const fs=require('fs'); for (const file of ['static/manage.html','static/test.html']) { const html=fs.readFileSync(file,'utf8'); const matches=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]; for (const [i,m] of matches.entries()) new Function(m[1]); console.log(file+': checked_scripts='+matches.length); }"
node tests\test_test_page_preview.js
```

Known full-suite issue as of 2026-06-30:

- `tests/test_veo_lite_support.py::VeoLiteFlowClientTests::test_check_video_status_uses_media_payload_and_normalizes_response` fails with `KeyError: 'fifeUrl'`.
- Other tests in the full suite passed when last checked.
- Treat this as a known unrelated baseline issue unless working on video status normalization.

## Security Notes

- Do not commit `data/`, runtime logs, cookies, live ST/AT values, browser profiles, SSH keys, or generated secret exports.
- When documenting production, record paths, commands, and masked identifiers rather than secret values.
- If a command output contains a secret, summarize the result instead of copying the raw output into docs.
