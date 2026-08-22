"""Anthropic (Claude) model provider implementation.

Native integration built on the official ``anthropic`` SDK / Messages API. Claude models are
also reachable through OpenRouter (``anthropic/...`` IDs); this provider gives a direct route
using an ``ANTHROPIC_API_KEY`` so bare aliases like ``opus``/``fable`` resolve to the first-party
API when a key is configured (native providers take priority over OpenRouter).
"""

import base64
import logging
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from tools.models import ToolModelCategory

from utils.image_utils import validate_image

from .base import ModelProvider
from .registries.anthropic import AnthropicModelRegistry
from .registry_provider_mixin import RegistryBackedProviderMixin
from .shared import ModelCapabilities, ModelResponse, ProviderType

logger = logging.getLogger(__name__)

# Anthropic requires an extended-thinking budget of at least this many tokens when thinking is
# enabled; requests below it are rejected. "minimal" therefore maps to thinking-disabled.
_MIN_THINKING_BUDGET = 1024


class AnthropicModelProvider(RegistryBackedProviderMixin, ModelProvider):
    """First-party Anthropic Claude integration built on the official SDK."""

    REGISTRY_CLASS = AnthropicModelRegistry
    MODEL_CAPABILITIES: ClassVar[dict[str, ModelCapabilities]] = {}

    # Thinking-mode budgets as a percentage of a model's max_thinking_tokens (mirrors the Gemini
    # provider). "minimal" is intentionally below Anthropic's floor so it disables thinking.
    THINKING_BUDGETS: ClassVar[dict[str, float]] = {
        "minimal": 0.0,
        "low": 0.08,
        "medium": 0.33,
        "high": 0.67,
        "max": 1.0,
    }

    def __init__(self, api_key: str, **kwargs):
        """Initialize the Anthropic provider with an API key and optional base URL."""
        self._ensure_registry()
        super().__init__(api_key, **kwargs)
        self._client = None
        self._base_url = kwargs.get("base_url", None)
        self._invalidate_capability_cache()

    # ------------------------------------------------------------------
    # Client access
    # ------------------------------------------------------------------
    @property
    def client(self):
        """Lazy initialization of the Anthropic client (SDK imported on first use)."""
        if self._client is None:
            import anthropic  # imported lazily so the module loads without the SDK installed

            client_kwargs: dict[str, object] = {"api_key": self.api_key}
            if self._base_url:
                client_kwargs["base_url"] = self._base_url
            self._client = anthropic.Anthropic(**client_kwargs)
        return self._client

    def get_provider_type(self) -> ProviderType:
        return ProviderType.ANTHROPIC

    # ------------------------------------------------------------------
    # Request execution
    # ------------------------------------------------------------------
    def generate_content(
        self,
        prompt: str,
        model_name: str,
        system_prompt: Optional[str] = None,
        temperature: float = 1.0,
        max_output_tokens: Optional[int] = None,
        thinking_mode: str = "medium",
        images: Optional[list[str]] = None,
        **kwargs,
    ) -> ModelResponse:
        """Generate content using a Claude model via the Anthropic Messages API."""
        self.validate_parameters(model_name, temperature)
        capabilities = self.get_capabilities(model_name)
        resolved_model_name = self._resolve_model_name(model_name)

        # Build the user message content (text + optional images).
        content: list[dict] = [{"type": "text", "text": prompt}]
        if images and capabilities.supports_images:
            for image_path in images:
                block = self._process_image(image_path)
                if block:
                    content.append(block)
        elif images and not capabilities.supports_images:
            logger.warning("Model %s does not support images, ignoring %d image(s)", resolved_model_name, len(images))

        messages = [{"role": "user", "content": content}]

        # max_tokens is required by the Messages API; fall back to the model's advertised ceiling.
        max_tokens = max_output_tokens or capabilities.max_output_tokens or 4096

        # Resolve the extended-thinking budget. Anthropic requires budget_tokens >= 1024 and
        # strictly less than max_tokens, and forces temperature to 1.0 while thinking is enabled.
        thinking_config = None
        effective_temperature = temperature
        budget = 0
        if capabilities.supports_extended_thinking and thinking_mode in self.THINKING_BUDGETS:
            fraction = self.THINKING_BUDGETS[thinking_mode]
            model_config = self.get_all_model_capabilities().get(resolved_model_name)
            max_thinking = model_config.max_thinking_tokens if model_config else 0
            budget = int(max_thinking * fraction)
            if budget >= _MIN_THINKING_BUDGET:
                # Guarantee room for the response after the thinking budget.
                if budget >= max_tokens:
                    max_tokens = min(capabilities.max_output_tokens or (budget + 4096), budget + 4096)
                if budget < max_tokens:
                    thinking_config = {"type": "enabled", "budget_tokens": budget}
                    effective_temperature = 1.0  # required by the API when thinking is on
                else:
                    budget = 0

        request_kwargs: dict[str, object] = {
            "model": resolved_model_name,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if system_prompt:
            request_kwargs["system"] = system_prompt
        if capabilities.supports_temperature:
            request_kwargs["temperature"] = effective_temperature
        if thinking_config is not None:
            request_kwargs["thinking"] = thinking_config

        max_retries = 4
        retry_delays = [1, 3, 5, 8]
        attempt_counter = {"value": 0}

        def _attempt() -> ModelResponse:
            attempt_counter["value"] += 1
            response = self.client.messages.create(**request_kwargs)

            text = "".join(
                getattr(block, "text", "")
                for block in (response.content or [])
                if getattr(block, "type", None) == "text"
            )
            usage = self._extract_usage(response)

            return ModelResponse(
                content=text,
                usage=usage,
                model_name=resolved_model_name,
                friendly_name="Claude",
                provider=ProviderType.ANTHROPIC,
                metadata={
                    "thinking_mode": thinking_mode if thinking_config is not None else None,
                    "thinking_budget": budget if thinking_config is not None else 0,
                    "finish_reason": getattr(response, "stop_reason", None),
                },
            )

        try:
            return self._run_with_retries(
                operation=_attempt,
                max_attempts=max_retries,
                delays=retry_delays,
                log_prefix=f"Anthropic API ({resolved_model_name})",
            )
        except Exception as exc:
            attempts = max(attempt_counter["value"], 1)
            error_msg = (
                f"Anthropic API error for model {resolved_model_name} after {attempts} attempt"
                f"{'s' if attempts > 1 else ''}: {exc}"
            )
            raise RuntimeError(error_msg) from exc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_usage(self, response) -> dict[str, int]:
        """Extract token usage from an Anthropic Messages response."""
        usage: dict[str, int] = {}
        try:
            meta = response.usage
            input_tokens = getattr(meta, "input_tokens", None)
            output_tokens = getattr(meta, "output_tokens", None)
            if input_tokens is not None:
                usage["input_tokens"] = input_tokens
            if output_tokens is not None:
                usage["output_tokens"] = output_tokens
            if input_tokens is not None and output_tokens is not None:
                usage["total_tokens"] = input_tokens + output_tokens
        except (AttributeError, TypeError):
            pass
        return usage

    def _process_image(self, image_path: str) -> Optional[dict]:
        """Process an image into an Anthropic content block."""
        try:
            image_bytes, mime_type = validate_image(image_path)
            if image_path.startswith("data:"):
                _, data = image_path.split(",", 1)
            else:
                data = base64.b64encode(image_bytes).decode()
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": mime_type, "data": data},
            }
        except ValueError as e:
            logger.warning(str(e))
            return None
        except Exception as e:
            logger.error(f"Error processing image {image_path}: {e}")
            return None

    def _is_error_retryable(self, error: Exception) -> bool:
        """Retry on transient Anthropic failures (rate limits, overload, 5xx, connection)."""
        try:
            import anthropic

            if isinstance(
                error, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError)
            ):
                return True
            if isinstance(error, anthropic.APIStatusError):
                return getattr(error, "status_code", 0) >= 500
        except Exception:  # pragma: no cover - SDK missing / unexpected shape
            pass
        message = str(error).lower()
        return any(token in message for token in ("timeout", "overloaded", "rate limit", "503", "502", "500", "529"))

    def get_preferred_model(self, category: "ToolModelCategory", allowed_models: list[str]) -> Optional[str]:
        """Pick Claude's preferred model for a tool category from the allowed list."""
        from tools.models import ToolModelCategory

        if not allowed_models:
            return None

        capability_map = self.get_all_model_capabilities()
        canonical_allowed = [m for m in allowed_models if m in capability_map]
        if canonical_allowed:
            allowed_models = canonical_allowed

        def sort_key(model_name: str) -> tuple[int, int, str]:
            # Raw intelligence_score first: the effective capability rank saturates at 100 for
            # every model scoring >=18 (opus/fable/sonnet all cap out), so relying on it alone would
            # let a lexical tie-break pick sonnet over the higher-scored opus/fable.
            caps = capability_map.get(model_name)
            if not caps:
                return (0, 0, model_name)
            return (caps.intelligence_score, caps.get_effective_capability_rank(), model_name)

        def best(candidates: list[str]) -> Optional[str]:
            if not candidates:
                return None
            return max(candidates, key=sort_key)

        if category == ToolModelCategory.FAST_RESPONSE:
            # Prefer the fast/cheap Haiku tier; otherwise the lowest-scored available model.
            haiku = [m for m in allowed_models if "haiku" in m]
            if haiku:
                return best(haiku)
            return min(allowed_models, key=sort_key)

        if category == ToolModelCategory.EXTENDED_REASONING:
            thinking = [m for m in allowed_models if capability_map[m].supports_extended_thinking]
            if thinking:
                return best(thinking)

        # BALANCED / fallback: highest-capability allowed model.
        return best(allowed_models)


# Load registry data at import time for registry consumers
AnthropicModelProvider._ensure_registry()
