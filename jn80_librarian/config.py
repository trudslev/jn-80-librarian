from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .position import WritePosition

APP_NAME = "jn80-librarian"


def _config_dir() -> Path:
    home = Path.home()
    macos_dir = home / "Library" / "Application Support" / APP_NAME
    if macos_dir.parent.exists():
        return macos_dir
    return home / ".config" / APP_NAME


def config_path() -> Path:
    return _config_dir() / "config.json"


@dataclass
class AppConfig:
    last_midi_port: Optional[str] = None
    last_write: Optional[WritePosition] = None
    last_browsed_dir: Optional[str] = None

    def to_dict(self) -> dict:
        data = {
            "last_midi_port": self.last_midi_port,
            "last_browsed_dir": self.last_browsed_dir,
        }
        if self.last_write is None:
            data["last_write_bank"] = None
            data["last_write_slot"] = None
        else:
            data["last_write_bank"] = self.last_write.bank
            data["last_write_slot"] = self.last_write.slot
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        bank = data.get("last_write_bank")
        slot = data.get("last_write_slot")
        last_write = None
        if bank is not None and slot is not None:
            try:
                pos = WritePosition(str(bank).upper(), int(slot))
                pos.validate()
                last_write = pos
            except (ValueError, TypeError):
                last_write = None

        last_browsed_dir = data.get("last_browsed_dir")
        if last_browsed_dir is not None:
            last_browsed_dir = str(last_browsed_dir)

        last_midi_port = data.get("last_midi_port")
        if last_midi_port is not None:
            last_midi_port = str(last_midi_port)

        return cls(
            last_midi_port=last_midi_port,
            last_write=last_write,
            last_browsed_dir=last_browsed_dir,
        )


def load_config() -> AppConfig:
    path = config_path()
    if not path.exists():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return AppConfig.from_dict(data)
    except (json.JSONDecodeError, OSError):
        pass
    return AppConfig()


def save_config(config: AppConfig) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
