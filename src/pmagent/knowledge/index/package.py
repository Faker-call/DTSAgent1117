"""索引制品分发：打包、校验和、安全解包。"""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path, PurePosixPath

from pmagent.errors import PmagentError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pack_index(
    index_root: str | Path, commit: str, out_dir: str | Path | None = None
) -> tuple[Path, str]:
    root = Path(index_root)
    snapshot = root / commit[:12]
    if not (snapshot / "manifest.json").exists():
        raise PmagentError(f"索引快照不存在：{snapshot}")
    out_dir = Path(out_dir) if out_dir is not None else root / "packages"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"index-{commit[:12]}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(snapshot, arcname=commit[:12])
    digest = sha256_file(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(digest, encoding="utf-8")
    return archive, digest


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for member in tar.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise PmagentError(f"制品包含不安全路径：{member.name}")
        if member.issym() or member.islnk():
            raise PmagentError(f"制品包含链接，拒绝解包：{member.name}")
        members.append(member)
    return members


def unpack_index(
    archive: str | Path,
    index_root: str | Path,
    *,
    expected_sha256: str | None = None,
) -> str:
    archive = Path(archive)
    if not archive.exists():
        raise PmagentError(f"制品不存在：{archive}")
    expected = expected_sha256
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if expected is None and sidecar.exists():
        expected = sidecar.read_text(encoding="utf-8").strip()
    actual = sha256_file(archive)
    if expected and actual != expected:
        raise PmagentError(
            f"制品校验和不匹配（期望 {expected[:12]}…，实际 {actual[:12]}…）",
            hint="重新分发制品，不要使用被修改的索引",
        )

    root = Path(index_root)
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(root, members=_safe_members(tar))

    snapshots = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").exists()]
    if not snapshots:
        raise PmagentError("制品中没有找到 manifest")
    latest = max(snapshots, key=lambda p: (p / "manifest.json").stat().st_mtime)
    (root / "current").write_text(latest.name, encoding="utf-8")
    return latest.name
