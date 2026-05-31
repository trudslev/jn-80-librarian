import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jn80_librarian.browser import BrowserEntry
from jn80_librarian.app import (
    AppState,
    _active_patch_summary,
    _f3_mode,
    _handle_f3_merge,
    _handle_f2,
    _handle_f5,
    _handle_f6,
    _handle_f8,
    _merge_selected_files,
    _parse_index_selection,
    _prompt_bank_slot,
    _prompt_init_range,
    _handle_receive,
    _move_cursor_page,
    _page_down_pin_top,
    _positions_inclusive,
    _save_split_frames_as_files,
    _save_selected_frames_to_file,
    _send_init_range,
    _send_files,
    _split_output_filename,
)
from jn80_librarian.midi import MidiReceiveResult, MidiResult
from jn80_librarian.position import WritePosition
from jn80_librarian.sysex import HEADER_PREFIX


def make_message() -> bytes:
    address = bytes([0x00, 0x00])
    data = bytes([0x01] * 103)
    return HEADER_PREFIX + address + data + bytes([0xF7])


def make_named_message(name: str, bank_index: int = 0, slot_index: int = 0) -> bytes:
    storage_address = (bank_index * 20) + slot_index
    address = bytes([storage_address & 0x7F, (storage_address >> 7) & 0x7F])
    data = bytearray([0x01] * 103)
    data[5] = bank_index
    data[6] = slot_index
    encoded = name.encode("ascii", errors="ignore")[:16]
    start = 20
    data[start : start + len(encoded)] = encoded
    return HEADER_PREFIX + address + bytes(data) + bytes([0xF7])


class TestAppSendFlow(unittest.TestCase):
    def test_active_patch_summary_reports_patch_count_for_highlighted_syx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "multi.syx"
            file_path.write_bytes(make_message() + make_message())

            state = AppState(cwd=root)
            state.entries = [BrowserEntry(path=file_path, name=file_path.name, is_dir=False)]
            state.cursor = 0

            self.assertEqual(_active_patch_summary(state), "Patches: 2")

    def test_active_patch_summary_reports_invalid_for_bad_syx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "bad.syx"
            file_path.write_bytes(b"not-a-valid-sysex")

            state = AppState(cwd=root)
            state.entries = [BrowserEntry(path=file_path, name=file_path.name, is_dir=False)]
            state.cursor = 0

            self.assertEqual(_active_patch_summary(state), "Patches: invalid")

    def test_active_patch_summary_is_none_for_directories(self) -> None:
        state = AppState(cwd=Path.cwd())
        state.entries = [BrowserEntry(path=Path.cwd(), name=".", is_dir=True)]
        state.cursor = 0

        self.assertIsNone(_active_patch_summary(state))

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

    def test_send_files_sends_all_presets_in_single_multi_frame_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "multi.syx"
            file_path.write_bytes(make_message() + make_message())

            state = AppState(cwd=root, selected_port="JN80")
            state.selection_order = {file_path: 1}

            with patch(
                "jn80_librarian.app.send_sysex",
                return_value=MidiResult(True, "ok", ack_received=True, ack_message="JN-80 reply: 00"),
            ) as mock_send:
                ok, message, last = _send_files(state, WritePosition("A", 1))

            self.assertTrue(ok)
            self.assertEqual(last, WritePosition("A", 2))
            self.assertEqual(mock_send.call_count, 2)
            self.assertIn("Sent 2 presets", message)
            self.assertIn("ACK 2/2", message)

    def test_send_files_blocks_when_destination_capacity_is_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "overflow.syx"
            file_path.write_bytes(make_message() + make_message())

            state = AppState(cwd=root, selected_port="JN80")
            state.selection_order = {file_path: 1}

            with patch("jn80_librarian.app.send_sysex") as mock_send:
                ok, message, last = _send_files(state, WritePosition("T", 20))

            self.assertFalse(ok)
            self.assertIsNone(last)
            self.assertIn("Not enough destination slots", message)
            mock_send.assert_not_called()

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

    def test_handle_f6_stops_at_t20_without_wrapping(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")
        state.last_written = WritePosition("T", 20)

        with patch("jn80_librarian.app._send_files_with_progress") as mock_send:
            _handle_f6(None, state)

        self.assertIn("no next slot", state.status.lower())
        self.assertEqual(state.last_written, WritePosition("T", 20))
        mock_send.assert_not_called()

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

    def test_f3_mode_prefers_merge_for_multi_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.syx"
            second = root / "b.syx"
            first.write_bytes(make_message())
            second.write_bytes(make_message())

            state = AppState(cwd=root)
            state.selection_order = {first: 1, second: 2}

            self.assertEqual(_f3_mode(state), "merge")

    def test_f3_mode_split_for_highlighted_multi_preset_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "multi.syx"
            file_path.write_bytes(make_message() + make_message())

            state = AppState(cwd=root)
            state.entries = [BrowserEntry(path=file_path, name=file_path.name, is_dir=False)]
            state.cursor = 0

            self.assertEqual(_f3_mode(state), "split")

    def test_merge_selected_files_concatenates_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.syx"
            second = root / "b.syx"
            first_frame = make_named_message("FIRST")
            second_frame = make_named_message("SECOND")
            first.write_bytes(first_frame)
            second.write_bytes(second_frame)

            state = AppState(cwd=root)
            ok, message = _merge_selected_files(state, [first, second], root / "merged.syx")

            self.assertTrue(ok)
            self.assertIn("2 files", message)
            self.assertEqual((root / "merged.syx").read_bytes(), first_frame + second_frame)

    def test_handle_f3_merge_clears_selection_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.syx"
            second = root / "b.syx"
            first.write_bytes(make_message())
            second.write_bytes(make_message())

            state = AppState(cwd=root)
            state.selection_order = {first: 1, second: 2}

            _handle_f3_merge(None, state)

            self.assertEqual(state.selection_order, {})
            self.assertTrue((root / "merged.syx").exists())

    def test_parse_index_selection_supports_lists_and_ranges(self) -> None:
        self.assertEqual(_parse_index_selection("1,3,5-7", 8), [1, 3, 5, 6, 7])

    def test_parse_index_selection_rejects_non_contiguous_when_required(self) -> None:
        with self.assertRaises(ValueError):
            _parse_index_selection("1,3", 5, require_contiguous=True)

    def test_split_output_filename_supports_bank_patch_mode(self) -> None:
        from jn80_librarian.sysex import parse_sysex_message

        parsed = parse_sysex_message(make_named_message("Pad One", bank_index=2, slot_index=6))
        filename = _split_output_filename(parsed, 1, "bank_patch")

        self.assertTrue(filename.startswith("C07 - "))
        self.assertTrue(filename.endswith(".syx"))

    def test_split_output_filename_prefers_storage_address_over_data_bytes(self) -> None:
        from jn80_librarian.sysex import parse_sysex_message

        # Address says D05 (bank index 3, slot index 4), while DATA bytes say C07.
        storage_address = (3 * 20) + 4
        address = bytes([storage_address & 0x7F, (storage_address >> 7) & 0x7F])
        data = bytearray([0x01] * 103)
        data[5] = 2
        data[6] = 6
        name = "Addr Wins"
        encoded = name.encode("ascii")
        data[20 : 20 + len(encoded)] = encoded

        raw = HEADER_PREFIX + address + bytes(data) + bytes([0xF7])
        parsed = parse_sysex_message(raw)
        filename = _split_output_filename(parsed, 1, "bank_patch")

        self.assertTrue(filename.startswith("D05 - Addr Wins"))

    def test_split_output_filename_extracts_null_interleaved_patch_name(self) -> None:
        from jn80_librarian.sysex import parse_sysex_message

        address = bytes([0x00, 0x00])
        data = bytearray([0x01] * 103)
        data[5] = 0
        data[6] = 0
        name = "Warm Pad Full"
        pos = 20
        for char in name:
            if pos + 1 >= len(data):
                break
            data[pos] = ord(char)
            data[pos + 1] = 0x00
            pos += 2

        raw = HEADER_PREFIX + address + bytes(data) + bytes([0xF7])
        parsed = parse_sysex_message(raw)
        filename = _split_output_filename(parsed, 1, "patch")

        self.assertTrue(filename.startswith("Warm Pad Full"))
        self.assertNotIn("Patch 001", filename)

    def test_save_split_frames_as_files_writes_requested_indices(self) -> None:
        from jn80_librarian.sysex import parse_sysex_messages

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = AppState(cwd=root)
            payload = make_named_message("ONE") + make_named_message("TWO") + make_named_message("THREE")
            frames = parse_sysex_messages(payload)

            ok, message = _save_split_frames_as_files(state, frames, [1, 3], "patch")

            self.assertTrue(ok)
            self.assertIn("2 presets", message)
            syx_files = sorted(p.name for p in root.glob("*.syx"))
            self.assertEqual(len(syx_files), 2)

    def test_save_selected_frames_to_file_preserves_selection_order(self) -> None:
        from jn80_librarian.sysex import parse_sysex_messages

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = AppState(cwd=root)
            frame_a = make_named_message("A")
            frame_b = make_named_message("B")
            frame_c = make_named_message("C")
            frames = parse_sysex_messages(frame_a + frame_b + frame_c)

            ok, message = _save_selected_frames_to_file(state, frames, [3, 1], root / "selected.syx")

            self.assertTrue(ok)
            self.assertIn("2 selected presets", message)
            self.assertEqual((root / "selected.syx").read_bytes(), frame_c + frame_a)


class _FakePromptWindow:
    def __init__(self, keys: list[int]) -> None:
        self._keys = keys

    def keypad(self, _enabled: bool) -> None:
        return

    def erase(self) -> None:
        return

    def box(self) -> None:
        return

    def attron(self, _attr: int) -> None:
        return

    def attroff(self, _attr: int) -> None:
        return

    def addnstr(self, _y: int, _x: int, _text: str, _n: int) -> None:
        return

    def move(self, _y: int, _x: int) -> None:
        return

    def refresh(self) -> None:
        return

    def getch(self) -> int:
        return self._keys.pop(0)


class _FakeScreen:
    def getmaxyx(self) -> tuple[int, int]:
        return (24, 80)


class TestPromptBankSlot(unittest.TestCase):
    def test_prompt_resets_persisted_digit_field_on_open(self) -> None:
        _prompt_bank_slot._last_digit_field = 2
        fake_win = _FakePromptWindow([ord("B"), 27])

        with patch("jn80_librarian.app.curses.newwin", return_value=fake_win):
            with patch("jn80_librarian.app.curses.curs_set", return_value=0):
                _prompt_bank_slot(_FakeScreen(), "A01")

        self.assertEqual(_prompt_bank_slot._last_digit_field, 1)

    def test_init_prompt_resets_persisted_digit_fields_on_open(self) -> None:
        _prompt_init_range._last_from_digit_field = 2
        _prompt_init_range._last_to_digit_field = 5
        fake_win = _FakePromptWindow([ord("B"), 27])

        with patch("jn80_librarian.app.curses.newwin", return_value=fake_win):
            with patch("jn80_librarian.app.curses.curs_set", return_value=0):
                _prompt_init_range(_FakeScreen(), "A01", "A01")

        self.assertEqual(_prompt_init_range._last_from_digit_field, 1)
        self.assertEqual(_prompt_init_range._last_to_digit_field, 4)

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
