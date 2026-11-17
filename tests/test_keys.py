from __future__ import annotations

import pytest

from pmagent.errors import ConfigError
from pmagent.gateway.keys import ApiKeys, load_api_keys, require_keys, scrub_secrets
from pmagent.store import TraceRecorder


def test_load_keys_from_environ() -> None:
    keys = load_api_keys({"DEEPSEEK_API_KEY": "d", "QWEN3.7": "q"})
    assert keys.deepseek == "d"
    assert keys.embedding == "q"


def test_repr_masks_values() -> None:
    keys = ApiKeys(deepseek="sk-super-secret", embedding="qwen-secret")
    assert "sk-super-secret" not in repr(keys)
    assert "qwen-secret" not in str(keys)
    assert "set" in repr(keys)


def test_require_model_key_missing() -> None:
    with pytest.raises(ConfigError) as exc:
        require_keys(environ={})
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_require_embedding_key_missing() -> None:
    with pytest.raises(ConfigError) as exc:
        require_keys(model=False, embedding=True, environ={"DEEPSEEK_API_KEY": "d"})
    assert "QWEN3.7" in str(exc.value)


def test_require_keys_ok() -> None:
    keys = require_keys(environ={"DEEPSEEK_API_KEY": "d", "QWEN3.7": "q"})
    assert keys.deepseek == "d"


def test_scrub_secrets() -> None:
    keys = ApiKeys(deepseek="sk-abcdef123456", embedding="qwen-abcdef123456")
    text = "request failed with sk-abcdef123456 and qwen-abcdef123456"
    scrubbed = scrub_secrets(text, keys)
    assert "sk-abcdef123456" not in scrubbed
    assert "qwen-abcdef123456" not in scrubbed
    assert scrubbed.count("<SECRET>") == 2


def test_trace_never_contains_key(tmp_path) -> None:  # noqa: ANN001
    keys = ApiKeys(deepseek="sk-abcdef123456")
    path = tmp_path / "trace.jsonl"
    with TraceRecorder(path, run_id="r1") as recorder:
        recorder.record("model_called", detail=scrub_secrets("using sk-abcdef123456", keys))
    raw = path.read_text(encoding="utf-8")
    assert "sk-abcdef123456" not in raw
    assert "<SECRET>" in raw
