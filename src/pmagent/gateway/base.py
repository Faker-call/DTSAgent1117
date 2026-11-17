"""模型网关公共类型。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pmagent.errors import PmagentError


class ModelRole(StrEnum):
    ROUTER = "router"
    COMPRESSOR = "compressor"
    VERIFIER = "verifier"
    REASONER = "reasoner"
    REASONER_STRONG = "reasoner_strong"
    VISION = "vision"


class GatewayError(PmagentError):
    """网关可预期错误。"""


class ModelUnavailable(GatewayError):
    """重试耗尽仍不可用。"""


class CircuitOpen(GatewayError):
    """熔断中，快速失败。"""


class StructuredOutputError(GatewayError):
    """模型输出无法解析为约定结构。"""


@dataclass
class Message:
    role: str
    content: str | list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


# transport(url, headers, payload, timeout) -> (status_code, json_body)
Transport = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, dict[str, Any]]]


def as_messages(messages: list[dict[str, Any]] | list[Message]) -> list[dict[str, Any]]:
    return [m.as_dict() if isinstance(m, Message) else dict(m) for m in messages]
