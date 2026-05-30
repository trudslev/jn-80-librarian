import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jn80_librarian.app import (
    AppState,
    _handle_f2,
    _handle_f5,
    _handle_f6,
    _handle_receive,
    _positions_inclusive,
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
        state = AppState(cwd=Path.cwd(), selected_port="JN80")
        file_path = Path.cwd() / "dummy.syx"
        state.selection_order = {file_path: 1}

        with patch("jn80_librarian.app._prompt_bank_slot", return_value=WritePosition("A", 1)):
            with patch(
                "jn80_librarian.app._send_files",
                return_value=(True, "Sent", WritePosition("B", 3)),
            ):
                _handle_f5(None, state)

        self.assertEqual(state.last_written, WritePosition("B", 3))
        self.assertEqual(state.selection_order, {})

    def test_handle_f6_keeps_selection_on_failure(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")
        state.last_written = WritePosition("A", 1)
        file_path = Path.cwd() / "dummy.syx"
        state.selection_order = {file_path: 1}

        with patch("jn80_librarian.app._send_files", return_value=(False, "Failed", None)):
            _handle_f6(None, state)

        self.assertEqual(state.last_written, WritePosition("A", 1))
        self.assertEqual(state.selection_order, {file_path: 1})

    def test_handle_f2_sets_last_written_on_success(self) -> None:
        state = AppState(cwd=Path.cwd(), selected_port="JN80")

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
        self.assertEqual(state.last_written, WritePosition("A", 3))
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


if __name__ == "__main__":
    unittest.main()
