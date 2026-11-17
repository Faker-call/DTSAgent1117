from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pmagent.config import ModelConfig
from pmagent.errors import ConfigError
from pmagent.gateway import (
    CircuitOpen,
    DeepSeekGateway,
    ModelRole,
    ModelUnavailable,
    complete_json,
    extract_json_object,
)
from pmagent.gateway.cost import CostMeter, cost_of


class FakeTransport:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):  # noqa: ANN001
        self.calls.append(payload)
        return self.responses.pop(0)


def _ok(text: str = "hello", prompt: int = 10, completion: int = 5) -> tuple[int, dict]:
    return 200, {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def _gw(transport, **kwargs) -> DeepSeekGateway:
    cfg = ModelConfig(max_retries=kwargs.pop("max_retries", 2))
    return DeepSeekGateway(
        cfg, api_key="test-key", transport=transport, sleep=lambda _s: None, **kwargs
    )


def test_complete_success() -> None:
    transport = FakeTransport([_ok("结果")])
    gw = _gw(transport)
    response = gw.complete([{"role": "user", "content": "hi"}], role=ModelRole.REASONER)
    assert response.text == "结果"
    assert response.prompt_tokens == 10
    assert response.completion_tokens == 5
    assert transport.calls[0]["model"] == "deepseek-flash"
    assert transport.calls[0]["reasoning_effort"] == "high"


def test_retry_on_429_then_success() -> None:
    transport = FakeTransport([(429, {"error": {"message": "rate limit"}}), _ok("ok")])
    gw = _gw(transport)
    assert gw.complete([{"role": "user", "content": "x"}]).text == "ok"
    assert len(transport.calls) == 2


def test_non_retryable_error_fails_fast() -> None:
    transport = FakeTransport([(400, {"error": {"message": "bad request"}})])
    gw = _gw(transport)
    with pytest.raises(ModelUnavailable):
        gw.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 1


def test_retries_exhausted() -> None:
    transport = FakeTransport([(500, {"error": "boom"})] * 3)
    gw = _gw(transport, max_retries=2)
    with pytest.raises(ModelUnavailable):
        gw.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 3


def test_circuit_opens_and_recovers() -> None:
    now = [0.0]
    transport = FakeTransport([(500, {"error": "boom"})] * 2 + [_ok("recovered")])
    gw = _gw(
        transport,
        max_retries=0,
        failure_threshold=2,
        cooldown_seconds=10.0,
        clock=lambda: now[0],
    )
    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            gw.complete([{"role": "user", "content": "x"}])
    with pytest.raises(CircuitOpen):
        gw.complete([{"role": "user", "content": "x"}])
    assert len(transport.calls) == 2

    now[0] = 11.0
    assert gw.complete([{"role": "user", "content": "x"}]).text == "recovered"


def test_missing_api_key_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        DeepSeekGateway(ModelConfig(), api_key="", transport=FakeTransport([]))


def test_role_model_mapping() -> None:
    gw = _gw(FakeTransport([]))
    assert gw.model_for(ModelRole.ROUTER) == "deepseek-flash"
    assert gw.model_for(ModelRole.REASONER_STRONG) == "deepseek-v4-pro"


# -- 真实 HTTP 往返（验证 urllib 传输层） ---------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps(
            {
                "model": "deepseek-flash",
                "choices": [{"message": {"content": "from-server"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: ANN002
        return


def test_http_transport_roundtrip() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = ModelConfig(deepseek_base_url=f"http://127.0.0.1:{server.server_port}")
        gw = DeepSeekGateway(cfg, api_key="k", sleep=lambda _s: None)
        assert gw.complete([{"role": "user", "content": "hi"}]).text == "from-server"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# -- 结构化输出 -----------------------------------------------------------


class _SeqGateway:
    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls = 0

    def complete(self, messages, **kwargs):  # noqa: ANN001
        from pmagent.gateway.base import LLMResponse

        self.calls += 1
        return LLMResponse(text=self.texts.pop(0), model="deepseek-flash")


def test_extract_json_from_fence() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_from_prose() -> None:
    assert extract_json_object('前缀 {"a": {"b": 2}} 后缀') == {"a": {"b": 2}}


def test_extract_json_invalid() -> None:
    with pytest.raises(ValueError):
        extract_json_object("没有 JSON")


def test_complete_json_retries_once() -> None:
    from pmagent.contracts import IntentDecision

    gateway = _SeqGateway(["不是 JSON", '{"primary": "defect", "confidence": 0.8}'])
    result = complete_json(gateway, [{"role": "user", "content": "x"}], IntentDecision)
    assert result.value.primary == "defect"
    assert gateway.calls == 2


def test_complete_json_exhausted() -> None:
    from pmagent.contracts import IntentDecision

    gateway = _SeqGateway(["bad", "still bad"])
    from pmagent.gateway.base import StructuredOutputError

    with pytest.raises(StructuredOutputError):
        complete_json(gateway, [{"role": "user", "content": "x"}], IntentDecision)


# -- 成本 -----------------------------------------------------------------


def test_cost_of_flash() -> None:
    # 1M 输入 + 1M 输出 = 2 + 8 = 10 元
    assert cost_of("deepseek-flash", 1_000_000, 1_000_000) == pytest.approx(10.0)


def test_cost_meter_accumulates() -> None:
    meter = CostMeter(max_cost_cny=100, max_tokens=10_000_000)
    meter.record(role="router", model="deepseek-flash", prompt_tokens=1000, completion_tokens=500)
    meter.record(
        role="reasoner", model="deepseek-v4-pro", prompt_tokens=1000, completion_tokens=500
    )
    assert meter.total_tokens == 3000
    assert set(meter.by_role) == {"router", "reasoner"}
    assert meter.snapshot()["total_cny"] > 0


def test_cost_meter_budget_exceeded() -> None:
    from pmagent.errors import BudgetExceeded

    meter = CostMeter(max_cost_cny=0.001)
    with pytest.raises(BudgetExceeded):
        meter.record(
            role="reasoner",
            model="deepseek-v4-pro",
            prompt_tokens=1_000_000,
            completion_tokens=0,
        )


def test_cost_meter_cached_tokens_cheaper() -> None:
    plain = CostMeter(max_cost_cny=100, max_tokens=10_000_000)
    cached = CostMeter(max_cost_cny=100, max_tokens=10_000_000)
    plain.record(role="r", model="deepseek-flash", prompt_tokens=1_000_000, completion_tokens=0)
    cached.record(
        role="r",
        model="deepseek-flash",
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_tokens=1_000_000,
    )
    assert cached.total_cny < plain.total_cny
