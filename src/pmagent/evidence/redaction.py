"""脱敏：证据入库与出网前两个调用点，规则版本化，保留可逆映射。

设计约定：
- 可逆映射只保存在本地，不随证据出网；
- 规则带版本号，便于审计与回滚；
- 幂等：对已脱敏文本再次脱敏不会重复替换。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

RULES_VERSION = "1"


@dataclass(frozen=True)
class RedactionRule:
    rule_id: str
    placeholder: str
    pattern: re.Pattern[str]
    replacer: Callable[[re.Match[str], Callable[[str], str]], str]


def _whole(placeholder: str) -> Callable[[re.Match[str], Callable[[str], str]], str]:
    def replace(match: re.Match[str], mint: Callable[[str], str]) -> str:
        return mint(match.group(0))

    return replace


def _keep_name(placeholder: str) -> Callable[[re.Match[str], Callable[[str], str]], str]:
    """保留键名，只替换值，例如 api_key=<TOKEN:1>。"""

    def replace(match: re.Match[str], mint: Callable[[str], str]) -> str:
        value = match.group(2)
        return match.group(0).replace(value, mint(value))

    return replace


RULES_V1: tuple[RedactionRule, ...] = (
    RedactionRule(
        rule_id="coord",
        placeholder="COORD",
        pattern=re.compile(r"\b-?\d{1,3}\.\d{4,}\s*[,，]\s*-?\d{1,3}\.\d{4,}\b"),
        replacer=_whole("COORD"),
    ),
    RedactionRule(
        rule_id="bearer",
        placeholder="TOKEN",
        pattern=re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._\-]{16,})"),
        replacer=_keep_name("TOKEN"),
    ),
    RedactionRule(
        rule_id="credential",
        placeholder="TOKEN",
        pattern=re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd)"
            r"\b\s*[=:]\s*(?!<[A-Z]+:\d+>)([^\s,;'\"]{6,})"
        ),
        replacer=_keep_name("TOKEN"),
    ),
    RedactionRule(
        rule_id="device",
        placeholder="DEVICE",
        pattern=re.compile(
            r"(?i)\b(imei|imsi|udid|oaid|android[_-]?id|device[_-]?id|serial(?:[_-]?no)?)"
            r"\b\s*[=:]\s*([A-Za-z0-9_\-]{6,})"
        ),
        replacer=_keep_name("DEVICE"),
    ),
    RedactionRule(
        rule_id="win_path",
        placeholder="PATH",
        pattern=re.compile(r"\b[A-Za-z]:\\(?:[^\\\s\"'<>|:*?]+\\)*[^\\\s\"'<>|:*?]*"),
        replacer=_whole("PATH"),
    ),
    RedactionRule(
        rule_id="posix_home",
        placeholder="PATH",
        pattern=re.compile(r"/(?:Users|home)/[^\s\"']+"),
        replacer=_whole("PATH"),
    ),
)


@dataclass
class RedactionResult:
    text: str
    rules_applied: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.mapping)


class Redactor:
    def __init__(
        self,
        rules: tuple[RedactionRule, ...] = RULES_V1,
        *,
        enabled: bool = True,
        rules_version: str = RULES_VERSION,
    ) -> None:
        self.rules = rules
        self.enabled = enabled
        self.rules_version = rules_version
        self._mapping: dict[str, str] = {}
        self._counters: dict[str, int] = {}

    def redact(self, text: str) -> RedactionResult:
        if not self.enabled or not text:
            return RedactionResult(text=text)

        applied: list[str] = []
        result = text
        for rule in self.rules:

            def mint(value: str, _rule: RedactionRule = rule) -> str:
                index = self._counters.get(_rule.placeholder, 0) + 1
                self._counters[_rule.placeholder] = index
                placeholder = f"<{_rule.placeholder}:{index}>"
                self._mapping[placeholder] = value
                return placeholder

            new_result, hits = rule.pattern.subn(lambda m, r=rule: r.replacer(m, mint), result)
            if hits:
                applied.append(rule.rule_id)
                result = new_result
        return RedactionResult(text=result, rules_applied=applied, mapping=dict(self._mapping))

    # 两个调用点：证据入库与出网前，使用同一引擎但分别记录
    def redact_for_storage(self, text: str) -> RedactionResult:
        return self.redact(text)

    def redact_for_egress(self, text: str) -> RedactionResult:
        return self.redact(text)

    def restore(self, text: str) -> str:
        restored = text
        for placeholder, original in self._mapping.items():
            restored = restored.replace(placeholder, original)
        return restored

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._mapping)
