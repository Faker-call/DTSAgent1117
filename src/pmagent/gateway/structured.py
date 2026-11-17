"""结构化输出：从模型响应中稳健提取 JSON 并用 pydantic 校验。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from pmagent.gateway.base import LLMResponse, Message, ModelRole, StructuredOutputError

TModel = TypeVar("TModel", bound=BaseModel)

_RETRY_HINT = (
    "上一次输出不符合约定：{error}。请只输出一个合法的 JSON 对象，"
    "不要包含解释、前后缀或 Markdown 代码围栏。"
)


@dataclass
class StructuredResult:
    value: BaseModel
    response: LLMResponse


def _iter_brace_candidates(text: str):
    start = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start : index + 1]
                start = None
            elif depth < 0:
                depth = 0
                start = None


def extract_json_object(text: str) -> dict[str, Any]:
    """从任意文本中提取第一个合法 JSON 对象。

    支持三种常见形态：纯 JSON、```json 围栏、JSON 前后带解释文字。
    """
    if not text or not text.strip():
        raise ValueError("空响应")
    candidates = [text.strip()]
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = [ln for ln in lines if not ln.strip().startswith("```")]
        candidates.append("\n".join(body).strip())
    candidates.extend(_iter_brace_candidates(text))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("响应中没有可解析的 JSON 对象")


def complete_json(
    gateway: Any,
    messages: list[dict[str, Any]] | list[Message],
    schema_model: type[TModel],
    *,
    role: ModelRole | str = ModelRole.REASONER,
    max_tokens: int = 1024,
    attempts: int = 2,
) -> StructuredResult:
    """请求结构化输出；解析或校验失败时携带错误提示重试一次。"""
    conversation: list[Any] = list(messages)
    last_error = "未知错误"

    for attempt in range(1, attempts + 1):
        response = gateway.complete(
            conversation,
            role=role,
            schema=schema_model.model_json_schema(),
            max_tokens=max_tokens,
        )
        try:
            payload = extract_json_object(response.text)
            value = schema_model.model_validate(payload)
            return StructuredResult(value=value, response=response)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc).splitlines()[0]
            if attempt >= attempts:
                break
            conversation = [
                *conversation,
                {"role": "assistant", "content": response.text},
                {"role": "user", "content": _RETRY_HINT.format(error=last_error)},
            ]

    raise StructuredOutputError(
        f"结构化输出解析失败（已尝试 {attempts} 次）：{last_error}",
        hint="检查提示词或放宽 schema",
    )
