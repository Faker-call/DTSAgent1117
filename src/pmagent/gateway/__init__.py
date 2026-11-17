"""模型网关：对话、视觉、向量化、成本与密钥（M04）。"""

from pmagent.gateway.base import (
    CircuitOpen,
    GatewayError,
    LLMResponse,
    Message,
    ModelRole,
    ModelUnavailable,
    StructuredOutputError,
)
from pmagent.gateway.cost import CostMeter, cost_of, price_for
from pmagent.gateway.deepseek import DeepSeekGateway
from pmagent.gateway.embedding import BailianEmbedding, EmbeddingCache
from pmagent.gateway.keys import ApiKeys, load_api_keys, require_keys, scrub_secrets
from pmagent.gateway.structured import StructuredResult, complete_json, extract_json_object

__all__ = [
    "ApiKeys",
    "BailianEmbedding",
    "CircuitOpen",
    "CostMeter",
    "DeepSeekGateway",
    "EmbeddingCache",
    "GatewayError",
    "LLMResponse",
    "Message",
    "ModelRole",
    "ModelUnavailable",
    "StructuredOutputError",
    "StructuredResult",
    "complete_json",
    "cost_of",
    "extract_json_object",
    "load_api_keys",
    "price_for",
    "require_keys",
    "scrub_secrets",
]
