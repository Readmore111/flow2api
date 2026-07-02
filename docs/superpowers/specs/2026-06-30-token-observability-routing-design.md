# Token Observability And Routing Design

## Goal

Give the admin console a professional Token operations view and add real backend routing so specific user API keys can be pinned to specific Tokens or Token pools.

## Current Context

The existing admin page already shows total image/video/error counts from `token_stats`, and `LoadBalancer.select_token()` is the central selection path for image and video generation. API authentication is currently a single global API key through `AuthManager.verify_api_key()`, so per-user usage and Token pinning need first-class client records rather than front-end-only filtering.

## Backend Design

Add an `api_clients` table for per-user API keys:

- `id`, `name`, `api_key`, `is_active`
- `daily_limit`, `total_limit`
- `success_count`, `today_success_count`, `today_date`
- timestamps

Add an `api_client_token_bindings` table:

- `client_id`
- `token_id`
- `generation_type`: `image`, `video`, or `all`

The legacy global API key remains valid as a default admin/shared client with no pinned Token restrictions. New client keys are resolved during request authentication and passed to the generation handler. The load balancer filters candidate Tokens by client bindings before applying the existing availability, tier, concurrency, and round-robin logic. If a client has bindings but none of those Tokens are usable, the request fails with a clear "bound Token unavailable" error rather than silently using another account.

## Observability Design

The Token list endpoint will include operational fields:

- `image_inflight`, `video_inflight`
- `image_remaining`, `video_remaining`
- `today_image_count`, `today_video_count`, `today_error_count`
- `last_status_text`, `last_status_code`, `last_duration`, `last_request_at`
- `bound_clients`

The existing request log table will be extended with `api_client_id` and `api_client_name` so later views can attribute usage by user.

## Admin UI Design

The Token page becomes an operations dashboard:

- summary cards for total, active, busy, today success, today errors
- a dense professional Token table with status chips, live load, usage, quota/credits, expiry, bindings, and grouped actions
- automatic refresh every three seconds while the Token tab is visible

Add a new "用户/API Key" tab:

- list clients
- create/edit/disable API keys
- assign one or more Tokens to a client
- show success usage and limits

## Testing

Backend tests cover:

- API clients can be created, listed, updated, and disabled
- API client authentication accepts new keys and the legacy global key
- bound clients only select bound Tokens
- unavailable bound Tokens return no fallback Token
- Token list includes live concurrency/request-log fields

UI verification covers:

- static JS syntax
- the dashboard renders with the new API shape
- cloud `/health`, `/api/tokens`, and `/api/clients` work after deployment
