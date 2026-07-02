"""Mihomo node failover helper for Flow2API image-generation failures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, Iterable, Optional
from urllib import parse, request


DEFAULT_FAILURE_MARKERS = (
    "PUBLIC_ERROR_UNUSUAL_ACTIVITY",
    "TOO_MUCH_TRAFFIC",
    "reCAPTCHA evaluation failed",
)


@dataclass
class FailoverConfig:
    db_path: str = "/opt/flow2api/app/data/flow.db"
    state_path: str = "/var/lib/flow2api-proxy-failover/state.json"
    controller_url: str = "http://127.0.0.1:9090"
    controller_secret: str = ""
    selector: str = "FLOW2API"
    operation: str = "generate_image"
    failure_threshold: int = 3
    window_seconds: int = 600
    switch_cooldown_seconds: int = 300
    bad_node_cooldown_seconds: int = 600
    rotation_interval_seconds: int = 0
    probe_urls: tuple[str, ...] = (
        "https://www.google.com/generate_204",
        "https://labs.google/fx/zh/tools/flow",
    )
    probe_timeout_ms: int = 5000
    probe_parallelism: int = 8
    failure_markers: tuple[str, ...] = DEFAULT_FAILURE_MARKERS


@dataclass
class FailoverDecision:
    switched: bool
    reason: str
    failure_count: int = 0
    current_node: Optional[str] = None
    selected_node: Optional[str] = None
    max_log_id: int = 0


class MihomoClient:
    def __init__(self, controller_url: str, secret: str = ""):
        self.controller_url = controller_url.rstrip("/")
        self.secret = secret.strip()

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = None
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(
            f"{self.controller_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def get_selector(self, selector: str) -> Dict[str, Any]:
        return self._request("GET", f"/proxies/{parse.quote(selector, safe='')}")

    def probe_delay(self, node_name: str, timeout_ms: int, url: str) -> Optional[int]:
        query = parse.urlencode({"timeout": int(timeout_ms), "url": url})
        try:
            payload = self._request(
                "GET",
                f"/proxies/{parse.quote(node_name, safe='')}/delay?{query}",
            )
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        delay = payload.get("delay")
        return int(delay) if isinstance(delay, (int, float)) and delay >= 0 else None

    def switch(self, selector: str, node_name: str) -> None:
        self._request(
            "PUT",
            f"/proxies/{parse.quote(selector, safe='')}",
            {"name": node_name},
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _read_state(path: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _write_state(path: str, state: Dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _contains_failure_marker(row: sqlite3.Row, markers: Iterable[str]) -> bool:
    haystack = " ".join(
        str(row[key] or "")
        for key in row.keys()
        if key in {"result_summary", "error_message", "response_body", "status_text"}
    )
    return any(marker and marker in haystack for marker in markers)


def _recent_failure_count(config: FailoverConfig, state: Dict[str, Any], now: datetime) -> tuple[int, int]:
    since = now - timedelta(seconds=max(1, config.window_seconds))
    last_switch_log_id = int(state.get("last_switch_log_id") or 0)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM request_logs
            WHERE operation = ?
              AND id > ?
            ORDER BY id DESC
            LIMIT 200
            """,
            (config.operation, last_switch_log_id),
        ).fetchall()
    finally:
        conn.close()

    failure_count = 0
    max_log_id = last_switch_log_id
    for row in rows:
        row_id = int(row["id"])
        max_log_id = max(max_log_id, row_id)
        created_at = _parse_datetime(row["created_at"] if "created_at" in row.keys() else None)
        if created_at and created_at < since:
            continue
        status_code = int(row["status_code"] or 0) if "status_code" in row.keys() else 0
        if status_code >= 500 and _contains_failure_marker(row, config.failure_markers):
            failure_count += 1
    return failure_count, max_log_id


def _cooldown_active(state: Dict[str, Any], now: datetime, cooldown_seconds: int) -> bool:
    if cooldown_seconds <= 0:
        return False
    last_switch_at = _parse_datetime(state.get("last_switch_at"))
    if not last_switch_at:
        return False
    return now - last_switch_at < timedelta(seconds=cooldown_seconds)


def _bad_nodes(state: Dict[str, Any], now: datetime, cooldown_seconds: int) -> set[str]:
    raw = state.get("bad_nodes") or {}
    if not isinstance(raw, dict):
        return set()
    bad = set()
    for node, timestamp in raw.items():
        marked_at = _parse_datetime(timestamp)
        if marked_at and now - marked_at < timedelta(seconds=cooldown_seconds):
            bad.add(str(node))
    return bad


def _mark_bad_node(state: Dict[str, Any], node: str, now: datetime) -> None:
    bad_nodes = state.setdefault("bad_nodes", {})
    if isinstance(bad_nodes, dict):
        bad_nodes[node] = now.isoformat()


def _clear_bad_node(state: Dict[str, Any], node: str) -> None:
    bad_nodes = state.get("bad_nodes")
    if isinstance(bad_nodes, dict):
        bad_nodes.pop(node, None)


def _candidate_nodes(selector_payload: Dict[str, Any], state: Dict[str, Any], now: datetime, config: FailoverConfig) -> list[str]:
    current = str(selector_payload.get("now") or "")
    all_nodes = selector_payload.get("all") or []
    bad = _bad_nodes(state, now, config.bad_node_cooldown_seconds)
    excluded = {current, "DIRECT", "REJECT", "REJECT-DROP", "PASS"}
    ordered_nodes = [str(node) for node in all_nodes if str(node)]
    if current in ordered_nodes:
        current_index = ordered_nodes.index(current)
        ordered_nodes = ordered_nodes[current_index + 1 :] + ordered_nodes[:current_index]
    candidates = []
    for node in ordered_nodes:
        node_name = str(node)
        if not node_name or node_name in excluded or node_name in bad:
            continue
        candidates.append(node_name)
    return candidates


def _rotation_due(config: FailoverConfig, state: Dict[str, Any], now: datetime) -> bool:
    if config.rotation_interval_seconds <= 0:
        return False
    last_switch_at = _parse_datetime(state.get("last_switch_at"))
    if not last_switch_at:
        return True
    return now - last_switch_at >= timedelta(seconds=config.rotation_interval_seconds)


def _node_passes_all_probes(
    client: MihomoClient,
    node: str,
    config: FailoverConfig,
) -> bool:
    probe_urls = tuple(url for url in config.probe_urls if url)
    for url in probe_urls:
        delay = client.probe_delay(node, config.probe_timeout_ms, url)
        if delay is None:
            return False
    return True


def _choose_healthy_candidate(
    client: MihomoClient,
    candidates: list[str],
    state: Dict[str, Any],
    now: datetime,
    config: FailoverConfig,
) -> Optional[str]:
    parallelism = max(1, int(config.probe_parallelism or 1))
    if parallelism <= 1:
        for node in candidates:
            if _node_passes_all_probes(client, node, config):
                _clear_bad_node(state, node)
                return node
            _mark_bad_node(state, node, now)
        return None

    for start in range(0, len(candidates), parallelism):
        batch = candidates[start : start + parallelism]
        results: Dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            future_to_node = {
                executor.submit(_node_passes_all_probes, client, node, config): node
                for node in batch
            }
            for future, node in future_to_node.items():
                try:
                    results[node] = bool(future.result())
                except Exception:
                    results[node] = False
        for node in batch:
            if results.get(node):
                _clear_bad_node(state, node)
                return node
            _mark_bad_node(state, node, now)
    return None


def run_once(
    config: FailoverConfig,
    client: Optional[MihomoClient] = None,
    *,
    now: Optional[datetime] = None,
) -> FailoverDecision:
    now = (now or _utc_now()).astimezone(timezone.utc)
    state = _read_state(config.state_path)
    failure_count, max_log_id = _recent_failure_count(config, state, now)
    selector_payload = (client or MihomoClient(config.controller_url, config.controller_secret)).get_selector(config.selector)
    current_node = str(selector_payload.get("now") or "")
    rotation_due = _rotation_due(config, state, now)

    if failure_count < max(1, config.failure_threshold) and not rotation_due:
        return FailoverDecision(
            switched=False,
            reason="below_threshold",
            failure_count=failure_count,
            current_node=current_node,
            max_log_id=max_log_id,
        )

    if _cooldown_active(state, now, config.switch_cooldown_seconds):
        return FailoverDecision(
            switched=False,
            reason="switch_cooldown",
            failure_count=failure_count,
            current_node=current_node,
            max_log_id=max_log_id,
        )

    active_client = client or MihomoClient(config.controller_url, config.controller_secret)
    selected_node = _choose_healthy_candidate(
        active_client,
        _candidate_nodes(selector_payload, state, now, config),
        state,
        now,
        config,
    )
    if selected_node:
        active_client.switch(config.selector, selected_node)
        state["last_switch_at"] = now.isoformat()
        state["last_switch_log_id"] = max_log_id
        state["last_selected_node"] = selected_node
        _write_state(config.state_path, state)
        return FailoverDecision(
            switched=True,
            reason="scheduled_rotation" if rotation_due and failure_count < max(1, config.failure_threshold) else "switched",
            failure_count=failure_count,
            current_node=current_node,
            selected_node=selected_node,
            max_log_id=max_log_id,
        )

    _write_state(config.state_path, state)
    return FailoverDecision(
        switched=False,
        reason="no_healthy_candidate",
        failure_count=failure_count,
        current_node=current_node,
        max_log_id=max_log_id,
    )


def config_from_env() -> FailoverConfig:
    secret = os.environ.get("MIHOMO_SECRET", "")
    secret_file = os.environ.get("MIHOMO_SECRET_FILE", "")
    if secret_file and not secret:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            secret = ""
    probe_urls_raw = os.environ.get("FLOW2API_FAILOVER_PROBE_URLS", "")
    if probe_urls_raw:
        probe_urls = tuple(url.strip() for url in probe_urls_raw.split(",") if url.strip())
    elif os.environ.get("FLOW2API_FAILOVER_PROBE_URL"):
        probe_urls = (os.environ["FLOW2API_FAILOVER_PROBE_URL"].strip(),)
    else:
        probe_urls = FailoverConfig.probe_urls
    return FailoverConfig(
        db_path=os.environ.get("FLOW2API_DB_PATH", FailoverConfig.db_path),
        state_path=os.environ.get("FLOW2API_FAILOVER_STATE", FailoverConfig.state_path),
        controller_url=os.environ.get("MIHOMO_CONTROLLER_URL", FailoverConfig.controller_url),
        controller_secret=secret,
        selector=os.environ.get("MIHOMO_SELECTOR", FailoverConfig.selector),
        failure_threshold=int(os.environ.get("FLOW2API_FAILOVER_THRESHOLD", FailoverConfig.failure_threshold)),
        window_seconds=int(os.environ.get("FLOW2API_FAILOVER_WINDOW_SECONDS", FailoverConfig.window_seconds)),
        switch_cooldown_seconds=int(
            os.environ.get("FLOW2API_FAILOVER_SWITCH_COOLDOWN_SECONDS", FailoverConfig.switch_cooldown_seconds)
        ),
        rotation_interval_seconds=int(
            os.environ.get("FLOW2API_FAILOVER_ROTATION_INTERVAL_SECONDS", FailoverConfig.rotation_interval_seconds)
        ),
        bad_node_cooldown_seconds=int(
            os.environ.get("FLOW2API_FAILOVER_BAD_NODE_COOLDOWN_SECONDS", FailoverConfig.bad_node_cooldown_seconds)
        ),
        probe_urls=probe_urls,
        probe_timeout_ms=int(os.environ.get("FLOW2API_FAILOVER_PROBE_TIMEOUT_MS", FailoverConfig.probe_timeout_ms)),
        probe_parallelism=int(os.environ.get("FLOW2API_FAILOVER_PROBE_PARALLELISM", FailoverConfig.probe_parallelism)),
    )


def main() -> int:
    decision = run_once(config_from_env())
    output = decision.__dict__.copy()
    if output.get("current_node"):
        output["current_node"] = "[current]"
    if output.get("selected_node"):
        output["selected_node"] = "[selected]"
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
