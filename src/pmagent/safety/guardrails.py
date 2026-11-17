"""写路径白名单：只允许写 `artifacts/` 与 `.pmagent/`，拒绝写入仓库源码。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pmagent.errors import PermissionDenied

WRITABLE_DIRS = ("artifacts", ".pmagent")


class Guardrails:
    def __init__(
        self,
        root: str | Path = ".",
        *,
        audit: Callable[..., None] | None = None,
        writable_dirs: tuple[str, ...] = WRITABLE_DIRS,
    ) -> None:
        self.root = Path(root).resolve()
        self.audit = audit
        self.writable_dirs = writable_dirs

    def ensure_writable(self, path: str | Path) -> Path:
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        resolved = target.resolve()
        allowed = any(
            resolved == (self.root / name).resolve()
            or (self.root / name).resolve() in resolved.parents
            for name in self.writable_dirs
        )
        if not allowed:
            self._record(
                "write_denied",
                path=str(path),
                resolved=str(resolved),
                reason="不在可写目录（仅 artifacts/ 与 .pmagent/）",
            )
            raise PermissionDenied(
                f"拒绝写入 {path}",
                hint="只能写 artifacts/ 或 .pmagent/；补丁请通过 patch apply 确认后应用",
            )
        return resolved

    def write(self, path: str | Path, content: str) -> Path:
        resolved = self.ensure_writable(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        self._record("write_allowed", path=str(resolved))
        return resolved

    def _record(self, event: str, **fields: object) -> None:
        if self.audit is not None:
            self.audit(event, **fields)


__all__ = ["Guardrails", "WRITABLE_DIRS"]
