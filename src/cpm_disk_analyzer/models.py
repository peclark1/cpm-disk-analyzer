"""Data models shared by the analyzer, CLI, and GUI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Evidence:
    category: str
    message: str
    points: int = 0


@dataclass(frozen=True)
class DirectoryEntry:
    user: int
    name: str
    extent: int
    records: int
    allocation_blocks: tuple[int, ...]
    deleted: bool = False

    @property
    def estimated_size(self) -> int:
        return self.records * 128


@dataclass(frozen=True)
class LogicalFile:
    user: int
    name: str
    extents: tuple[DirectoryEntry, ...]

    @property
    def records(self) -> int:
        return sum(entry.records for entry in self.extents)

    @property
    def estimated_size(self) -> int:
        return self.records * 128


@dataclass
class CandidateResult:
    profile_id: str
    profile_name: str
    score: int
    confidence: str
    geometry: dict[str, Any]
    filesystem: dict[str, Any]
    evidence: list[Evidence] = field(default_factory=list)
    files: list[DirectoryEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImageResult:
    path: Path
    sha256: str
    size: int
    container: str
    container_metadata: dict[str, Any]
    observations: list[str]
    candidates: list[CandidateResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def best_candidate(self) -> CandidateResult | None:
        return self.candidates[0] if self.candidates else None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value
