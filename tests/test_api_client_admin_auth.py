import tempfile
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from src.api import admin, routes
from src.core.config import config
from src.core.database import Database
from src.core.models import Token


class ApiClientAdminAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(db_path=f"{self._temp_dir.name}/flow.db")
        await self.db.init_db()
        self.token_id = await self.db.add_token(
            Token(st="st-client", at="at-client", email="client@example.com", name="Client")
        )
        self.old_admin_db = admin.db
        self.old_routes_handler = routes.generation_handler
        self.old_api_key = config.api_key
        config.api_key = "legacy-key"
        admin.db = self.db
        routes.generation_handler = SimpleNamespace(db=self.db)

    async def asyncTearDown(self):
        admin.db = self.old_admin_db
        routes.generation_handler = self.old_routes_handler
        config.api_key = self.old_api_key
        self._temp_dir.cleanup()

    async def test_create_update_bind_and_list_api_client(self):
        created = await admin.create_api_client(
            admin.ApiClientRequest(
                name="Alice",
                username="alice",
                password="alice-pass",
                daily_limit=10,
            ),
            token="admin-session",
        )
        client_id = created["client"]["id"]

        self.assertTrue(created["success"])
        self.assertEqual(created["client"]["name"], "Alice")
        self.assertEqual(created["client"]["username"], "alice")
        self.assertTrue(created["client"]["api_key"].startswith("flow2api_"))

        binding_response = await admin.save_api_client_bindings(
            client_id,
            admin.ApiClientBindingsRequest(
                bindings=[admin.ApiClientBindingRequest(token_id=self.token_id, generation_type="image")]
            ),
            token="admin-session",
        )
        self.assertTrue(binding_response["success"])

        await admin.update_api_client(
            client_id,
            admin.ApiClientRequest(name="Alice Updated", is_active=False, daily_limit=5),
            token="admin-session",
        )
        clients = await admin.list_api_clients(token="admin-session")
        client = next(item for item in clients["clients"] if item["id"] == client_id)

        self.assertEqual(client["name"], "Alice Updated")
        self.assertFalse(client["is_active"])
        self.assertEqual(client["daily_limit"], 5)
        self.assertEqual(client["binding_count"], 1)
        self.assertNotIn(created["client"]["api_key"], client["api_key"])

    async def test_get_detail_returns_full_key_and_delete_removes_client(self):
        created = await admin.create_api_client(
            admin.ApiClientRequest(
                name="Detail User",
                username="detail-user",
                password="detail-pass",
                api_key="detail-user-key",
            ),
            token="admin-session",
        )
        client_id = created["client"]["id"]
        await admin.save_api_client_bindings(
            client_id,
            admin.ApiClientBindingsRequest(
                bindings=[admin.ApiClientBindingRequest(token_id=self.token_id, generation_type="all")]
            ),
            token="admin-session",
        )

        listed = await admin.list_api_clients(token="admin-session")
        listed_client = next(item for item in listed["clients"] if item["id"] == client_id)
        detail = await admin.get_api_client_detail(client_id, token="admin-session")

        self.assertNotEqual(listed_client["api_key"], "detail-user-key")
        self.assertEqual(detail["client"]["api_key"], "detail-user-key")
        self.assertEqual(detail["client"]["binding_count"], 1)
        self.assertEqual(detail["client"]["bindings"][0]["token_id"], self.token_id)

        deleted = await admin.delete_api_client(client_id, token="admin-session")
        self.assertTrue(deleted["success"])

        with self.assertRaises(HTTPException) as ctx:
            await admin.get_api_client_detail(client_id, token="admin-session")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_resolve_api_client_context_accepts_client_and_legacy_keys(self):
        created = await admin.create_api_client(
            admin.ApiClientRequest(
                name="Bob",
                username="bob",
                password="bob-pass",
                api_key="bob-key",
            ),
            token="admin-session",
        )

        client_context = await routes._resolve_api_client_context("bob-key")
        legacy_context = await routes._resolve_api_client_context("legacy-key")

        self.assertEqual(client_context["id"], created["client"]["id"])
        self.assertEqual(client_context["name"], "Bob")
        self.assertFalse(client_context["is_legacy"])
        self.assertIsNone(legacy_context["id"])
        self.assertTrue(legacy_context["is_legacy"])

    async def test_resolve_api_client_context_rejects_disabled_client(self):
        created = await admin.create_api_client(
            admin.ApiClientRequest(
                name="Disabled",
                username="disabled",
                password="disabled-pass",
                api_key="disabled-key",
                is_active=False,
            ),
            token="admin-session",
        )
        self.assertTrue(created["success"])

        with self.assertRaises(HTTPException) as ctx:
            await routes._resolve_api_client_context("disabled-key")

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_create_api_client_requires_login_credentials(self):
        with self.assertRaises(HTTPException) as missing_username:
            await admin.create_api_client(
                admin.ApiClientRequest(name="No Username", password="pw"),
                token="admin-session",
            )
        self.assertEqual(missing_username.exception.status_code, 400)

        with self.assertRaises(HTTPException) as missing_password:
            await admin.create_api_client(
                admin.ApiClientRequest(name="No Password", username="no-password"),
                token="admin-session",
            )
        self.assertEqual(missing_password.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
