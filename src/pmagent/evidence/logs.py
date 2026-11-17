"""日志分析：正则归一化 + Drain3 模板 + 时间线与异常窗口。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pmagent.contracts import LogEvidence

logging.getLogger("drain3").setLevel(logging.ERROR)

TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b")
HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
COORD = re.compile(r"\b-?\d{1,3}\.\d{4,}\b")
THREAD = re.compile(r"\b(?:thread|tid)[=: ]+(\d+)\b", re.IGNORECASE)
PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s\"']+")
NUMBER = re.compile(r"\b\d+\b")

ANOMALY = re.compile(
    r"\b(error|fatal|exception|assert|failed|failure|crash|abort|segfault|oom)\b",
    re.IGNORECASE,
)

MASK_PATTERNS = (
    (TIMESTAMP, "<TS>"),
    (HEX, "<HEX>"),
    (COORD, "<NUM>"),
    (PATH, "<PATH>"),
    (THREAD, "thread=<NUM>"),
    (NUMBER, "<NUM>"),
)


@dataclass
class TemplateStat:
    template: str
    count: int = 0
    first_line: int = 0
    last_line: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class LogAnalysis:
    evidence: LogEvidence
    templates: list[TemplateStat]
    anomaly_lines: list[int]
    total_lines: int


def mask_line(line: str) -> str:
    masked = line
    for pattern, placeholder in MASK_PATTERNS:
        masked = pattern.sub(placeholder, masked)
    return " ".join(masked.split())


class LogAnalyzer:
    def __init__(self, max_samples: int = 3) -> None:
        self.max_samples = max_samples

    def analyze(self, path: str | Path, *, max_lines: int = 200_000) -> LogAnalysis:
        path = Path(path)
        try:
            raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise FileNotFoundError(f"日志不可读：{path}") from exc
        lines = raw_lines[:max_lines]

        templates: dict[str, TemplateStat] = {}
        anomaly_lines: list[int] = []
        first_ts: str | None = None
        last_ts: str | None = None

        miner = None
        try:
            from drain3 import TemplateMiner

            miner = TemplateMiner()
        except Exception:  # pragma: no cover - drain3 缺失时退化为纯归一化
            miner = None

        for index, line in enumerate(lines, start=1):
            timestamp = TIMESTAMP.search(line)
            if timestamp:
                first_ts = first_ts or timestamp.group(0)
                last_ts = timestamp.group(0)
            if ANOMALY.search(line):
                anomaly_lines.append(index)

            masked = mask_line(line)
            template = masked
            if miner is not None:
                try:
                    result = miner.add_log_message(masked)
                    template = str(result.get("template_mined") or masked)
                except Exception:  # pragma: no cover
                    template = masked

            stat = templates.get(template)
            if stat is None:
                stat = TemplateStat(template=template, first_line=index, last_line=index)
                templates[template] = stat
            stat.count += 1
            stat.last_line = index
            if len(stat.samples) < self.max_samples:
                stat.samples.append(line.strip())

        ordered = sorted(templates.values(), key=lambda item: item.count, reverse=True)
        time_range = (first_ts, last_ts) if first_ts and last_ts else None
        evidence = LogEvidence(
            source_path=str(path),
            line_count=len(lines),
            templates=[f"{stat.count}x {stat.template}" for stat in ordered[:20]],
            anomalies=[f"line {line}" for line in anomaly_lines[:50]],
            time_range=time_range,
        )
        return LogAnalysis(
            evidence=evidence,
            templates=ordered[:20],
            anomaly_lines=anomaly_lines,
            total_lines=len(lines),
        )


__all__ = ["LogAnalysis", "LogAnalyzer", "TemplateStat", "mask_line"]
