import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jn80_librarian.browser import BrowserEntry
from jn80_librarian.app import (
    AppState,
    _handle_f2,
    _handle_f5,
    _handle_f6,
    _handle_f8,
    _handle_receive,
    _move_cursor_page,
    _page_down_pin_top,
    _positions_inclusive,
    _send_init_range,
    _send_files,
)
from jn80_librarian.midi import MidiReceiveResult, MidiResult
from jn80_librarian.position import WritePosition
from jn80_librarian.sysex import HEADER_PREFIX


def make_message() -> bytes:
    address = bytes([0x00, 0x00])
    data = bytes([0x01] * 103)
    return HEADER_PREFIX + address + data + bytes([0xF7])


class TestAppSendFlow(unittest.TestCase):
    def test_positions_inclusive_builds_cross_bank_range(self) -> None:
        positions = _positions_inclusive(WritePosition("A", 19), WritePosition("B", 2))
        self.assertEqual(
            positions,
            [
                WritePosition("A", 19),
                WritePosition("A", 20),
                WritePosition("B", 1),
                WritePosition("B", 2),
            ],
        )

    def test_positions_inclusive_returns_empty_for_reversed_range(self) -> None:
        positions = _positions_inclusive(WritePosition("C", 3), WritePosition("B", 20))
        self.assertEqual(positions, [])

    def test_move_cursor_page_moves_down_by_page(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path(f"file_{i}.syx"), name=f"file_{i}.syx", is_dir=False) for i in range(50)]
        state.cursor = 5

        _move_cursor_page(state, page_size=10, direction=1)

        self.assertEqual(state.cursor, 15)

    def test_move_cursor_page_moves_up_by_page_and_clamps(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path(f"file_{i}.syx"), name=f"file_{i}.syx", is_dir=False) for i in range(50)]
        state.cursor = 4

        _move_cursor_page(state, page_size=10, direction=-1)

        self.assertEqual(state.cursor, 0)

    def test_move_cursor_page_clamps_to_last_entry(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path(f"file_{i}.syx"), name=f"file_{i}.syx", is_dir=False) for i in range(12)]
        state.cursor = 8

        _move_cursor_page(state, page_size=10, direction=1)

        self.assertEqual(state.cursor, 11)

    def test_page_down_pin_top_sets_scroll_to_new_cursor(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path(f"file_{i}.syx"), name=f"file_{i}.syx", is_dir=False) for i in range(50)]
        state.cursor = 5
        state.scroll = 2

        _page_down_pin_top(state, page_size=10)

        self.assertEqual(state.cursor, 15)
        self.assertEqual(state.scroll, 15)

    def test_page_down_pin_top_clamps_scroll_to_last_full_page(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path(f"file_{i}.syx"), name=f"file_{i}.syx", is_dir=False) for i in range(12)]
        state.cursor = 8
        state.scroll = 4

        _page_down_pin_top(state, page_size=10)

        self.assertEqual(state.cursor, 11)
        self.assertEqual(state.scroll, 2)

    def test_send_files_uses_selection_timestamp_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.syx"
            second = root / "b.syx"
            first.write_bytes(make_message())
            second.write_bytes(make_message())

            state = AppState(cwd=root, selected_port="JN80")
            # Timestamp order: first selected, first sent.
            state.selection_order = {second: 2, first: 1}

            with patch("jn80_librarian.app.load_syx_file", side_effect=lambda p: p.read_bytes()) as mock_load:
                with patch(
                    "jn80_librarian.app.send_sysex",
                    return_value=MidiResult(True, "ok", ack_received=True, ack_message="JN-80 reply: 00"),
                ):
                    ok, message, last = _send_files(state, WritePosition("A", 1))

            self.assertTrue(ok)
            self.assertEqual(last, WritePosition("A", 2))
            self.assertIn("ACK 2/2", message)
            self.assertIn("JN-80 confirmed", message)
            ordered_paths = [call.args[0] for call in mock_load.call_args_list]
            self.assertEqual(ordered_paths, [first, second])

    def test_handle_f5_clears_selection_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "dummy.syx"
            file_path.write_bytes(make_message())

            state = AppState(cwd=root, selected_port="JN80")
            state.selection_order = {file_path: 1}

            with patch("jn80_librarian.app._prompt_bank_slot", return_value=WritePosition("A", 1)):
                with patch(
                    "jn80_librarian.app._send_files_with_progress",
                    return_value=(True, "Sent", WritePosition("B", 3)),
                ):
                    _handle_f5(None, state)

            self.assertEqual(state.last_written, WritePosition("B", 3))
            self.assertEqual(state.selection_order, {})

    def test_handle_f5_prefills_from_last_f5_target(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80", last_f5_target=WritePosition("D", 12))
        with patch("jn80_librarian.app._prompt_bank_slot", return_value=None) as mock_prompt:
            _handle_f5(None, state)

        args = mock_prompt.call_args.args
        self.assertEqual(args[1], "D12")

    def test_handle_f6_keeps_selection_on_failure(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")
        state.last_written = WritePosition("A", 1)
        file_path = Path.cwd() / "dummy.syx"
        state.selection_order = {file_path: 1}

        with patch("jn80_librarian.app._send_files_with_progress", return_value=(False, "Failed", None)):
            _handle_f6(None, state)

        self.assertEqual(state.last_written, WritePosition("A", 1))
        self.assertEqual(state.selection_order, {file_path: 1})

    def test_handle_f2_does_not_set_last_written_on_success(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80", last_written=WritePosition("B", 8))

        with patch(
            "jn80_librarian.app._prompt_init_range",
            return_value=(WritePosition("A", 1), WritePosition("A", 3)),
        ):
            with patch("jn80_librarian.app._prompt_yes_no", return_value=True):
                with patch(
                    "jn80_librarian.app._send_init_range",
                    return_value=(True, "Erased", WritePosition("A", 3)),
                ) as mock_send:
                    _handle_f2(None, state)

        self.assertEqual(state.status, "Erased")
        self.assertEqual(state.last_written, WritePosition("B", 8))
        self.assertEqual(state.last_init_from, WritePosition("A", 1))
        self.assertEqual(state.last_init_to, WritePosition("A", 3))
        mock_send.assert_called_once()

    def test_handle_f2_prefills_with_persisted_values(self) -> None:
        state = AppState(
            cwd=Path.cwd(),
            selected_port="JN80",
            last_init_from=WritePosition("C", 4),
            last_init_to=WritePosition("D", 5),
        )

        with patch(
            "jn80_librarian.app._prompt_init_range",
            return_value=(WritePosition("C", 4), WritePosition("D", 5)),
        ) as mock_prompt:
            with patch("jn80_librarian.app._prompt_yes_no", return_value=False):
                _handle_f2(None, state)

        args = mock_prompt.call_args.args
        self.assertEqual(args[1], "C4")
        self.assertEqual(args[2], "D5")

    def test_handle_f2_cancels_when_not_confirmed(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")

        with patch(
            "jn80_librarian.app._prompt_init_range",
            return_value=(WritePosition("A", 1), WritePosition("A", 2)),
        ):
            with patch("jn80_librarian.app._prompt_yes_no", return_value=False):
                with patch("jn80_librarian.app._send_init_range") as mock_send:
                    _handle_f2(None, state)

        self.assertEqual(state.status, "INIT canceled")
        mock_send.assert_not_called()

    def test_handle_f2_rejects_reversed_range(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")

        with patch(
            "jn80_librarian.app._prompt_init_range",
            return_value=(WritePosition("B", 3), WritePosition("A", 2)),
        ):
            with patch("jn80_librarian.app._prompt_yes_no") as mock_confirm:
                _handle_f2(None, state)

        self.assertIn("Invalid range", state.status)
        mock_confirm.assert_not_called()

    def test_handle_receive_saves_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = AppState(cwd=root, selected_port="JN80")

            payload = bytes([0xF0, 0x00, 0x20, 0x32, 0xF7])
            with patch("jn80_librarian.app._default_receive_filename", return_value="captured.syx"):
                with patch(
                    "jn80_librarian.app.receive_sysex",
                    return_value=MidiReceiveResult(True, "Received JN-80 SysEx", sysex_bytes=payload),
                ):
                    _handle_receive(None, state)

            saved = root / "captured.syx"
            self.assertTrue(saved.exists())
            self.assertEqual(saved.read_bytes(), payload)
            self.assertIn("Receive OK", state.status)

    def test_handle_receive_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = AppState(cwd=Path(tmp), selected_port="JN80")
            with patch("jn80_librarian.app._default_receive_filename", return_value="captured.syx"):
                with patch(
                    "jn80_librarian.app.receive_sysex",
                    return_value=MidiReceiveResult(False, "No SysEx received within 8s", sysex_bytes=None),
                ):
                    _handle_receive(None, state)

            self.assertEqual(state.status, "No SysEx received within 8s")

    def test_handle_receive_combines_multiple_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = AppState(cwd=root, selected_port="JN80")

            payload_a = bytes([0xF0, 0x00, 0x20, 0x32, 0x01, 0xF7])
            payload_b = bytes([0xF0, 0x00, 0x20, 0x32, 0x02, 0xF7])
            with patch("jn80_librarian.app._default_receive_filename", return_value="captured.syx"):
                with patch(
                    "jn80_librarian.app.receive_sysex",
                    return_value=MidiReceiveResult(
                        True,
                        "Received 2 JN-80 SysEx messages",
                        sysex_bytes=payload_a,
                        sysex_messages=[payload_a, payload_b],
                        received_count=2,
                        jn80_count=2,
                    ),
                ):
                    _handle_receive(None, state)

            combined = root / "captured.syx"
            self.assertTrue(combined.exists())
            self.assertEqual(combined.read_bytes(), payload_a + payload_b)
            self.assertIn("saved 1", state.status.lower())

    def test_send_init_range_reports_progress(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")
        progress_calls: list[tuple[int, int, WritePosition]] = []

        def _on_progress(done: int, total: int, current: WritePosition) -> None:
            progress_calls.append((done, total, current))

        with patch(
            "jn80_librarian.app.send_sysex",
            return_value=MidiResult(True, "ok", ack_received=True, ack_message="JN-80 reply: 00"),
        ):
            ok, _, last = _send_init_range(
                state,
                WritePosition("A", 1),
                WritePosition("A", 3),
                on_progress=_on_progress,
            )

        self.assertTrue(ok)
        self.assertEqual(last, WritePosition("A", 3))
        self.assertEqual(len(progress_calls), 3)
        self.assertEqual(progress_calls[0], (1, 3, WritePosition("A", 1)))
        self.assertEqual(progress_calls[1], (2, 3, WritePosition("A", 2)))
        self.assertEqual(progress_calls[2], (3, 3, WritePosition("A", 3)))

    def test_handle_f8_deletes_selected_files_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.syx"
            second = root / "b.syx"
            first.write_bytes(make_message())
            second.write_bytes(make_message())

            state = AppState(cwd=root, selected_port="JN80")
            state.selection_order = {first: 1, second: 2}

            with patch("jn80_librarian.app._prompt_yes_no", return_value=True):
                _handle_f8(None, state)

            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertEqual(state.selection_order, {})
            self.assertEqual(state.status, "Deleted 2/2 files")

    def test_handle_f8_cancels_delete_when_not_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "single.syx"
            file_path.write_bytes(make_message())

            state = AppState(cwd=root, selected_port="JN80")
            state.entries = [BrowserEntry(path=file_path, name=file_path.name, is_dir=False)]
            state.cursor = 0

            with patch("jn80_librarian.app._prompt_yes_no", return_value=False):
                _handle_f8(None, state)

            self.assertTrue(file_path.exists())
            self.assertEqual(state.status, "Delete canceled")


if __name__ == "__main__":
    unittest.main()
