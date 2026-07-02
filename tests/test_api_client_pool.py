import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.core.config import config
from src.core.database import Database
from src.core.models import ApiClient, Token
from src.services.load_balancer import LoadBalancer


class ApiClientPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(db_path=f"{self._temp_dir.name}/flow.db")
        self._original_captcha_method = config.captcha_method
        self._original_call_logic_mode = config.call_logic_mode
        config.set_captcha_method("personal")
        await self.db.init_db()
        self.token_a = await self.db.add_token(
            Token(st="st-a", at="at-a", email="a@example.com", name="A")
        )
        self.token_b = await self.db.add_token(
            Token(st="st-b", at="at-b", email="b@example.com", name="B")
        )

    async def asyncTearDown(self):
        config.set_captcha_method(self._original_captcha_method)
        config.set_call_logic_mode(self._original_call_logic_mode)
        self._temp_dir.cleanup()

    def _fake_token(self, token_id, cooldown_until=None):
        return SimpleNamespace(
            id=token_id,
            email=f"token-{token_id}@example.com",
            user_paygate_tier=None,
            image_enabled=True,
            video_enabled=True,
            credits=0,
            cooldown_until=cooldown_until,
        )

    def _balancer_for_fake_tokens(self, tokens):
        token_manager = SimpleNamespace(
            db=None,
            get_active_tokens=AsyncMock(return_value=tokens),
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        return LoadBalancer(token_manager)

    async def test_create_client_and_bind_tokens(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="Alice", api_key="alice-key", daily_limit=20)
        )
        await self.db.set_api_client_token_bindings(
            client_id,
            [
                {"token_id": self.token_b, "generation_type": "image"},
            ],
        )

        client = await self.db.get_api_client_by_key("alice-key")
        bindings = await self.db.get_api_client_token_bindings(client_id)

        self.assertEqual(client.id, client_id)
        self.assertEqual(client.name, "Alice")
        self.assertTrue(client.is_active)
        self.assertEqual(client.daily_limit, 20)
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].token_id, self.token_b)
        self.assertEqual(bindings[0].generation_type, "image")

    async def test_bound_client_selects_only_bound_token(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="Bob", api_key="bob-key")
        )
        await self.db.set_api_client_token_bindings(
            client_id,
            [{"token_id": self.token_b, "generation_type": "image"}],
        )

        token_manager = SimpleNamespace(
            db=self.db,
            get_active_tokens=self.db.get_active_tokens,
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        balancer = LoadBalancer(token_manager)

        selected = await balancer.select_token(
            for_image_generation=True,
            api_client={"id": client_id, "name": "Bob"},
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, self.token_b)

    async def test_bound_client_does_not_fallback_to_unbound_tokens(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="Carol", api_key="carol-key")
        )
        await self.db.set_api_client_token_bindings(
            client_id,
            [{"token_id": self.token_b, "generation_type": "image"}],
        )
        await self.db.update_token(self.token_b, is_active=False)

        token_manager = SimpleNamespace(
            db=self.db,
            get_active_tokens=self.db.get_active_tokens,
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        balancer = LoadBalancer(token_manager)

        selected = await balancer.select_token(
            for_image_generation=True,
            api_client={"id": client_id, "name": "Carol"},
        )

        self.assertIsNone(selected)

    async def test_unbound_client_does_not_use_default_admin_pool(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="No Pool", api_key="no-pool-key")
        )
        token_manager = SimpleNamespace(
            db=self.db,
            get_active_tokens=self.db.get_active_tokens,
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        balancer = LoadBalancer(token_manager)

        selected = await balancer.select_token(
            for_image_generation=True,
            api_client={"id": client_id, "name": "No Pool"},
        )

        self.assertIsNone(selected)

    async def test_token_rows_include_bound_clients_and_latest_log(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="Dana", api_key="dana-key")
        )
        await self.db.set_api_client_token_bindings(
            client_id,
            [{"token_id": self.token_a, "generation_type": "all"}],
        )
        await self.db.add_request_log(
            SimpleNamespace(
                token_id=self.token_a,
                api_client_id=client_id,
                api_client_name="Dana",
                operation="generate_image",
                request_body="{}",
                response_body="{}",
                status_code=200,
                duration=3.25,
                status_text="completed",
                progress=100,
            )
        )

        rows = await self.db.get_all_tokens_with_stats()
        row = next(item for item in rows if item["id"] == self.token_a)

        self.assertEqual(row["bound_clients"], ["Dana"])
        self.assertEqual(row["last_status_text"], "completed")
        self.assertEqual(row["last_status_code"], 200)
        self.assertEqual(row["last_duration"], 3.25)
        self.assertEqual(row["last_api_client_name"], "Dana")

    async def test_token_group_is_persisted_and_returned_with_stats(self):
        await self.db.update_token(self.token_a, token_group="家具组")

        token = await self.db.get_token(self.token_a)
        rows = await self.db.get_all_tokens_with_stats()
        row = next(item for item in rows if item["id"] == self.token_a)

        self.assertEqual(token.token_group, "家具组")
        self.assertEqual(row["token_group"], "家具组")

    async def test_requested_token_id_selects_that_token_when_allowed(self):
        token_manager = SimpleNamespace(
            db=self.db,
            get_active_tokens=self.db.get_active_tokens,
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        balancer = LoadBalancer(token_manager)

        selected = await balancer.select_token(
            for_image_generation=True,
            requested_token_id=self.token_b,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, self.token_b)

    async def test_requested_token_id_respects_api_client_bindings(self):
        client_id = await self.db.add_api_client(
            ApiClient(name="Eve", api_key="eve-key")
        )
        await self.db.set_api_client_token_bindings(
            client_id,
            [{"token_id": self.token_b, "generation_type": "image"}],
        )
        token_manager = SimpleNamespace(
            db=self.db,
            get_active_tokens=self.db.get_active_tokens,
            needs_at_refresh=lambda token: False,
            ensure_valid_token=AsyncMock(side_effect=lambda token: token),
        )
        balancer = LoadBalancer(token_manager)

        selected = await balancer.select_token(
            for_image_generation=True,
            api_client={"id": client_id, "name": "Eve"},
            requested_token_id=self.token_a,
        )

        self.assertIsNone(selected)

    async def test_load_balancer_skips_token_in_failure_cooldown(self):
        config.set_call_logic_mode("polling")
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        balancer = self._balancer_for_fake_tokens([
            self._fake_token(1, cooldown_until=future),
            self._fake_token(2),
        ])

        selected = await balancer.select_token(for_image_generation=True)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, 2)

    async def test_requested_token_cannot_bypass_failure_cooldown(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=2)
        balancer = self._balancer_for_fake_tokens([
            self._fake_token(1, cooldown_until=future),
            self._fake_token(2),
        ])

        selected = await balancer.select_token(
            for_image_generation=True,
            requested_token_id=1,
        )

        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()
