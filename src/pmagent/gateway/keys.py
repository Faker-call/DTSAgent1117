"""密钥加载与校验：只从环境变量读取，缺失即报错，日志中不出现明文。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from pmagent.config import EMBEDDING_ENV_KEY, MODEL_ENV_KEY
from pmagent.errors import ConfigError


@dataclass
class ApiKeys:
    """持有密钥但禁止在 repr / str 中泄露。"""

    deepseek: str = field(default="", repr=False)
    embedding: str = field(default="", repr=False)

    def __repr__(self) -> str:
        deepseek = "set" if self.deepseek else "unset"
        embedding = "set" if self.embedding else "unset"
        return f"ApiKeys(deepseek={deepseek}, embedding={embedding})"

    def __str__(self) -> str:
        return repr(self)

    def values(self) -> list[str]:
        return [v for v in (self.deepseek, self.embedding) if v]


def load_api_keys(environ: dict[str, str] | None = None) -> ApiKeys:
    environ = dict(os.environ if environ is None else environ)
    return ApiKeys(
        deepseek=environ.get(MODEL_ENV_KEY, ""),
        embedding=environ.get(EMBEDDING_ENV_KEY, ""),
    )


def require_keys(
    *,
    model: bool = True,
    embedding: bool = False,
    environ: dict[str, str] | None = None,
) -> ApiKeys:
    """校验所需密钥，缺失时给出变量名并以配置错误退出（退出码 2）。"""
    keys = load_api_keys(environ)
    missing: list[str] = []
    if model and not keys.deepseek:
        missing.append(MODEL_ENV_KEY)
    if embedding and not keys.embedding:
        missing.append(EMBEDDING_ENV_KEY)
    if missing:
        raise ConfigError(
            f"缺少必需的密钥环境变量：{', '.join(missing)}",
            hint="设置环境变量后重试；密钥不会写入配置文件",
        )
    return keys


def scrub_secrets(text: str, keys: ApiKeys | None = None) -> str:
    """把已知密钥从任意文本中抹掉，用于日志与追踪兜底。"""
    if not text:
        return text
    scrubbed = text
    for value in (keys.values() if keys is not None else []):
        if value and len(value) >= 6:
            scrubbed = scrubbed.replace(value, "<SECRET>")
    return scrubbed
