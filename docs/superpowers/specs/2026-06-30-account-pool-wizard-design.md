# Account Pool Add Wizard Design

## Goal

Build a first-pass account pool add wizard that lets an operator add Google Flow accounts to Flow2API with one action from a Chrome extension while the account is already logged into Google Labs Flow.

## Recommended Approach

Use the existing Chrome extension and plugin connection-token mechanism instead of trying to read Google cookies from the admin web page. A normal admin page cannot read `labs.google` cookies because of browser origin isolation, while a Chrome extension with explicit `cookies` and `https://labs.google/*` permissions can.

## Operator Flow

1. Admin opens Flow2API settings and copies the extension connection URL and connection token.
2. Admin installs or reloads the local Chrome extension.
3. Admin configures the extension with:
   - Flow2API API endpoint
   - plugin connection token
   - optional route key and label for captcha routing
4. Admin logs into a Google account in Chrome and opens a Flow project page.
5. Admin clicks "Import current Flow account" in the extension options page.
6. The extension reads:
   - current `project_id` from the active tab URL
   - `__Secure-next-auth.session-token` from `labs.google` cookies
7. The extension posts the data to `/api/plugin/update-token`.
8. The backend converts ST to AT, discovers the Google email, and either adds a new token or updates the existing token for that email.
9. The token is enabled by default and appears in the admin token list.

## Backend Changes

Extend the existing `/api/plugin/update-token` endpoint so external importers can pass:

- `project_id`
- `project_name`
- `remark`
- `captcha_proxy_url`
- `extension_route_key`
- `image_enabled`
- `video_enabled`
- `image_concurrency`
- `video_concurrency`
- `protocol_mode`
- `google_cookies`
- `login_account`
- `login_password`
- `proxy_url`
- `auto_refresh_enabled`
- `refresh_interval_minutes`

For existing tokens, update the same fields through `token_manager.update_token`. For new tokens, pass the same fields to `token_manager.add_token`, including the provided `project_id` so the backend does not create an unnecessary first project when the operator is already on a Flow project page.

## Extension Changes

Add `cookies` permission and an import action to the extension options page. The extension should:

- validate that the configured connection URL is an HTTP(S) plugin endpoint
- find the current active tab
- require the tab URL to contain `/tools/flow/project/<uuid>` or `/flow/project/<uuid>`
- read the `__Secure-next-auth.session-token` cookie from `https://labs.google`
- call `/api/plugin/update-token` with `Authorization: Bearer <connection token>`
- show clear success or failure status without printing the ST

The existing WebSocket captcha behavior remains unchanged.

## Admin UI Changes

Add a compact "Account Pool Add Wizard" section near plugin settings that tells the operator exactly what to copy into the extension:

- connection URL
- connection token
- recommended route key
- reminder to keep Cloud/VPS URLs as HTTPS

No remote desktop or VNC is introduced in this first version.

## Security

- ST is never displayed after import.
- The backend requires the plugin connection token.
- The extension only requests cookies for `https://labs.google/*`.
- Existing admin session auth remains unchanged.

## Testing

Backend tests cover:

- plugin import adds a new token with `project_id`
- plugin import updates an existing token and keeps it enabled when auto-enable is active
- plugin import rejects missing or invalid connection tokens

Extension logic is kept small enough for manual verification in Chrome after deployment:

- import from a valid Flow project page succeeds
- import from a non-Flow page fails with a clear message
- missing ST fails with a clear message
