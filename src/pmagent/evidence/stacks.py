"""堆栈符号化：优先 llvm-symbolizer / addr2line，缺工具时保留原始帧并说明原因。"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pmagent.contracts import StackEvidence, StackFrame

FRAME_RE = re.compile(
    r"#(?P<index>\d+)\s+0x(?P<address>[0-9a-fA-F]+)\s+"
    r"(?P<function>.*?)"
    r"(?:\s+at\s+(?P<file>[^\s:]+):(?P<line>\d+))?\s*$"
)
TOOL_CANDIDATES = ("llvm-symbolizer", "addr2line")

Runner = Callable[[str, str, str | None], tuple[str, str, int] | None]


@dataclass
class SymbolicationResult:
    evidence: StackEvidence
    tool: str | None
    degraded_reason: str | None = None


def parse_frames(text: str) -> list[StackFrame]:
    frames: list[StackFrame] = []
    for line in text.splitlines():
        match = FRAME_RE.search(line.strip())
        if not match:
            continue
        frames.append(
            StackFrame(
                index=int(match.group("index")),
                address=match.group("address"),
                function=(match.group("function") or "").strip() or None,
                file=match.group("file"),
                line=int(match.group("line")) if match.group("line") else None,
            )
        )
    return frames


def _default_runner(tool: str, address: str, binary: str | None) -> tuple[str, str, int] | None:
    try:
        if tool.endswith("addr2line"):
            args = [tool, "-e", binary or "", "-f", "-C", f"0x{address}"]
            output = subprocess.run(args, capture_output=True, text=True, timeout=30).stdout
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if len(lines) >= 2 and ":" in lines[1]:
                file_name, _, line_no = lines[1].rpartition(":")
                return lines[0], file_name, int(line_no)
            return None
        output = subprocess.run(
            [tool, f"0x{address}"], capture_output=True, text=True, timeout=30
        ).stdout
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if len(lines) >= 2:
            file_name, _, line_no = lines[0].rpartition(":")
            return lines[1], file_name, int(line_no) if line_no.isdigit() else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return None


class StackSymbolizer:
    def __init__(
        self,
        tool_path: str | None = None,
        runner: Runner | None = None,
        binary: str | None = None,
    ) -> None:
        self.tool_path = tool_path or self._detect_tool()
        self.runner = runner or _default_runner
        self.binary = binary

    @staticmethod
    def _detect_tool() -> str | None:
        for candidate in TOOL_CANDIDATES:
            found = shutil.which(candidate)
            if found:
                return found
        return None

    def symbolicate(self, path: str | Path, *, binary: str | None = None) -> SymbolicationResult:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise FileNotFoundError(f"堆栈不可读：{path}") from exc
        frames = parse_frames(text)
        binary = binary or self.binary

        if not frames:
            return SymbolicationResult(
                evidence=StackEvidence(
                    source_path=str(path), frames=[], symbolication_status="unsupported"
                ),
                tool=self.tool_path,
                degraded_reason="未识别到栈帧格式",
            )

        if self.tool_path is None or binary is None:
            reason = "未找到符号化工具" if self.tool_path is None else "未提供二进制文件"
            return SymbolicationResult(
                evidence=StackEvidence(
                    source_path=str(path), frames=frames, symbolication_status="unsupported"
                ),
                tool=self.tool_path,
                degraded_reason=reason,
            )

        resolved = 0
        for frame in frames:
            if frame.address is None:
                continue
            result = self.runner(self.tool_path, frame.address, binary)
            if result is not None:
                function, file_name, line = result
                frame.function = function or frame.function
                frame.file = file_name or frame.file
                frame.line = line or frame.line
                resolved += 1

        if resolved == len(frames):
            status = "symbolicated"
        elif resolved:
            status = "partial"
        else:
            status = "unsupported"
        return SymbolicationResult(
            evidence=StackEvidence(
                source_path=str(path), frames=frames, symbolication_status=status
            ),
            tool=self.tool_path,
            degraded_reason=None if resolved else "所有地址均未能解析",
        )


__all__ = ["SymbolicationResult", "StackSymbolizer", "parse_frames"]
