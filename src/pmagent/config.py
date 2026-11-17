"""配置加载：默认值 → 配置文件 → 环境变量 → 命令行覆盖。

优先级从低到高，后者覆盖前者。密钥不写入配置文件，只从环境变量读取。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pmagent.contracts import Contract
from pmagent.errors import ConfigError

ENV_PREFIX = "PMAGENT_"
DEFAULT_CONFIG_PATH = Path.home() / ".pmagent" / "config.toml"
DEFAULT_INDEX_ROOT = Path.home() / ".pmagent" / "index"

MODEL_ENV_KEY = "DEEPSEEK_API_KEY"
EMBEDDING_ENV_KEY = "QWEN3.7"


class ModelConfig(Contract):
    deepseek_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-flash"
    reasoner_model: str = "deepseek-flash"
    strong_model: str = "deepseek-v4-pro"
    vision_model: str = "deepseek-flash"
    reasoning_effort: str = "high"
    timeout_seconds: float = 60.0
    max_retries: int = 3


class EmbeddingConfig(Contract):
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3.7-text-embedding"
    dim: int = 1024
    batch_size: int = 20


class BudgetConfig(Contract):
    max_tokens: int = 400_000
    max_cost_cny: float = 5.0
    max_wall_seconds: float = 120.0


class IndexConfig(Contract):
    root: Path = DEFAULT_INDEX_ROOT
    keep_snapshots: int = 3


class RedactionConfig(Contract):
    enabled: bool = True
    rules_version: str = "1"


class AppConfig(Contract):
    model: ModelConfig = ModelConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    budget: BudgetConfig = BudgetConfig()
    index: IndexConfig = IndexConfig()
    redaction: RedactionConfig = RedactionConfig()
    log_level: str = "INFO"
    output_format: str = "text"


def default_config_dict() -> dict[str, Any]:
    return AppConfig().model_dump(mode="json")


def config_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_path = os.environ.get(f"{ENV_PREFIX}CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_CONFIG_PATH


def read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法解析配置文件 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件 {path} 顶层必须是表结构")
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _set_path(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in dotted.split(".") if p]
    if not parts:
        return
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """把 PMAGENT_MODEL__CHAT_MODEL=x 解析成 {"model": {"chat_model": "x"}}。"""
    environ = dict(os.environ if environ is None else environ)
    result: dict[str, Any] = {}
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        if key in (
            f"{ENV_PREFIX}CONFIG",
            f"{ENV_PREFIX}TOKEN",
            f"{ENV_PREFIX}TEST_HOOKS",
            f"{ENV_PREFIX}DB",
            f"{ENV_PREFIX}SESSION",
            f"{ENV_PREFIX}MEMORY_DB",
            f"{ENV_PREFIX}STATE",
            f"{ENV_PREFIX}CORPUS",
        ):
            continue
        dotted = key[len(ENV_PREFIX) :].lower().replace("__", ".")
        _set_path(result, dotted, value)
    return result


def cli_overrides(pairs: list[str] | None) -> dict[str, Any]:
    """把 ["model.chat_model=deepseek-v4-pro"] 解析成嵌套字典。"""
    result: dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ConfigError(f"命令行覆盖格式应为 key=value，收到: {pair}")
        key, value = pair.split("=", 1)
        _set_path(result, key.strip(), value)
    return result


def load_config(
    path: str | Path | None = None,
    cli: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> AppConfig:
    merged = default_config_dict()
    merged = deep_merge(merged, read_toml(config_path(path)))
    merged = deep_merge(merged, env_overrides(environ))
    merged = deep_merge(merged, cli_overrides(cli))
    try:
        return AppConfig(**merged)
    except ValidationError as exc:
        raise ConfigError(f"配置无效: {exc}") from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Path):
        value = str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def to_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{key}]")
        for sub_key, sub_value in value.items():
            lines.append(f"{sub_key} = {_toml_value(sub_value)}")
    return "\n".join(lines) + "\n"


def write_default_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path
    path.write_text(to_toml(default_config_dict()), encoding="utf-8")
    return path


def secret_status(environ: dict[str, str] | None = None) -> dict[str, str]:
    """只报告密钥是否已设置，绝不返回内容。"""
    environ = dict(os.environ if environ is None else environ)
    return {
        MODEL_ENV_KEY: "set" if environ.get(MODEL_ENV_KEY) else "unset",
        EMBEDDING_ENV_KEY: "set" if environ.get(EMBEDDING_ENV_KEY) else "unset",
    }


def redacted_view(cfg: AppConfig, environ: dict[str, str] | None = None) -> dict[str, Any]:
    data = cfg.model_dump(mode="json")
    data["secrets"] = secret_status(environ)
    return data
