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

    async def test_plugin_config_uses_forwarded_https_scheme(self):
        request = SimpleNamespace(headers={
            "host": "niktokfurniture.com",
            "x-forwarded-proto": "https",
        })

        response = await admin.get_plugin_config(request, token="admin-session")

        self.assertTrue(response["success"])
        self.assertEqual(
            response["config"]["connection_url"],
            "https://niktokfurniture.com/api/plugin/update-token",
        )
