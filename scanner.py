"""File scanning and hashing engine. Thread-safe, cancellable."""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


@dataclass
class FileEntry:
    path: Path
    size: int
    mtime: float
    digest: str | None = None  # filled after hashing


@dataclass
class ScanResult:
    groups: list[list[FileEntry]] = field(default_factory=list)
    files_scanned: int = 0
    bytes_hashed: int = 0
    errors: list[str] = field(default_factory=list)


class Cancelled(Exception):
    pass


class Scanner:
    """
    Two-phase dedupe:
      1. Walk tree, group by size (cheap).
      2. Hash only size-collision groups (expensive but rare).
    """

    def __init__(
        self,
        roots: Iterable[Path],
        chunk_size: int = 1 << 20,      # 1 MiB read chunks
        min_size: int = 1,               # skip 0-byte files
        follow_symlinks: bool = False,
        hash_name: str = "blake2b",
    ) -> None:
        self.roots = [Path(r) for r in roots]
        self.chunk_size = chunk_size
        self.min_size = min_size
        self.follow_symlinks = follow_symlinks
        self.hash_name = hash_name
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    # ---------- phase 1 ----------
    def _walk(self, on_progress: Callable[[int], None] | None) -> tuple[dict[int, list[FileEntry]], ScanResult]:
        by_size: dict[int, list[FileEntry]] = {}
        result = ScanResult()
        seen_inodes: set[tuple[int, int]] = set()  # (dev, ino) -> skip hardlinks

        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root, followlinks=self.follow_symlinks):
                self._check_cancel()
                # skip common noise
                dirnames[:] = [d for d in dirnames if d not in {".git", "node_modules", "__pycache__"}]
                for name in filenames:
                    self._check_cancel()
                    p = Path(dirpath) / name
                    try:
                        st = p.stat()
                    except OSError as e:
                        result.errors.append(f"{p}: {e}")
                        continue
                    if not os.path.isfile(p):
                        continue
                    if st.st_size < self.min_size:
                        continue
                    # hardlink guard: identical inode = same file, not a dupe
                    key = (st.st_dev, st.st_ino)
                    if key in seen_inodes:
                        continue
                    seen_inodes.add(key)
                    by_size.setdefault(st.st_size, []).append(
                        FileEntry(path=p, size=st.st_size, mtime=st.st_mtime)
                    )
                    result.files_scanned += 1
                    if on_progress and result.files_scanned % 200 == 0:
                        on_progress(result.files_scanned)
        return by_size, result

    # ---------- phase 2 ----------
    def _hash_file(self, entry: FileEntry) -> str:
        h = hashlib.new(self.hash_name)
        with open(entry.path, "rb") as f:
            while True:
                self._check_cancel()
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _hash_groups(
        self,
        by_size: dict[int, list[FileEntry]],
        result: ScanResult,
        on_progress: Callable[[int, int], None] | None,
    ) -> None:
        candidates = [entries for entries in by_size.values() if len(entries) > 1]
        total = sum(len(e) for e in candidates)
        done = 0

        for entries in candidates:
            self._check_cancel()
            # hash all in group
            for entry in entries:
                self._check_cancel()
                try:
                    entry.digest = self._hash_file(entry)
                    result.bytes_hashed += entry.size
                except OSError as e:
                    result.errors.append(f"{entry.path}: {e}")
                    entry.digest = None
                done += 1
                if on_progress:
                    on_progress(done, total)

            # regroup by digest
            by_digest: dict[str, list[FileEntry]] = {}
            for entry in entries:
                if entry.digest:
                    by_digest.setdefault(entry.digest, []).append(entry)
            for group in by_digest.values():
                if len(group) > 1:
                    result.groups.append(sorted(group, key=lambda e: e.path))

    # ---------- public ----------
    def run(
        self,
        on_walk: Callable[[int], None] | None = None,
        on_hash: Callable[[int, int], None] | None = None,
    ) -> ScanResult:
        by_size, result = self._walk(on_walk)
        self._hash_groups(by_size, result, on_hash)
        result.groups.sort(key=lambda g: (-g[0].size, str(g[0].path)))
        return result


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def wasted_bytes(groups: list[list[FileEntry]]) -> int:
    """Space recoverable if we keep one file per group."""
    return sum((len(g) - 1) * g[0].size for g in groups)