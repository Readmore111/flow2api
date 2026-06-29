import json
import unittest
from unittest.mock import AsyncMock, patch

from src.services.flow_client import FlowClient


class FlowImageProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_generation_submit_uses_current_flow_text_payload(self):
        client = FlowClient(proxy_manager=None)
        captured = {}
        payload = {
            "clientContext": {"projectId": "project-1", "tool": "PINHOLE"},
            "useNewMedia": True,
            "requests": [{"imageModelName": "NARWHAL"}],
        }

        async def fake_make_request(**kwargs):
            captured.update(kwargs)
            return {"media": []}

        client._make_request = AsyncMock(side_effect=fake_make_request)

        with patch("src.services.flow_client.config") as cfg:
            cfg.flow_image_request_timeout = 30
            cfg.flow_image_timeout_retry_count = 0
            cfg.flow_image_timeout_retry_delay = 0
            cfg.flow_image_timeout_use_media_proxy_fallback = False
            cfg.flow_image_prefer_media_proxy = False

            await client._make_image_generation_request(
                url="https://aisandbox-pa.googleapis.com/v1/projects/project-1/flowMedia:batchGenerateImages",
                json_data=payload,
                at="at-token",
                project_id="project-1",
            )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"]["Content-Type"], "text/plain;charset=UTF-8")
        self.assertEqual(
            captured["raw_body"],
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
        self.assertEqual(captured["json_data"], payload)
        self.assertTrue(captured["use_at"])
        self.assertEqual(captured["at_token"], "at-token")
        self.assertFalse(captured["apply_default_client_headers"])


if __name__ == "__main__":
    unittest.main()
