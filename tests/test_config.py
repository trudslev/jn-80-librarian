import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jn80_librarian.config import AppConfig, load_config, save_config
from jn80_librarian.position import WritePosition


class TestConfig(unittest.TestCase):
    def test_save_and_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg = AppConfig(
                last_midi_port="JN80",
                last_write=WritePosition("C", 7),
                last_f5_target=WritePosition("B", 11),
                last_browsed_dir="/tmp/patches",
                last_init_from=WritePosition("D", 2),
                last_init_to=WritePosition("E", 9),
            )

            with patch("jn80_librarian.config.config_path", return_value=cfg_path):
                save_config(cfg)
                loaded = load_config()

            self.assertEqual(loaded.last_midi_port, "JN80")
            self.assertEqual(loaded.last_write, WritePosition("C", 7))
            self.assertEqual(loaded.last_f5_target, WritePosition("B", 11))
            self.assertEqual(loaded.last_browsed_dir, "/tmp/patches")
            self.assertEqual(loaded.last_init_from, WritePosition("D", 2))
            self.assertEqual(loaded.last_init_to, WritePosition("E", 9))

    def test_load_invalid_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text("{not valid json", encoding="utf-8")

            with patch("jn80_librarian.config.config_path", return_value=cfg_path):
                loaded = load_config()

            self.assertIsNone(loaded.last_midi_port)
            self.assertIsNone(loaded.last_write)
            self.assertIsNone(loaded.last_f5_target)
            self.assertIsNone(loaded.last_browsed_dir)
            self.assertIsNone(loaded.last_init_from)
            self.assertIsNone(loaded.last_init_to)

    def test_invalid_stored_write_position_is_ignored(self) -> None:
        data = {
            "last_midi_port": "JN80",
            "last_write_bank": "Z",
            "last_write_slot": 22,
            "last_browsed_dir": "/tmp",
        }
        loaded = AppConfig.from_dict(data)
        self.assertEqual(loaded.last_midi_port, "JN80")
        self.assertIsNone(loaded.last_write)


if __name__ == "__main__":
    unittest.main()
