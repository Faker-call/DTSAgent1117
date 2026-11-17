"""DeepSeek 网关：重试、退避、熔断、超时与角色映射。"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

from pmagent.config import MODEL_ENV_KEY, ModelConfig
from pmagent.errors import ConfigError
from pmagent.gateway.base import (
    CircuitOpen,
    LLMResponse,
    Message,
    ModelRole,
    ModelUnavailable,
    Transport,
    as_messages,
)

# 各角色的思考模式强度；None 表示不传该参数
ROLE_EFFORT: dict[ModelRole, str | None] = {
    ModelRole.ROUTER: "none",
    ModelRole.COMPRESSOR: "none",
    ModelRole.VERIFIER: "low",
    ModelRole.REASONER: "high",
    ModelRole.REASONER_STRONG: "high",
    ModelRole.VISION: "low",
}

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def http_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw)
        except json.JSONDecodeError:
            return int(exc.code), {"error": raw}
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


class DeepSeekGateway:
    """只依赖可注入的 transport，便于离线测试。"""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        api_key: str | None = None,
        transport: Transport | None = None,
        sleep: Any = time.sleep,
        clock: Any = time.monotonic,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self.config = config or ModelConfig()
        self.api_key = api_key if api_key is not None else os.environ.get(MODEL_ENV_KEY, "")
        if not self.api_key:
            raise ConfigError(
                f"缺少模型密钥：环境变量 {MODEL_ENV_KEY} 未设置",
                hint=f"export {MODEL_ENV_KEY}=<your key>",
            )
        self.transport: Transport = transport or http_transport
        self._sleep = sleep
        self._clock = clock
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    # -- 公开接口 ---------------------------------------------------------
    def complete(
        self,
        messages: list[dict[str, Any]] | list[Message],
        *,
        role: ModelRole | str = ModelRole.ROUTER,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> LLMResponse:
        role = ModelRole(role)
        model = self.model_for(role)
        self._guard_circuit(role)

        payload: dict[str, Any] = {
            "model": model,
            "messages": as_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        effort = ROLE_EFFORT.get(role)
        if effort and effort != "none":
            payload["reasoning_effort"] = effort
        elif effort == "none":
            payload["reasoning_effort"] = "none"
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.config.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        last_error = "未知错误"
        for attempt in range(1, self.config.max_retries + 2):
            status, body = self.transport(url, headers, payload, self.config.timeout_seconds)
            if status == 200:
                self._on_success(role)
                return self._parse_response(body, model)
            last_error = self._error_message(status, body)
            if status != 0 and status not in RETRYABLE_STATUS:
                self._on_failure(role)
                raise ModelUnavailable(f"模型请求失败（HTTP {status}）：{last_error}")
            if attempt <= self.config.max_retries:
                delay = min(8.0, 0.5 * (2 ** (attempt - 1))) * (0.5 + random.random())
                self._sleep(delay)

        self._on_failure(role)
        raise ModelUnavailable(f"模型重试 {self.config.max_retries} 次后仍失败：{last_error}")

    def model_for(self, role: ModelRole | str) -> str:
        role = ModelRole(role)
        return {
            ModelRole.ROUTER: self.config.chat_model,
            ModelRole.COMPRESSOR: self.config.chat_model,
            ModelRole.VERIFIER: self.config.chat_model,
            ModelRole.REASONER: self.config.reasoner_model,
            ModelRole.REASONER_STRONG: self.config.strong_model,
            ModelRole.VISION: self.config.vision_model,
        }[role]

    def is_open(self, role: ModelRole | str) -> bool:
        role = str(ModelRole(role))
        opened = self._opened_at.get(role)
        if opened is None:
            return False
        if self._clock() - opened >= self._cooldown:
            del self._opened_at[role]
            self._failures[role] = 0
            return False
        return True

    # -- 内部 -------------------------------------------------------------
    def _guard_circuit(self, role: ModelRole) -> None:
        if self.is_open(role):
            raise CircuitOpen(f"角色 {role} 处于熔断中，请在冷却后重试")

    def _on_success(self, role: ModelRole) -> None:
        self._failures[str(role)] = 0

    def _on_failure(self, role: ModelRole) -> None:
        key = str(role)
        self._failures[key] = self._failures.get(key, 0) + 1
        if self._failures[key] >= self._failure_threshold:
            self._opened_at[key] = self._clock()

    @staticmethod
    def _error_message(status: int, body: dict[str, Any]) -> str:
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message", error))
        if error:
            return str(error)
        return f"HTTP {status}"

    @staticmethod
    def _parse_response(body: dict[str, Any], fallback_model: str) -> LLMResponse:
        choices = body.get("choices") or []
        text = ""
        if choices:
            text = choices[0].get("message", {}).get("content") or ""
        usage = body.get("usage") or {}
        return LLMResponse(
            text=text,
            model=body.get("model", fallback_model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cached_tokens=int(usage.get("prompt_cache_hit_tokens", 0)),
            raw=body,
        )
