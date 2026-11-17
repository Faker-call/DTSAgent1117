from __future__ import annotations

import struct
from pathlib import Path

from pmagent.contracts import Product
from pmagent.evidence import (
    CrashInspector,
    EvidenceNormalizer,
    StackSymbolizer,
    parse_frames,
)
from pmagent.evidence.logs import LogAnalyzer, mask_line

LOG_TEXT = """\
2026-11-17 10:00:00.120 INFO  tile load start tile=12/34/56
2026-11-17 10:00:00.220 ERROR failed to decode tile 12/34/56 code=E1024 addr=0x7ff6aabb
2026-11-17 10:00:00.320 FATAL render thread crashed at 0x7ff6ccdd
"""

STACK_TEXT = """\
#0 0x0000000000a1b2c3 pm::render::BuildStrip(char const*) at /src/tess.cc:42
#1 0x0000000000d4e5f6 main at /src/main.cc:10
"""


def _minidump(code: int = 0xC0000005, address: int = 0x7FF6AABBCCDD, thread: int = 1234) -> bytes:
    stream = struct.pack("<II", thread, 0) + struct.pack("<IIQQ", code, 0, 0, address)
    header = struct.pack("<4sIIIIIQ", b"MDMP", 1, 1, 32, 0, 0, 0)
    directory = struct.pack("<III", 6, len(stream), 32 + 12)
    return header + directory + stream


def test_log_analyzer_templates_and_anomalies(tmp_path: Path) -> None:
    path = tmp_path / "engine.log"
    path.write_text(LOG_TEXT, encoding="utf-8")
    analysis = LogAnalyzer().analyze(path)
    assert analysis.total_lines == 3
    assert analysis.evidence.line_count == 3
    assert analysis.evidence.time_range is not None
    assert analysis.anomaly_lines == [2, 3]
    assert analysis.templates
    assert analysis.templates[0].count >= 1
    assert analysis.evidence.templates


def test_mask_line_replaces_numbers_and_paths() -> None:
    masked = mask_line("2026-11-17 10:00:00.120 error at 0x7ff6aabb in C:\\work\\a.cc")
    assert "0x7ff6aabb" not in masked
    assert "C:\\work\\a.cc" not in masked
    assert "<TS>" in masked
    assert "<HEX>" in masked


def test_analyzer_compresses_100k_lines(tmp_path: Path) -> None:
    path = tmp_path / "big.log"
    lines = [
        f"2026-11-17 10:00:{index % 60:02d}.{index % 1000:03d} "
        f"{'ERROR' if index % 1000 == 0 else 'INFO'} tile {index % 97} "
        f"code=E{index % 50:04d} addr=0x{index:08x}"
        for index in range(100_000)
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    analysis = LogAnalyzer().analyze(path)
    assert analysis.total_lines == 100_000
    assert len(analysis.templates) < 10
    assert len(analysis.anomaly_lines) == 100
    assert analysis.evidence.templates


def test_stack_parse_frames() -> None:
    frames = parse_frames(STACK_TEXT)
    assert len(frames) == 2
    assert frames[0].function.startswith("pm::render::BuildStrip")
    assert frames[0].file == "/src/tess.cc"
    assert frames[0].line == 42


def test_symbolizer_without_tool_degrades(tmp_path: Path) -> None:
    path = tmp_path / "stack.txt"
    path.write_text(STACK_TEXT, encoding="utf-8")
    symbolizer = StackSymbolizer(tool_path=None)
    symbolizer.tool_path = None
    result = symbolizer.symbolicate(path)
    assert result.evidence.symbolication_status == "unsupported"
    assert result.degraded_reason == "未找到符号化工具"


def test_symbolizer_with_runner(tmp_path: Path) -> None:
    path = tmp_path / "stack.txt"
    path.write_text(STACK_TEXT, encoding="utf-8")

    def runner(tool: str, address: str, binary: str | None):  # noqa: ANN001
        return ("resolved::Func", "/resolved.cc", 7)

    symbolizer = StackSymbolizer(tool_path="llvm-symbolizer", runner=runner)
    result = symbolizer.symbolicate(path, binary="engine.so")
    assert result.evidence.symbolication_status == "symbolicated"
    assert all(frame.file == "/resolved.cc" for frame in result.evidence.frames)


def test_symbolizer_requires_binary(tmp_path: Path) -> None:
    path = tmp_path / "stack.txt"
    path.write_text(STACK_TEXT, encoding="utf-8")
    symbolizer = StackSymbolizer(tool_path="llvm-symbolizer", runner=lambda *a: None)
    result = symbolizer.symbolicate(path)
    assert result.evidence.symbolication_status == "unsupported"
    assert result.degraded_reason == "未提供二进制文件"


def test_crash_minidump(tmp_path: Path) -> None:
    path = tmp_path / "crash.dmp"
    path.write_bytes(_minidump())
    evidence = CrashInspector().inspect(path)
    assert evidence.signal == "ACCESS_VIOLATION"
    assert evidence.fault_addr == "0x7ff6aabbccdd"
    assert evidence.fault_thread == "1234"


def test_crash_text_report(tmp_path: Path) -> None:
    path = tmp_path / "crash.txt"
    path.write_text("signal: SIGSEGV\nfaulting address: 0xdeadbeef\n", encoding="utf-8")
    evidence = CrashInspector().inspect(path)
    assert evidence.signal == "SIGSEGV"
    assert evidence.fault_addr == "0xdeadbeef"


def test_normalizer_assembles_context(tmp_path: Path) -> None:
    log_path = tmp_path / "engine.log"
    log_path.write_text(LOG_TEXT, encoding="utf-8")
    stack_path = tmp_path / "stack.txt"
    stack_path.write_text(STACK_TEXT, encoding="utf-8")

    result = EvidenceNormalizer().build(
        "地图缩放后出现空块",
        logs=[str(log_path)],
        stacks=[str(stack_path)],
        product=Product(sdk_version="6.0.1", platform="harmony"),
    )
    assert result.context.logs and result.context.stacks
    assert result.missing == []
    assert len(result.log_analyses) == 1
    assert len(result.stack_results) == 1


def test_normalizer_missing_evidence(tmp_path: Path) -> None:
    result = EvidenceNormalizer().build("地图空白")
    needed = [item.needed for item in result.missing]
    assert any("任一项" in item for item in needed)
    assert any("SDK 版本" in item for item in needed)


def test_normalizer_localize_requires_location(tmp_path: Path) -> None:
    result = EvidenceNormalizer().build("定位一下这个崩溃", intent="localize")
    needed = [item.needed for item in result.missing]
    assert any("代码位置" in item or "堆栈" in item for item in needed)


def test_normalizer_redacts_query() -> None:
    result = EvidenceNormalizer().build("失败 api_key=abcdef123456")
    assert "abcdef123456" not in result.context.raw_query
    assert "<TOKEN:1>" in result.context.raw_query
    assert "credential" in result.context.redaction.rules_applied
