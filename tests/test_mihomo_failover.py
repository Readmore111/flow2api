import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.services.mihomo_failover import FailoverConfig, run_once


class FakeMihomoClient:
    def __init__(self, current="node-a", candidates=None, healthy=None, healthy_by_url=None):
        self.current = current
        self.candidates = candidates or ["node-a", "node-b", "node-c"]
        self.healthy = set(healthy or [])
        self.healthy_by_url = healthy_by_url or {}
        self.switched_to = []
        self.probed = []

    def get_selector(self, selector):
        return {"name": selector, "now": self.current, "all": list(self.candidates)}

    def probe_delay(self, node_name, timeout_ms, url):
        self.probed.append((node_name, url))
        if self.healthy_by_url:
            healthy_urls = set(self.healthy_by_url.get(node_name, []))
            if url not in healthy_urls:
                return None
            return 123
        if node_name not in self.healthy:
            return None
        return 123

    def switch(self, selector, node_name):
        self.current = node_name
        self.switched_to.append((selector, node_name))


class MihomoFailoverTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "flow.db"
        self.state_path = Path(self.temp_dir.name) / "state.json"
        self._init_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE request_logs (
                id INTEGER PRIMARY KEY,
                operation TEXT,
                status_code INTEGER,
                result_summary TEXT,
                error_message TEXT,
                response_body TEXT,
                created_at TEXT
            )
            """
        )
        conn.commit()
        conn.close()

    def _insert_log(self, log_id, status_code, created_at, text="PUBLIC_ERROR_UNUSUAL_ACTIVITY"):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO request_logs
                (id, operation, status_code, result_summary, error_message, response_body, created_at)
            VALUES (?, 'generate_image', ?, ?, '', '', ?)
            """,
            (log_id, status_code, text, created_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()

    def _config(self, threshold=3, cooldown_seconds=300):
        return FailoverConfig(
            db_path=str(self.db_path),
            state_path=str(self.state_path),
            selector="FLOW2API",
            failure_threshold=threshold,
            window_seconds=600,
            switch_cooldown_seconds=cooldown_seconds,
            bad_node_cooldown_seconds=600,
            probe_timeout_ms=1000,
            probe_parallelism=1,
            probe_urls=("https://www.google.com/generate_204", "https://labs.google/fx/zh/tools/flow"),
        )

    def test_does_not_switch_below_failure_threshold(self):
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
        self._insert_log(1, 500, now - timedelta(seconds=30))
        self._insert_log(2, 500, now - timedelta(seconds=20))
        client = FakeMihomoClient(healthy={"node-b"})

        decision = run_once(self._config(threshold=3), client, now=now)

        self.assertFalse(decision.switched)
        self.assertEqual(decision.failure_count, 2)
        self.assertEqual(client.switched_to, [])

    def test_switches_to_first_probe_healthy_node_after_frequent_failures(self):
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
        for idx in range(1, 4):
            self._insert_log(idx, 500, now - timedelta(seconds=idx))
        client = FakeMihomoClient(
            current="node-a",
            candidates=["node-a", "node-b", "node-c"],
            healthy={"node-c"},
        )

        decision = run_once(self._config(threshold=3), client, now=now)

        self.assertTrue(decision.switched)
        self.assertEqual(decision.selected_node, "node-c")
        self.assertEqual(
            client.probed,
            [
                ("node-b", "https://www.google.com/generate_204"),
                ("node-c", "https://www.google.com/generate_204"),
                ("node-c", "https://labs.google/fx/zh/tools/flow"),
            ],
        )
        self.assertEqual(client.switched_to, [("FLOW2API", "node-c")])
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["last_switch_log_id"], 3)

    def test_candidate_must_pass_flow_probe_url(self):
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
        for idx in range(1, 4):
            self._insert_log(idx, 500, now - timedelta(seconds=idx))
        client = FakeMihomoClient(
            current="node-a",
            candidates=["node-a", "node-b", "node-c"],
            healthy_by_url={
                "node-b": {"https://www.google.com/generate_204"},
                "node-c": {
                    "https://www.google.com/generate_204",
                    "https://labs.google/fx/zh/tools/flow",
                },
            },
        )

        decision = run_once(self._config(threshold=3), client, now=now)

        self.assertTrue(decision.switched)
        self.assertEqual(decision.selected_node, "node-c")
        self.assertEqual(client.switched_to, [("FLOW2API", "node-c")])

    def test_scheduled_rotation_switches_without_recent_failures(self):
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
        self.state_path.write_text(
            json.dumps(
                {
                    "last_switch_at": (now - timedelta(hours=2)).isoformat(),
                    "last_switch_log_id": 0,
                }
            )
        )
        config = self._config(threshold=3, cooldown_seconds=300)
        config.rotation_interval_seconds = 3600
        client = FakeMihomoClient(current="node-a", healthy={"node-b"})

        decision = run_once(config, client, now=now)

        self.assertTrue(decision.switched)
        self.assertEqual(decision.reason, "scheduled_rotation")
        self.assertEqual(decision.failure_count, 0)
        self.assertEqual(client.switched_to, [("FLOW2API", "node-b")])

    def test_switch_cooldown_prevents_repeated_switches(self):
        now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
        for idx in range(1, 4):
            self._insert_log(idx, 500, now - timedelta(seconds=idx))
        self.state_path.write_text(
            json.dumps(
                {
                    "last_switch_at": (now - timedelta(seconds=60)).isoformat(),
                    "last_switch_log_id": 0,
                }
            )
        )
        client = FakeMihomoClient(healthy={"node-b"})

        decision = run_once(self._config(threshold=3, cooldown_seconds=300), client, now=now)

        self.assertFalse(decision.switched)
        self.assertEqual(decision.reason, "switch_cooldown")
        self.assertEqual(client.switched_to, [])


if __name__ == "__main__":
    unittest.main()
