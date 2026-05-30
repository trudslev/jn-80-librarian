from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    name: str
    is_dir: bool


SYX_EXTENSIONS = {".syx"}


def list_entries(cwd: Path) -> List[BrowserEntry]:
    entries: List[BrowserEntry] = []
    if cwd.parent != cwd:
        entries.append(BrowserEntry(path=cwd.parent, name="..", is_dir=True))

    dirs: List[BrowserEntry] = []
    files: List[BrowserEntry] = []

    try:
        for item in cwd.iterdir():
            if item.is_dir():
                dirs.append(BrowserEntry(path=item, name=item.name, is_dir=True))
            elif item.is_file() and item.suffix.lower() in SYX_EXTENSIONS:
                files.append(BrowserEntry(path=item, name=item.name, is_dir=False))
    except OSError:
        return entries

    dirs.sort(key=lambda e: e.name.lower())
    files.sort(key=lambda e: e.name.lower())
    entries.extend(dirs)
    entries.extend(files)
    return entries
