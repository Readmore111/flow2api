import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException, Response

from src.api import admin
from src.core.auth import AuthManager
from src.core.database import Database
from src.core.models import ApiClient, RequestLog, Token


class UserAccountScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(db_path=f"{self._temp_dir.name}/flow.db")
        await self.db.init_db()

        self.old_db = admin.db
        self.old_token_manager = admin.token_manager
        self.old_active_user_tokens = getattr(admin, "active_user_tokens", None)
        admin.db = self.db
        if hasattr(admin, "active_user_tokens"):
            admin.active_user_tokens.clear()

    async def asyncTearDown(self):
        admin.db = self.old_db
        admin.token_manager = self.old_token_manager
        if self.old_active_user_tokens is not None:
            admin.active_user_tokens = self.old_active_user_tokens
        self._temp_dir.cleanup()

    async def _create_user(self, username="alice", password="secret", plugin_token="plugin-alice"):
        return await self.db.add_api_client(
            ApiClient(
                name="Alice",
                username=username,
                password_hash=AuthManager.hash_password(password),
                api_key="alice-api-key",
                plugin_connection_token=plugin_token,
            )
        )

    async def test_user_can_login_with_created_account_password(self):
        client_id = await self._create_user()

        response = Response()
        payload = await admin.admin_login(
            admin.LoginRequest(username="alice", password="secret"),
            response,
        )

        self.assertTrue(payload["success"])
        self.assertEqual(payload["role"], "user")
        self.assertEqual(payload["client_id"], client_id)
        self.assertIn(payload["token"], admin.active_user_tokens)

    async def test_user_token_and_log_scope_is_limited_to_owned_tokens(self):
        client_id = await self._create_user()
        owned_token_id = await self.db.add_token(
            Token(st="st-owned", at="at-owned", email="owned@example.com", owner_client_id=client_id)
        )
        admin_token_id = await self.db.add_token(
            Token(st="st-admin", at="at-admin", email="admin@example.com")
        )
        await self.db.add_request_log(
            RequestLog(
                token_id=owned_token_id,
                api_client_id=client_id,
                api_client_name="Alice",
                operation="generate_image",
                request_body="{}",
                response_body="{}",
                status_code=200,
                duration=1,
            )
        )
        await self.db.add_request_log(
            RequestLog(
                token_id=admin_token_id,
                api_client_id=client_id,
                api_client_name="Alice",
                operation="generate_image",
                request_body="{}",
                response_body="{}",
                status_code=200,
                duration=1,
            )
        )

        visible_tokens = await self.db.get_all_tokens_with_stats(owner_client_id=client_id)
        visible_logs = await self.db.get_logs(owner_client_id=client_id)

        self.assertEqual([row["id"] for row in visible_tokens], [owned_token_id])
        self.assertEqual([row["token_id"] for row in visible_logs], [owned_token_id])

    async def test_user_dashboard_stats_scope_uses_owned_token_logs(self):
        client_id = await self._create_user()
        owned_token_id = await self.db.add_token(
            Token(st="st-owned", at="at-owned", email="owned@example.com", owner_client_id=client_id)
        )
        admin_token_id = await self.db.add_token(
            Token(st="st-admin", at="at-admin", email="admin@example.com")
        )
        await self.db.increment_image_count(owned_token_id)
        await self.db.add_request_log(
            RequestLog(
                token_id=owned_token_id,
                operation="generate_image",
                request_body="{}",
                response_body="{}",
                status_code=500,
                duration=1,
            )
        )
        await self.db.add_request_log(
            RequestLog(
                token_id=admin_token_id,
                operation="generate_image",
                request_body="{}",
                response_body="{}",
                status_code=500,
                duration=1,
            )
        )

        stats = await self.db.get_dashboard_stats(owner_client_id=client_id)

        self.assertEqual(stats["total_tokens"], 1)
        self.assertEqual(stats["today_images"], 1)
        self.assertEqual(stats["total_errors"], 1)

    async def test_user_plugin_import_sets_owner_and_default_binding(self):
        client_id = await self._create_user()
        plugin_config = SimpleNamespace(connection_token="admin-plugin", auto_enable_on_update=True)

        self.db.get_plugin_config = AsyncMock(return_value=plugin_config)
        flow_client = SimpleNamespace(
            st_to_at=AsyncMock(
                return_value={
                    "access_token": "at-new",
                    "expires": "2026-06-30T18:00:00Z",
                    "user": {"email": "new@example.com"},
                }
            )
        )
        async def fake_add_token(**kwargs):
            token_id = await self.db.add_token(
                Token(
                    st=kwargs["st"],
                    at="at-new",
                    email="new@example.com",
                    owner_client_id=kwargs.get("owner_client_id"),
                )
            )
            return await self.db.get_token(token_id)

        token_manager = SimpleNamespace(
            flow_client=flow_client,
            add_token=AsyncMock(side_effect=fake_add_token),
            update_token=AsyncMock(),
            enable_token=AsyncMock(),
        )
        admin.token_manager = token_manager

        payload = await admin.plugin_update_token(
            {"session_token": "st-new"},
            authorization="Bearer plugin-alice",
        )

        self.assertTrue(payload["success"])
        token_manager.add_token.assert_awaited_once()
        self.assertEqual(token_manager.add_token.await_args.kwargs["owner_client_id"], client_id)
        bindings = await self.db.get_api_client_token_bindings(client_id)
        self.assertEqual([(item.token_id, item.generation_type) for item in bindings], [(payload["token_id"], "all")])

    async def test_user_plugin_config_returns_personal_read_only_token(self):
        client_id = await self.db.add_api_client(
            ApiClient(
                name="Alice",
                username="alice",
                password_hash=AuthManager.hash_password("secret"),
                api_key="alice-api-key",
            )
        )
        client = await self.db.get_api_client(client_id)
        request = SimpleNamespace(headers={
            "host": "www.niktokfurniture.com",
            "x-forwarded-proto": "https",
        })

        payload = await admin.get_plugin_config(
            request,
            context={"role": "user", "client_id": client_id, "client": client},
        )
        updated_client = await self.db.get_api_client(client_id)

        self.assertTrue(payload["success"])
        self.assertTrue(payload["config"]["read_only"])
        self.assertEqual(payload["config"]["api_key"], "alice-api-key")
        self.assertEqual(
            payload["config"]["connection_url"],
            "https://www.niktokfurniture.com/api/plugin/update-token",
        )
        self.assertTrue(payload["config"]["connection_token"])
        self.assertEqual(payload["config"]["connection_token"], updated_client.plugin_connection_token)

    async def test_wrong_user_password_is_rejected(self):
        await self._create_user()

        with self.assertRaises(HTTPException) as ctx:
            await admin.admin_login(
                admin.LoginRequest(username="alice", password="wrong"),
                Response(),
            )

        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
