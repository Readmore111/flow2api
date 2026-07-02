from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import unittest

from src.core.models import Token
from src.services.token_manager import TokenManager


class FakeRefreshDB:
    def __init__(self, tokens):
        self.tokens = {token.id: token for token in tokens}
        self.config = SimpleNamespace(enabled=True, refresh_interval_minutes=120)

    async def get_token_refresh_config(self):
        return self.config

    async def get_all_tokens(self):
        return list(self.tokens.values())

    async def get_token(self, token_id):
        return self.tokens.get(token_id)

    async def update_token(self, token_id, **updates):
        token = self.tokens[token_id]
        for key, value in updates.items():
            setattr(token, key, value)

    async def reset_error_count(self, token_id):
        return None


class FakeFlowClient:
    def __init__(self):
        self.st_to_at = AsyncMock()
        self.get_credits = AsyncMock()
        self._fingerprint = None

    def get_request_fingerprint(self):
        return self._fingerprint

    def _set_request_fingerprint(self, fingerprint):
        self._fingerprint = fingerprint


class TokenManagerRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_at_validation_records_reason_and_marks_refresh_ban(self):
        token = Token(
            id=1,
            st="session-token",
            at="old-at",
            email="bad@example.com",
            is_active=True,
            at_expires=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db = FakeRefreshDB([token])
        flow = FakeFlowClient()
        flow.st_to_at.return_value = {
            "access_token": "new-at",
            "expires": "2026-07-01T00:00:00.000Z",
            "user": {"email": token.email},
        }
        flow.get_credits.side_effect = Exception("HTTP Error 401: invalid authentication credentials")
        manager = TokenManager(db, flow)
        manager._try_refresh_st = AsyncMock(return_value=None)

        result = await manager._refresh_at_inner(token.id)

        self.assertFalse(result)
        self.assertFalse(token.is_active)
        self.assertEqual(token.ban_reason, "at_refresh_failed")
        self.assertIsNotNone(token.banned_at)
        self.assertIn("401", token.last_st_refresh_result)
        self.assertIsNotNone(token.last_st_refresh_at)

    async def test_protocol_keepalive_reenables_token_after_refresh_failure_ban(self):
        token = Token(
            id=2,
            st="old-session",
            at="old-at",
            email="recover@example.com",
            is_active=False,
            ban_reason="at_refresh_failed",
            banned_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            protocol_mode="protocol",
            google_cookies="SID=value;",
            auto_refresh_enabled=True,
            refresh_interval_minutes=1,
            last_st_refresh_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db = FakeRefreshDB([token])
        flow = FakeFlowClient()
        flow.st_to_at.return_value = {
            "access_token": "new-at",
            "expires": "2026-07-01T00:00:00.000Z",
            "user": {"email": token.email, "name": "Recover"},
        }
        flow.get_credits.return_value = {"credits": 12, "userPaygateTier": "PAYGATE_TIER_ONE"}
        manager = TokenManager(db, flow)
        manager._try_protocol_refresh_st = AsyncMock(return_value="new-session")

        await manager.run_protocol_refresh_once()

        self.assertTrue(token.is_active)
        self.assertIsNone(token.ban_reason)
        self.assertIsNone(token.banned_at)
        self.assertEqual(token.st, "new-session")
        self.assertEqual(token.at, "new-at")
        self.assertEqual(token.credits, 12)

    async def test_active_session_token_near_expiry_is_refreshed_by_keepalive_loop(self):
        token = Token(
            id=3,
            st="session-token",
            at="old-at",
            email="session@example.com",
            is_active=True,
            protocol_mode="session",
            auto_refresh_enabled=True,
            at_expires=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db = FakeRefreshDB([token])
        flow = FakeFlowClient()
        manager = TokenManager(db, flow)
        manager._refresh_at = AsyncMock(return_value=True)

        await manager.run_protocol_refresh_once()

        manager._refresh_at.assert_awaited_once_with(token.id)


if __name__ == "__main__":
    unittest.main()
