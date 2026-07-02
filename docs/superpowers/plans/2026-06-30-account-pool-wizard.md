# Account Pool Add Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chrome-extension based account pool wizard that imports the currently logged-in Google Flow account into Flow2API.

**Architecture:** Reuse the existing plugin connection-token endpoint as the secure ingestion path. Extend the backend endpoint to accept project and capability fields, then add an extension options-page button that reads the current Flow project URL and `labs.google` ST cookie before posting to the backend.

**Tech Stack:** FastAPI, Pydantic, unittest async tests, Chrome Manifest V3 extension APIs.

---

## File Structure

- Modify `src/api/admin.py`: accept and pass through project/capability fields in `/api/plugin/update-token`.
- Create `tests/test_plugin_account_import.py`: unit-test the plugin import helper behavior through the FastAPI route function with fakes.
- Modify `extension/manifest.json`: add `cookies` permission.
- Modify `extension/options.html`: add account import controls.
- Modify `extension/options.js`: implement active-tab project detection, cookie lookup, and plugin POST.
- Modify `static/manage.html`: add a compact admin-facing wizard/help block near plugin settings.
- Deploy changed files to `/opt/flow2api/app` and rebuild the headed Docker image.

---

### Task 1: Backend Plugin Import Pass-Through

**Files:**
- Test: `tests/test_plugin_account_import.py`
- Modify: `src/api/admin.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_account_import.py` with tests that fake `db`, `token_manager`, and plugin config:

```python
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from src.api import admin


class PluginAccountImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_db = admin.db
        self.old_token_manager = admin.token_manager
        self.plugin_config = SimpleNamespace(
            connection_token="secret",
            auto_enable_on_update=True,
        )
        self.db = SimpleNamespace(
            get_plugin_config=AsyncMock(return_value=self.plugin_config),
            get_token_by_email=AsyncMock(return_value=None),
        )
        self.flow_client = SimpleNamespace(
            st_to_at=AsyncMock(return_value={
                "access_token": "at-new",
                "expires": "2026-06-30T18:00:00Z",
                "user": {"email": "new@example.com"},
            })
        )
        self.token_manager = SimpleNamespace(
            flow_client=self.flow_client,
            add_token=AsyncMock(return_value=SimpleNamespace(
                id=7,
                email="new@example.com",
            )),
            update_token=AsyncMock(),
            enable_token=AsyncMock(),
        )
        admin.db = self.db
        admin.token_manager = self.token_manager

    async def asyncTearDown(self):
        admin.db = self.old_db
        admin.token_manager = self.old_token_manager

    async def test_adds_new_token_with_project_and_capability_fields(self):
        response = await admin.plugin_update_token(
            {
                "session_token": "st-new",
                "project_id": "project-123",
                "project_name": "Imported Project",
                "remark": "Imported by wizard",
                "image_enabled": True,
                "video_enabled": False,
                "image_concurrency": 2,
                "video_concurrency": 0,
                "extension_route_key": "acct-02",
                "protocol_mode": "session",
                "auto_refresh_enabled": True,
                "refresh_interval_minutes": 90,
            },
            authorization="Bearer secret",
        )

        self.assertTrue(response["success"])
        self.assertEqual(response["action"], "added")
        self.token_manager.add_token.assert_awaited_once()
        kwargs = self.token_manager.add_token.await_args.kwargs
        self.assertEqual(kwargs["st"], "st-new")
        self.assertEqual(kwargs["project_id"], "project-123")
        self.assertEqual(kwargs["project_name"], "Imported Project")
        self.assertEqual(kwargs["remark"], "Imported by wizard")
        self.assertEqual(kwargs["video_enabled"], False)
        self.assertEqual(kwargs["image_concurrency"], 2)
        self.assertEqual(kwargs["extension_route_key"], "acct-02")
        self.assertEqual(kwargs["refresh_interval_minutes"], 90)

    async def test_updates_existing_token_and_auto_enables_when_configured(self):
        self.db.get_token_by_email.return_value = SimpleNamespace(
            id=3,
            email="new@example.com",
            is_active=False,
        )

        response = await admin.plugin_update_token(
            {
                "session_token": "st-updated",
                "project_id": "project-updated",
                "image_enabled": True,
                "video_enabled": False,
            },
            authorization="Bearer secret",
        )

        self.assertTrue(response["success"])
        self.assertEqual(response["action"], "updated")
        self.assertTrue(response["auto_enabled"])
        self.token_manager.update_token.assert_awaited_once()
        kwargs = self.token_manager.update_token.await_args.kwargs
        self.assertEqual(kwargs["token_id"], 3)
        self.assertEqual(kwargs["project_id"], "project-updated")
        self.assertEqual(kwargs["video_enabled"], False)
        self.token_manager.enable_token.assert_awaited_once_with(3)

    async def test_rejects_invalid_connection_token(self):
        with self.assertRaises(HTTPException) as ctx:
            await admin.plugin_update_token(
                {"session_token": "st-new"},
                authorization="Bearer wrong",
            )
        self.assertEqual(ctx.exception.status_code, 401)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_plugin_account_import -v`

Expected: first two tests fail because `project_id`, `project_name`, and capability fields are not passed through yet.

- [ ] **Step 3: Implement minimal backend changes**

In `src/api/admin.py`, inside `plugin_update_token`, pass `request.get(...)` values for:

```python
project_id=request.get("project_id"),
project_name=request.get("project_name"),
remark=request.get("remark", "Added by Chrome Extension"),
captcha_proxy_url=request.get("captcha_proxy_url"),
extension_route_key=request.get("extension_route_key"),
image_enabled=request.get("image_enabled", True),
video_enabled=request.get("video_enabled", True),
image_concurrency=request.get("image_concurrency", -1),
video_concurrency=request.get("video_concurrency", -1),
```

Do this for both `token_manager.add_token(...)` and `token_manager.update_token(...)`, using `None` defaults for optional update fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_plugin_account_import -v`

Expected: all tests pass.

---

### Task 2: Extension Import Action

**Files:**
- Modify: `extension/manifest.json`
- Modify: `extension/options.html`
- Modify: `extension/options.js`

- [ ] **Step 1: Write manual test checklist before code**

Record these checks in the final verification notes:

```text
1. Extension options page saves HTTPS plugin URL and token.
2. Import button on a Flow project tab sends project_id and ST.
3. Import button on a non-Flow tab shows a clear error.
4. Missing labs.google ST shows a clear error.
```

- [ ] **Step 2: Add manifest permission**

Add `"cookies"` to `permissions` in `extension/manifest.json`.

- [ ] **Step 3: Add options-page controls**

In `extension/options.html`, add a second button after the save button:

```html
<button id="importBtn" type="button">导入当前 Flow 账号</button>
```

Add a hint telling the operator to open a Flow project page first.

- [ ] **Step 4: Implement import logic**

In `extension/options.js`, add:

```javascript
function toPluginEndpoint(value) {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("连接接口必须是 http:// 或 https://");
  }
  return url.toString();
}

function extractProjectId(tabUrl) {
  const url = new URL(tabUrl || "");
  const match = url.pathname.match(/\/flow\/project\/([0-9a-f-]{20,})/i);
  if (!match) throw new Error("请先打开 Google Flow 项目页面");
  return match[1];
}
```

Then use `chrome.tabs.query({active: true, currentWindow: true})`, `chrome.cookies.get({url: "https://labs.google", name: "__Secure-next-auth.session-token"})`, and `fetch(connectionUrl, {method: "POST", headers: {"Authorization": "Bearer " + token, "Content-Type": "application/json"}, body: JSON.stringify(...)})`.

- [ ] **Step 5: Syntax-check extension scripts**

Run: `node --check extension/options.js && node --check extension/background.js`

Expected: no syntax errors.

---

### Task 3: Admin Wizard Help

**Files:**
- Modify: `static/manage.html`

- [ ] **Step 1: Add a compact guide block**

Near the plugin settings card, add a bordered block titled `账号池添加向导` that says:

```text
1. 复制连接接口和连接 Token 到 Chrome 扩展
2. 在 Chrome 登录 Google 账号并打开 Flow 项目
3. 在扩展里点击“导入当前 Flow 账号”
4. 回到这里刷新 Token 列表
```

- [ ] **Step 2: Keep the current plugin config code working**

Do not change `loadPluginConfig`, `savePluginConfig`, `copyConnectionUrl`, or `copyConnectionToken` signatures.

- [ ] **Step 3: Verify the page still serves**

Run: `python -m unittest tests.test_plugin_account_import -v` again, then launch or deploy and check `/` loads after login.

---

### Task 4: Full Verification And Deployment

**Files:**
- Deploy changed source files to `/opt/flow2api/app`

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
python -m unittest tests.test_plugin_account_import -v
node --check extension/options.js
node --check extension/background.js
```

- [ ] **Step 2: Rebuild and restart cloud service**

Run:

```powershell
scp src\api\admin.py ubuntu@15.204.119.78:/opt/flow2api/app/src/api/admin.py
scp static\manage.html ubuntu@15.204.119.78:/opt/flow2api/app/static/manage.html
scp extension\manifest.json extension\options.html extension\options.js ubuntu@15.204.119.78:/opt/flow2api/app/extension/
ssh ubuntu@15.204.119.78 "cd /opt/flow2api && docker compose -f docker-compose.prod.yml up -d --build --force-recreate"
```

- [ ] **Step 3: Verify cloud health**

Run:

```powershell
curl.exe -sS https://niktokfurniture.com/health
```

Expected: `backend_running` is true.

- [ ] **Step 4: Explain extension reload to operator**

Tell the operator to open `chrome://extensions`, enable Developer Mode, load unpacked extension from the local or deployed `extension` folder, then configure the options page with:

```text
Connection URL: https://niktokfurniture.com/api/plugin/update-token
Connection Token: value from admin settings
WebSocket URL: wss://niktokfurniture.com/captcha_ws
API Key: current Flow2API API key
```
