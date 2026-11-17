"""代码索引：文件发现、符号抽取、切块、存储与分发（M05）。"""

from pmagent.knowledge.index.chunker import CHUNK_POLICY_VERSION, build_chunks
from pmagent.knowledge.index.discovery import discover_files
from pmagent.knowledge.index.models import (
    Chunk,
    FileRecord,
    IncludeEdge,
    ParsedFile,
    RefEdge,
    Symbol,
)
from pmagent.knowledge.index.package import pack_index, unpack_index
from pmagent.knowledge.index.parser import parse_source
from pmagent.knowledge.index.store import (
    SCHEMA_VERSION,
    CodeIndex,
    EmbeddingMeta,
    IndexBuilder,
    IndexVersionMismatch,
    Manifest,
)

__all__ = [
    "CHUNK_POLICY_VERSION",
    "SCHEMA_VERSION",
    "Chunk",
    "CodeIndex",
    "EmbeddingMeta",
    "FileRecord",
    "IncludeEdge",
    "IndexBuilder",
    "IndexVersionMismatch",
    "Manifest",
    "ParsedFile",
    "RefEdge",
    "Symbol",
    "build_chunks",
    "discover_files",
    "pack_index",
    "parse_source",
    "unpack_index",
]
