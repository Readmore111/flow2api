import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from src.core.config import config
from src.core.models import Token
from src.services.concurrency_manager import ConcurrencyManager
from src.services.generation_handler import GenerationHandler
from src.services.load_balancer import LoadBalancer


class FakeGenerationDB:
    def __init__(self):
        self.next_log_id = 1
        self.logs = {}

    async def add_request_log(self, log):
        log_id = self.next_log_id
        self.next_log_id += 1
        self.logs[log_id] = {
            "token_id": log.token_id,
            "status_code": log.status_code,
            "response_body": log.response_body,
            "status_text": log.status_text,
        }
        return log_id

    async def update_request_log(self, log_id, **updates):
        self.logs.setdefault(log_id, {}).update(updates)

    async def increment_api_client_success(self, client_id):
        return None


class FakeTokenManager:
    def __init__(self, token):
        self.token = token
        self.record_usage = AsyncMock()
        self.record_success = AsyncMock()
        self.record_error = AsyncMock()
        self.cooldown_token_after_failure = AsyncMock()

    async def get_active_tokens(self):
        return [self.token] if self.token.is_active else []

    def needs_at_refresh(self, token):
        return False

    async def ensure_valid_token(self, token):
        return token

    async def ensure_project_exists(self, token_id):
        return "project-1"


class FakeFlowClient:
    def __init__(self):
        self.clear_request_fingerprint_called = False
        self.prefill_remote_browser_pool = AsyncMock()
        self.generate_image = AsyncMock(return_value=self.image_success_result())

    def clear_request_fingerprint(self):
        self.clear_request_fingerprint_called = True

    @staticmethod
    def image_success_result():
        return (
            {
                "media": [
                    {
                        "name": "media-1",
                        "image": {
                            "generatedImage": {
                                "fifeUrl": "https://flow-content.google/image/11111111-1111-1111-1111-111111111111"
                            }
                        },
                    }
                ]
            },
            "session-1",
            {"generation_attempts": []},
        )


class GenerationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_flow_config = dict(config._config.get("flow", {}))
        self._original_captcha_method = config.captcha_method
        config._config.setdefault("flow", {})["image_slot_wait_timeout"] = 1
        config.set_captcha_method("personal")
        self.token = Token(
            id=1,
            st="st",
            at="at",
            email="token@example.com",
            image_concurrency=1,
            video_concurrency=-1,
            user_paygate_tier="PAYGATE_TIER_ONE",
        )
        self.db = FakeGenerationDB()
        self.flow_client = FakeFlowClient()
        self.token_manager = FakeTokenManager(self.token)
        self.concurrency_manager = ConcurrencyManager()
        await self.concurrency_manager.initialize([self.token])
        self.load_balancer = LoadBalancer(self.token_manager, self.concurrency_manager)
        self.handler = GenerationHandler(
            self.flow_client,
            self.token_manager,
            self.load_balancer,
            self.db,
            self.concurrency_manager,
            proxy_manager=None,
        )

    async def asyncTearDown(self):
        config._config["flow"] = self._original_flow_config
        config.set_captcha_method(self._original_captcha_method)

    async def _collect(self):
        chunks = []
        async for chunk in self.handler.handle_generation(
            model="gemini-3.0-pro-image-square",
            prompt="make a clean ecommerce image",
            stream=False,
        ):
            chunks.append(chunk)
        self.assertTrue(chunks)
        return json.loads(chunks[-1])

    async def test_image_request_waits_and_fails_when_token_concurrency_is_full(self):
        acquired = await self.concurrency_manager.acquire_image(self.token.id)
        self.assertTrue(acquired)

        payload = await self._collect()

        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["status_code"], 429)
        self.assertEqual(payload["error"]["code"], "concurrency_full")
        self.assertTrue(payload["error"]["retryable"])
        self.assertGreaterEqual(payload["error"]["retry_after_ms"], 1000)
        self.flow_client.generate_image.assert_not_awaited()
        self.assertEqual(await self.concurrency_manager.get_image_inflight(self.token.id), 1)

        await self.concurrency_manager.release_image(self.token.id)

    async def test_no_available_token_error_includes_retry_metadata(self):
        self.token.is_active = False

        payload = await self._collect()

        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["status_code"], 503)
        self.assertEqual(payload["error"]["code"], "no_token_available")
        self.assertTrue(payload["error"]["retryable"])
        self.assertGreaterEqual(payload["error"]["retry_after_ms"], 1000)
        self.flow_client.generate_image.assert_not_awaited()

    async def test_image_concurrency_slot_is_released_after_success(self):
        async def generate_image_with_slot_assertion(**kwargs):
            self.assertEqual(await self.concurrency_manager.get_image_inflight(self.token.id), 1)
            return FakeFlowClient.image_success_result()

        self.flow_client.generate_image.side_effect = generate_image_with_slot_assertion

        payload = await self._collect()

        self.assertNotIn("error", payload)
        self.flow_client.generate_image.assert_awaited_once()
        self.assertEqual(await self.concurrency_manager.get_image_inflight(self.token.id), 0)

    async def test_failed_request_log_triggers_token_failure_cooldown(self):
        await self.handler._log_request(
            token_id=self.token.id,
            operation="generate_image",
            request_data={"model": "gemini-3.0-pro-image-square"},
            response_data={"error": "upstream failed"},
            status_code=500,
            duration=1.25,
            status_text="failed",
            progress=48,
        )

        self.token_manager.cooldown_token_after_failure.assert_awaited_once_with(self.token.id)


if __name__ == "__main__":
    unittest.main()
