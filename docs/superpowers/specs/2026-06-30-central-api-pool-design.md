# Central API Pool Design

## Purpose

Build Flow2API into a Linux cloud-hosted central API pool for about 20 users and 30+ Google Flow accounts. The primary workflow is image-to-image generation: a user uploads one source image, chooses or edits a prompt, and receives one generated image.

The system must hide account complexity from users. Users call one API endpoint or use one web workspace. The server manages account selection, image upload, captcha handling, AT/ST refresh, retries, quotas, and usage logging.

## Scope

Phase 1 includes:

- Linux cloud service deployment.
- Per-user API keys with usage statistics and success-based quotas.
- Single-image image-to-image API and web workspace.
- Prompt templates with public and private visibility.
- Automated account pool management for 30+ accounts.
- Health checks, failure isolation, retry routing, and request logging.

Phase 1 does not include billing, multi-tenant public SaaS hosting, full desktop packaging, or video-first workflows.

## Recommended Architecture

Use the existing FastAPI service as the core backend and add two infrastructure services:

- PostgreSQL for durable data: users, API keys, prompt templates, token accounts, projects, quotas, jobs, and logs.
- Redis for runtime coordination: account locks, request queues, retry state, health cache, and idempotency keys.

On the Linux cloud server:

- Flow2API runs as a Docker Compose service or systemd-managed Python service.
- PostgreSQL and Redis run as Docker Compose services with persistent volumes.
- A reverse proxy such as Caddy or Nginx exposes HTTPS.
- systemd starts the stack after reboot.
- Firewall rules expose only SSH, HTTP, HTTPS, and any explicitly required admin port.

The public API base URL should look like:

```text
https://<domain-or-lan-host>/v1
```

## Defaults

The default image-to-image settings are:

- Model: `gemini-3.0-pro-image-square`
- Aspect ratio: square
- Output count: `1`
- Input images: exactly one source image for the main workspace flow

Users may override model, aspect ratio, output count, and prompt/template when permitted by their API key policy.

## User API Keys

Replace the single global API key behavior with per-user keys.

Each API key stores:

- owner display name
- hashed key value
- enabled or disabled status
- allowed account groups
- allowed models
- per-user concurrency limit
- daily, monthly, and total success quotas
- created, last-used, and revoked timestamps

Quota is deducted only after a generation succeeds and a final image URL or cached image is returned. Failed uploads, captcha failures, account failures, upstream failures, and internal errors do not deduct quota, but they are logged.

Each request log stores:

- API key/user id
- selected account/token id
- model
- aspect ratio
- prompt template id, if any
- status and error reason
- duration
- success quota charged
- generated asset metadata

## Prompt Templates

Templates support both shared admin templates and private user templates.

Template fields:

- name
- description
- prompt body
- visibility: `public` or `private`
- owner user id for private templates
- default model
- default aspect ratio
- default output count
- category/tags
- enabled status

Public templates are managed by admins and visible to all users. Private templates are visible only to the owner. A user can copy a public template into a private template and edit it.

Templates may support variables such as `{background}`, `{style}`, or `{color}`. The server validates that required variables are supplied before generation.

## Image-To-Image Flow

The dedicated image-to-image endpoint accepts:

- source image as multipart upload, data URL, or image URL
- prompt text or template id plus template variables
- optional model override
- optional aspect ratio override
- optional output count override

Processing flow:

1. Authenticate the user API key.
2. Check enabled status, quota, model permission, and user concurrency.
3. Resolve prompt from direct text or template.
4. Resolve final model and aspect ratio, defaulting to `gemini-3.0-pro-image-square`.
5. Create an idempotent job record.
6. Select a healthy account from the allowed account groups.
7. Acquire account and user concurrency locks.
8. Ensure the account AT is valid and the Flow project is ready.
9. Upload the source image with the selected account and project.
10. Generate the image using the same account and project.
11. Cache or return the final image URL.
12. Mark the job successful and deduct success quota.
13. Release locks and update account health.

If an account fails with a retryable error, the retry must select a new account and re-upload the source image. Media ids from one account must not be reused with another account.

## Account Pool

The account pool continues to use Flow session token accounts, but account management becomes automated and group-aware.

Account fields:

- email/name
- ST, AT, AT expiration
- account tier
- credits/balance
- current project id
- project pool
- image/video capability flags
- account group
- token-level proxy
- captcha proxy
- health state
- cooldown until timestamp
- last refresh result
- consecutive failures

Health states:

- `healthy`
- `warming`
- `cooldown`
- `needs_login`
- `disabled`
- `banned_or_rate_limited`

The load balancer should prefer healthy accounts with available concurrency, sufficient tier, valid AT, and low recent failure rate.

## Account Add Wizard

The admin flow for adding accounts should be as simple as possible.

Single account flow:

1. Admin clicks "Add account".
2. Admin chooses import method:
   - read from an already logged-in browser session
   - paste ST/cookies
   - upload/import structured JSON
3. Server validates the ST and converts it to AT.
4. Server fetches email, account tier, and credits.
5. Server binds or creates an initial Flow project.
6. Server assigns default group `image-default`.
7. Server runs a health check.
8. If successful, account enters the healthy pool.

Batch import flow:

- Admin uploads JSON/CSV or pastes multiple ST/cookie rows.
- Server validates each row independently.
- Duplicates are skipped.
- Successful accounts are grouped and warmed.
- Failed accounts show actionable error reasons.

## Automation Workers

Background workers should keep the account pool ready without manual attention.

Required workers:

- AT refresh worker: refresh before AT expiration.
- ST refresh worker: attempt protocol or browser refresh when needed.
- account health worker: refresh credits, tier, and recent status.
- project pool worker: ensure each account has enough usable projects.
- cooldown worker: restore accounts after transient failures.
- queue worker: assign pending generation jobs to healthy accounts.
- cleanup worker: expire old temporary uploads and cached files.

The refresh system must avoid logging ST or AT in plaintext.

## Stability Rules

The system should prioritize stable throughput over maximum theoretical concurrency.

Initial limits:

- account image concurrency default: `1`
- user concurrency default: configurable, recommended `1-3`
- global queue enabled
- retryable account errors trigger account cooldown
- non-retryable user errors fail fast without account penalty

Quota is charged only once per successful job. Use job-level idempotency so retries or client reconnects do not double-charge.

## Admin Interfaces

Phase 1 admin pages:

- Users/API keys: create, disable, reset key, set quota, set allowed group, view usage.
- Accounts: add account wizard, batch import, group assignment, health, credits, project id, last error, cooldown.
- Templates: public template CRUD.
- Jobs/logs: filter by user, account, status, model, template, and time.
- System health: active accounts, queue depth, Redis/PostgreSQL status, worker status.

## User Workspace

Phase 1 user page:

- API key login or pre-authenticated internal session.
- Upload one source image.
- Choose public or private prompt template.
- Edit prompt before submit.
- Choose model, aspect ratio, and output count if allowed.
- Submit generation and view progress.
- Preview/download result.
- Save current prompt as a private template.
- View own usage and remaining quota.

## Deployment On Linux Cloud

Linux cloud requirements:

- Ubuntu 24.04 LTS or another current Ubuntu LTS release.
- 4 vCPU and 16 GB RAM recommended for the initial 20-user, 30-account pool.
- 8 GB RAM may work for testing, but 16 GB is preferred for browser automation, Redis, PostgreSQL, and workers.
- 160 GB SSD recommended for database, logs, uploads, cached media, and backups.
- Docker Engine and Docker Compose plugin.
- Persistent data directory for database volumes, logs, uploaded source images, and cached generated media.
- Reverse proxy with HTTPS using a domain name.
- SSH key authentication and firewall hardening.

Recommended deployment shape:

```text
Linux Cloud Server
  Caddy or Nginx reverse proxy
  Flow2API backend container
  PostgreSQL container
  Redis container
  worker process/container
  persistent data directory
  backup directory or remote backup target
```

Recommended regions:

- Singapore, Hong Kong, Tokyo, or US West, depending on which region gives the most stable Google Flow access.
- The final region should be validated by logging into Flow, importing at least one account, and running several image-to-image test generations.

Recommended provider-neutral setup:

- OS: Ubuntu 24.04 LTS.
- Access: SSH key, no password login.
- Domain: one API domain such as `api.example.com`.
- TLS: automatic HTTPS through Caddy or certbot.
- Firewall: allow 22, 80, 443; restrict database and Redis to the Docker/internal network.
- Backups: daily PostgreSQL dump plus persistent media/log rotation.

## Migration From Current Project

The current project already has:

- token storage
- ST to AT conversion
- account/project model
- image upload
- image-to-image generation path
- load balancer
- admin UI
- request logs

Phase 1 should extend these systems instead of replacing them. The main changes are user-level API keys, template storage, stronger account health states, Redis-backed coordination, PostgreSQL support, and a dedicated image-to-image workflow.

## Testing And Acceptance

Acceptance criteria:

- Admin can add at least 30 accounts and see health for each.
- User API keys are independent and enforce success-based quotas.
- Failed generations do not deduct quota.
- Successful image-to-image requests deduct exactly one unit when output count is one.
- Public templates are visible to all users.
- Private templates are visible only to their owner.
- Image upload and generation use the same account.
- Retry with a different account re-uploads the image.
- A disabled or unhealthy account is skipped automatically.
- Service survives reboot through the documented Linux systemd or Docker Compose startup flow.
- Logs never expose full ST, AT, or user API keys.
