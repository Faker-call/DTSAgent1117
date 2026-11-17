from __future__ import annotations

from pathlib import Path

import pytest

from pmagent.config import (
    ConfigError,
    load_config,
    redacted_view,
    to_toml,
    write_default_config,
)


def test_defaults(tmp_path: Path) -> None:
    cfg = load_config(path=tmp_path / "missing.toml", environ={})
    assert cfg.model.chat_model == "deepseek-flash"
    assert cfg.model.strong_model == "deepseek-v4-pro"
    assert cfg.embedding.model == "qwen3.7-text-embedding"
    assert cfg.embedding.dim == 1024
    assert cfg.budget.max_wall_seconds == 120.0


def test_file_overrides_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[model]\nchat_model = "from-file"\n', encoding="utf-8")
    cfg = load_config(path=path, environ={})
    assert cfg.model.chat_model == "from-file"
    assert cfg.model.reasoner_model == "deepseek-flash"


def test_env_overrides_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[model]\nchat_model = "from-file"\n', encoding="utf-8")
    cfg = load_config(path=path, environ={"PMAGENT_MODEL__CHAT_MODEL": "from-env"})
    assert cfg.model.chat_model == "from-env"


def test_cli_overrides_env(tmp_path: Path) -> None:
    cfg = load_config(
        path=tmp_path / "missing.toml",
        cli=["model.chat_model=from-cli", "budget.max_tokens=1234"],
        environ={"PMAGENT_MODEL__CHAT_MODEL": "from-env"},
    )
    assert cfg.model.chat_model == "from-cli"
    assert cfg.budget.max_tokens == 1234


def test_unknown_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[model]\nnot_a_field = "x"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path=path, environ={})


def test_malformed_override_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(path=tmp_path / "missing.toml", cli=["broken"], environ={})


def test_write_default_config_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"
    write_default_config(path)
    assert path.exists()
    first = path.read_text(encoding="utf-8")
    write_default_config(path)
    assert path.read_text(encoding="utf-8") == first
    cfg = load_config(path=path, environ={})
    assert cfg.model.chat_model == "deepseek-flash"


def test_toml_roundtrip_shape() -> None:
    text = to_toml({"a": 1, "b": {"c": "x"}})
    assert "a = 1" in text
    assert "[b]" in text
    assert 'c = "x"' in text


def test_redacted_view_hides_secrets() -> None:
    cfg = load_config(path=Path("missing.toml"), environ={})
    view = redacted_view(cfg, environ={"DEEPSEEK_API_KEY": "sk-very-secret-value"})
    assert view["secrets"]["DEEPSEEK_API_KEY"] == "set"
    assert view["secrets"]["QWEN3.7"] == "unset"
    assert "sk-very-secret-value" not in str(view)
