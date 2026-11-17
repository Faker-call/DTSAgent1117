"""崩溃转储：minidump 头部与异常流摘要；文本崩溃报告兜底。"""

from __future__ import annotations

import re
import struct
from pathlib import Path

from pmagent.contracts import CrashEvidence

MDMP_SIGNATURE = b"MDMP"
EXCEPTION_STREAM = 6

EXCEPTION_SIGNALS = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000094: "INT_DIVIDE_BY_ZERO",
    0xE06D7363: "CPP_EXCEPTION",
    0x80000003: "BREAKPOINT",
}

TEXT_SIGNAL = re.compile(r"\b(SIGSEGV|SIGABRT|SIGBUS|SIGILL|SIGFPE|SEGV|abort)\b")


class CrashInspector:
    def inspect(self, path: str | Path) -> CrashEvidence:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"崩溃文件不存在：{path}")
        header = path.read_bytes()[:32]
        if header[:4] == MDMP_SIGNATURE:
            return self._inspect_minidump(path)
        return self._inspect_text(path)

    # -- minidump ---------------------------------------------------------
    def _inspect_minidump(self, path: Path) -> CrashEvidence:
        data = path.read_bytes()
        if len(data) < 32:
            return CrashEvidence(dump_path=str(path), signal="UNKNOWN")

        _sig, _version, stream_count, dir_rva, _checksum, _ts, _flags = struct.unpack_from(
            "<4sIIIIIQ", data, 0
        )
        signal: str | None = None
        fault_addr: str | None = None
        fault_thread: str | None = None

        if 0 < stream_count < 1024 and dir_rva + stream_count * 12 <= len(data):
            for index in range(stream_count):
                offset = dir_rva + index * 12
                stream_type, stream_size, stream_rva = struct.unpack_from("<III", data, offset)
                if stream_type != EXCEPTION_STREAM or stream_rva + 32 > len(data):
                    continue
                thread_id = struct.unpack_from("<I", data, stream_rva)[0]
                fault_thread = str(thread_id)
                record_rva = stream_rva + 8
                code, _flags2 = struct.unpack_from("<II", data, record_rva)
                address = struct.unpack_from("<Q", data, record_rva + 16)[0]
                signal = EXCEPTION_SIGNALS.get(code, f"0x{code:08X}")
                fault_addr = f"0x{address:x}"
                break

        return CrashEvidence(
            dump_path=str(path),
            signal=signal or "UNKNOWN",
            fault_addr=fault_addr,
            fault_thread=fault_thread,
        )

    # -- 文本报告 ---------------------------------------------------------
    def _inspect_text(self, path: Path) -> CrashEvidence:
        text = path.read_text(encoding="utf-8", errors="replace")[:20_000]
        match = TEXT_SIGNAL.search(text)
        address = re.search(r"fault(?:ing)? address[:= ]+0x([0-9a-fA-F]+)", text)
        return CrashEvidence(
            dump_path=str(path),
            signal=match.group(1).upper() if match else None,
            fault_addr=f"0x{address.group(1)}" if address else None,
        )


__all__ = ["CrashInspector", "EXCEPTION_SIGNALS"]
