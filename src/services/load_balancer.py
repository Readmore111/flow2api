"""Load balancing module for Flow2API."""
import asyncio
import random
from datetime import datetime, timezone
from typing import Dict, Optional

from ..core.account_tiers import (
    get_paygate_tier_label,
    get_required_paygate_tier_for_model,
    normalize_user_paygate_tier,
    supports_model_for_tier,
)
from ..core.config import config
from ..core.logger import debug_logger
from ..core.models import Token
from .concurrency_manager import ConcurrencyManager


class LoadBalancer:
    """Token load balancer with load-aware and client-aware selection."""

    def __init__(self, token_manager, concurrency_manager: Optional[ConcurrencyManager] = None):
        self.token_manager = token_manager
        self.concurrency_manager = concurrency_manager
        self._image_pending: Dict[int, int] = {}
        self._video_pending: Dict[int, int] = {}
        self._pending_lock = asyncio.Lock()
        self._round_robin_state: Dict[str, Optional[int]] = {
            "image": None,
            "video": None,
            "default": None,
        }
        self._rr_lock = asyncio.Lock()

    async def _get_pending_count(self, token_id: int, for_image_generation: bool, for_video_generation: bool) -> int:
        async with self._pending_lock:
            if for_image_generation:
                return max(0, int(self._image_pending.get(token_id, 0)))
            if for_video_generation:
                return max(0, int(self._video_pending.get(token_id, 0)))
            return 0

    async def _add_pending(self, token_id: int, for_image_generation: bool, for_video_generation: bool):
        async with self._pending_lock:
            if for_image_generation:
                self._image_pending[token_id] = max(0, int(self._image_pending.get(token_id, 0))) + 1
            elif for_video_generation:
                self._video_pending[token_id] = max(0, int(self._video_pending.get(token_id, 0))) + 1

    async def release_pending(self, token_id: int, for_image_generation: bool = False, for_video_generation: bool = False):
        async with self._pending_lock:
            if for_image_generation:
                current = max(0, int(self._image_pending.get(token_id, 0)))
                if current <= 1:
                    self._image_pending.pop(token_id, None)
                else:
                    self._image_pending[token_id] = current - 1
            elif for_video_generation:
                current = max(0, int(self._video_pending.get(token_id, 0)))
                if current <= 1:
                    self._video_pending.pop(token_id, None)
                else:
                    self._video_pending[token_id] = current - 1

    async def _get_token_load(self, token_id: int, for_image_generation: bool, for_video_generation: bool) -> tuple[int, Optional[int]]:
        if not self.concurrency_manager:
            return 0, None

        if for_image_generation:
            inflight = await self.concurrency_manager.get_image_inflight(token_id)
            remaining = await self.concurrency_manager.get_image_remaining(token_id)
            pending = await self._get_pending_count(token_id, True, False)
            effective_inflight = inflight + pending
            if remaining is not None:
                remaining = max(0, remaining - pending)
            return effective_inflight, remaining

        if for_video_generation:
            inflight = await self.concurrency_manager.get_video_inflight(token_id)
            remaining = await self.concurrency_manager.get_video_remaining(token_id)
            pending = await self._get_pending_count(token_id, False, True)
            effective_inflight = inflight + pending
            if remaining is not None:
                remaining = max(0, remaining - pending)
            return effective_inflight, remaining

        return 0, None

    async def _reserve_slot(self, token_id: int, for_image_generation: bool, for_video_generation: bool) -> bool:
        if not self.concurrency_manager:
            return True
        if for_image_generation:
            return await self.concurrency_manager.acquire_image(token_id)
        if for_video_generation:
            return await self.concurrency_manager.acquire_video(token_id)
        return True

    async def _select_round_robin(self, tokens: list[dict], scenario: str) -> Optional[dict]:
        if not tokens:
            return None

        tokens_sorted = sorted(tokens, key=lambda item: item["token"].id or 0)
        async with self._rr_lock:
            last_id = self._round_robin_state.get(scenario)
            start_idx = 0
            if last_id is not None:
                for idx, item in enumerate(tokens_sorted):
                    if item["token"].id == last_id:
                        start_idx = (idx + 1) % len(tokens_sorted)
                        break
            selected = tokens_sorted[start_idx]
            self._round_robin_state[scenario] = selected["token"].id
        return selected

    async def _check_extension_route(self, token: Token) -> tuple[bool, str]:
        if config.captcha_method != "extension":
            return True, ""

        try:
            from .browser_captcha_extension import ExtensionCaptchaService

            service = await ExtensionCaptchaService.get_instance(getattr(self.token_manager, "db", None))
            has_connection, route_key = await service.has_connection_for_token(token.id)
            if has_connection:
                return True, ""

            available = service.describe_routes() or "none"
            if route_key:
                return False, f"Extension route {route_key} is not connected. Available routes: {available}"
            return False, f"Extension route is missing or disconnected. Available routes: {available}"
        except Exception as exc:
            return False, f"Extension route check failed: {exc}"

    async def _filter_tokens_for_client(
        self,
        active_tokens: list[Token],
        api_client: Optional[Dict],
        for_image_generation: bool,
        for_video_generation: bool,
    ) -> list[Token]:
        client_id = (api_client or {}).get("id")
        if not client_id or not getattr(self.token_manager, "db", None):
            return active_tokens

        generation_type = "video" if for_video_generation else "image" if for_image_generation else "all"
        bound_token_ids = await self.token_manager.db.get_bound_token_ids_for_client(
            int(client_id),
            generation_type,
        )

        filtered = [token for token in active_tokens if token.id in bound_token_ids]
        debug_logger.log_info(
            f"[LOAD_BALANCER] API client {client_id} bound token filter: {sorted(bound_token_ids)}"
        )
        return filtered

    def _filter_requested_token(
        self,
        active_tokens: list[Token],
        requested_token_id: Optional[int],
    ) -> list[Token]:
        if not requested_token_id:
            return active_tokens
        return [token for token in active_tokens if token.id == int(requested_token_id)]

    def _parse_datetime(self, value) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except Exception:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _failure_cooldown_remaining_seconds(self, token: Token) -> float:
        cooldown_until = self._parse_datetime(getattr(token, "cooldown_until", None))
        if not cooldown_until:
            return 0.0
        return max(0.0, (cooldown_until - datetime.now(timezone.utc)).total_seconds())

    async def select_token(
        self,
        for_image_generation: bool = False,
        for_video_generation: bool = False,
        model: Optional[str] = None,
        api_client: Optional[Dict] = None,
        requested_token_id: Optional[int] = None,
        reserve: bool = False,
        enforce_concurrency_filter: bool = True,
        track_pending: bool = False,
    ) -> Optional[Token]:
        """Select an available token using capability, binding, and load filters."""
        debug_logger.log_info(
            f"[LOAD_BALANCER] selecting token image={for_image_generation}, "
            f"video={for_video_generation}, model={model}, reserve={reserve}"
        )

        active_tokens = await self.token_manager.get_active_tokens()
        active_tokens = await self._filter_tokens_for_client(
            active_tokens,
            api_client,
            for_image_generation,
            for_video_generation,
        )
        active_tokens = self._filter_requested_token(active_tokens, requested_token_id)
        debug_logger.log_info(f"[LOAD_BALANCER] active token candidates: {len(active_tokens)}")

        if not active_tokens:
            return None

        available_tokens = []
        filtered_reasons = {}
        required_tier = get_required_paygate_tier_for_model(model)

        for token in active_tokens:
            cooldown_remaining = self._failure_cooldown_remaining_seconds(token)
            if cooldown_remaining > 0:
                filtered_reasons[token.id] = f"failure cooldown active ({int(cooldown_remaining)}s remaining)"
                continue

            normalized_tier = normalize_user_paygate_tier(token.user_paygate_tier)
            if model and not supports_model_for_tier(model, normalized_tier):
                filtered_reasons[token.id] = "account tier requires " + get_paygate_tier_label(required_tier)
                continue

            if for_image_generation:
                if not token.image_enabled:
                    filtered_reasons[token.id] = "image generation disabled"
                    continue
                route_ok, route_reason = await self._check_extension_route(token)
                if not route_ok:
                    filtered_reasons[token.id] = route_reason
                    continue
                if (
                    enforce_concurrency_filter
                    and self.concurrency_manager
                    and not await self.concurrency_manager.can_use_image(token.id)
                ):
                    filtered_reasons[token.id] = "image concurrency full"
                    continue

            if for_video_generation:
                if not token.video_enabled:
                    filtered_reasons[token.id] = "video generation disabled"
                    continue
                route_ok, route_reason = await self._check_extension_route(token)
                if not route_ok:
                    filtered_reasons[token.id] = route_reason
                    continue
                if (
                    enforce_concurrency_filter
                    and self.concurrency_manager
                    and not await self.concurrency_manager.can_use_video(token.id)
                ):
                    filtered_reasons[token.id] = "video concurrency full"
                    continue

            inflight, remaining = await self._get_token_load(
                token.id,
                for_image_generation=for_image_generation,
                for_video_generation=for_video_generation,
            )
            available_tokens.append({
                "token": token,
                "inflight": inflight,
                "remaining": remaining,
                "needs_refresh": self.token_manager.needs_at_refresh(token),
                "random": random.random(),
            })

        if filtered_reasons:
            for token_id, reason in filtered_reasons.items():
                debug_logger.log_info(f"[LOAD_BALANCER] filtered token {token_id}: {reason}")

        if not available_tokens:
            return None

        call_mode = config.call_logic_mode
        if call_mode == "polling":
            scenario = "image" if for_image_generation else "video" if for_video_generation else "default"
            ordered_candidates = []
            first_candidate = await self._select_round_robin(available_tokens, scenario)
            if first_candidate is not None:
                ordered_candidates.append(first_candidate)
                ordered_candidates.extend(
                    item for item in sorted(available_tokens, key=lambda item: item["token"].id or 0)
                    if item["token"].id != first_candidate["token"].id
                )
            available_tokens = ordered_candidates
        else:
            available_tokens.sort(
                key=lambda item: (
                    1 if item["needs_refresh"] else 0,
                    item["inflight"],
                    0 if item["remaining"] is None else 1,
                    -(item["remaining"] or 0),
                    item["random"],
                )
            )

        ready_candidates = [item for item in available_tokens if not item["needs_refresh"]]
        refresh_candidates = [item for item in available_tokens if item["needs_refresh"]]
        if ready_candidates and refresh_candidates:
            available_tokens = ready_candidates + refresh_candidates

        for item in available_tokens:
            token = item["token"]
            token_id = token.id

            token = await self.token_manager.ensure_valid_token(token)
            if not token:
                debug_logger.log_info(f"[LOAD_BALANCER] skipped token {token_id}: invalid AT")
                continue

            if reserve and not await self._reserve_slot(token.id, for_image_generation, for_video_generation):
                debug_logger.log_info(f"[LOAD_BALANCER] skipped token {token.id}: reserve failed")
                continue

            if track_pending:
                await self._add_pending(token.id, for_image_generation, for_video_generation)

            debug_logger.log_info(
                f"[LOAD_BALANCER] selected token {token.id} ({token.email}) "
                f"inflight={item['inflight']}, credits={token.credits}"
            )
            return token

        return None

    async def get_unavailable_reason(
        self,
        *,
        for_image_generation: bool = False,
        for_video_generation: bool = False,
        model: Optional[str] = None,
        api_client: Optional[Dict] = None,
        requested_token_id: Optional[int] = None,
    ) -> Optional[str]:
        """Return a clearer no-token reason when one can be inferred."""
        active_tokens = await self.token_manager.get_active_tokens()
        original_count = len(active_tokens)
        active_tokens = await self._filter_tokens_for_client(
            active_tokens,
            api_client,
            for_image_generation,
            for_video_generation,
        )
        if original_count and not active_tokens and (api_client or {}).get("id"):
            return "Current API key has no available Token pool. Ask the administrator to bind or enable Tokens."
        before_requested_count = len(active_tokens)
        active_tokens = self._filter_requested_token(active_tokens, requested_token_id)
        if before_requested_count and not active_tokens and requested_token_id:
            return "Requested Token is unavailable or is not allowed for the current API key."
        if not active_tokens:
            return None

        required_tier = get_required_paygate_tier_for_model(model)
        supported_tokens = []
        for token in active_tokens:
            normalized_tier = normalize_user_paygate_tier(token.user_paygate_tier)
            if model and not supports_model_for_tier(model, normalized_tier):
                continue
            supported_tokens.append(token)

        if model and not supported_tokens:
            tier_label = get_paygate_tier_label(required_tier)
            return f"Current model requires {tier_label} account, but no available {tier_label} token exists: {model}"

        cooled_tokens = [
            token for token in supported_tokens
            if self._failure_cooldown_remaining_seconds(token) > 0
        ]
        available_after_cooldown = [
            token for token in supported_tokens
            if self._failure_cooldown_remaining_seconds(token) <= 0
        ]
        if supported_tokens and not available_after_cooldown:
            if requested_token_id and len(supported_tokens) == 1:
                return "Requested Token is cooling down after a recent failure. Try again shortly."
            shortest_remaining = min(
                int(self._failure_cooldown_remaining_seconds(token))
                for token in cooled_tokens
            )
            return f"All matching Tokens are cooling down after recent failures. Try again in about {shortest_remaining}s."

        capability_tokens = []
        for token in available_after_cooldown:
            if for_image_generation and not token.image_enabled:
                continue
            if for_video_generation and not token.video_enabled:
                continue
            capability_tokens.append(token)

        if supported_tokens and not capability_tokens:
            if for_image_generation:
                return "Matching accounts exist, but image generation is disabled for all of them."
            if for_video_generation:
                return "Matching accounts exist, but video generation is disabled for all of them."

        return None
