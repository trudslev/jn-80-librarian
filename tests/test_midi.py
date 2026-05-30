import unittest
from unittest.mock import MagicMock, patch

import mido

from jn80_librarian.midi import _classify_reply, receive_sysex, send_sysex


class TestMidiReplies(unittest.TestCase):
    def test_classify_jn80_reply(self) -> None:
        msg = mido.Message(
            "sysex",
            data=[0x00, 0x20, 0x32, 0x00, 0x01, 0x1D, 0x00, 0x01, 0x00, 0x00],
        )
        result = _classify_reply(msg)
        self.assertIsNotNone(result)
        self.assertIn("JN-80 reply", result)

    def test_ignore_non_sysex(self) -> None:
        msg = mido.Message("note_on", note=60, velocity=100)
        result = _classify_reply(msg)
        self.assertIsNone(result)

    def test_send_sysex_requires_selected_port(self) -> None:
        result = send_sysex(None, [0x00])
        self.assertFalse(result.ok)
        self.assertIn("No MIDI port selected", result.message)

    def test_send_sysex_unavailable_port(self) -> None:
        with patch("jn80_librarian.midi.list_output_ports", return_value=["Other Port"]):
            result = send_sysex("JN80", [0x00])
        self.assertFalse(result.ok)
        self.assertIn("MIDI port unavailable", result.message)

    def test_send_sysex_ack_received(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [[], [mido.Message("sysex", data=[0x00, 0x20, 0x32, 0x00, 0x01, 0x1D])]]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        out_port = MagicMock()
        out_cm = MagicMock()
        out_cm.__enter__.return_value = out_port
        out_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_output_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
                with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                    with patch("jn80_librarian.midi.mido.open_output", return_value=out_cm):
                        with patch("jn80_librarian.midi.time.sleep", return_value=None):
                            result = send_sysex("JN80", [0x00], ack_timeout_sec=0.2)

        self.assertTrue(result.ok)
        self.assertTrue(result.ack_received)
        self.assertIn("Send successful", result.message)
        out_port.send.assert_called_once()

    def test_send_sysex_no_ack_timeout(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [[], []]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        out_port = MagicMock()
        out_cm = MagicMock()
        out_cm.__enter__.return_value = out_port
        out_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_output_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
                with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                    with patch("jn80_librarian.midi.mido.open_output", return_value=out_cm):
                        with patch("jn80_librarian.midi.time.monotonic", side_effect=[0.0, 1.0]):
                            result = send_sysex("JN80", [0x00], ack_timeout_sec=0.5)

        self.assertTrue(result.ok)
        self.assertFalse(result.ack_received)
        self.assertIn("no reply", result.message.lower())

    def test_receive_sysex_requires_selected_port(self) -> None:
        result = receive_sysex(None)
        self.assertFalse(result.ok)
        self.assertIn("No MIDI port selected", result.message)

    def test_receive_sysex_timeout(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [[], []]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                with patch("jn80_librarian.midi.time.monotonic", side_effect=[0.0, 9.0]):
                    result = receive_sysex("JN80", timeout_sec=8.0)

        self.assertFalse(result.ok)
        self.assertIn("No SysEx received", result.message)

    def test_receive_sysex_success(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [
            [],
            [mido.Message("sysex", data=[0x00, 0x20, 0x32, 0x00, 0x01, 0x1D, 0x01])],
            [],
        ]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                with patch("jn80_librarian.midi.time.monotonic", side_effect=[0.0, 0.1, 0.5]):
                    with patch("jn80_librarian.midi.time.sleep", return_value=None):
                        result = receive_sysex("JN80", timeout_sec=1.0, inter_message_timeout_sec=0.3)

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.sysex_bytes)
        self.assertEqual(result.sysex_bytes[0], 0xF0)
        self.assertEqual(result.sysex_bytes[-1], 0xF7)
        self.assertEqual(len(result.sysex_messages or []), 1)

    def test_receive_sysex_multiple_messages(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [
            [],
            [
                mido.Message("sysex", data=[0x00, 0x20, 0x32, 0x00, 0x01, 0x1D, 0x01]),
                mido.Message("sysex", data=[0x00, 0x20, 0x32, 0x00, 0x01, 0x1D, 0x02]),
            ],
            [],
        ]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                with patch("jn80_librarian.midi.time.monotonic", side_effect=[0.0, 0.1, 0.5]):
                    with patch("jn80_librarian.midi.time.sleep", return_value=None):
                        result = receive_sysex("JN80", timeout_sec=1.0, inter_message_timeout_sec=0.3)

        self.assertTrue(result.ok)
        self.assertEqual(len(result.sysex_messages or []), 2)
        self.assertIn("2", result.message)

    def test_receive_sysex_canceled_by_user(self) -> None:
        in_port = MagicMock()
        in_port.iter_pending.side_effect = [[], []]
        in_cm = MagicMock()
        in_cm.__enter__.return_value = in_port
        in_cm.__exit__.return_value = None

        with patch("jn80_librarian.midi.list_input_ports", return_value=["JN80"]):
            with patch("jn80_librarian.midi.mido.open_input", return_value=in_cm):
                with patch("jn80_librarian.midi.time.monotonic", side_effect=[0.0, 0.1]):
                    result = receive_sysex(
                        "JN80",
                        timeout_sec=8.0,
                        should_cancel=lambda: True,
                    )

        self.assertFalse(result.ok)
        self.assertIn("canceled", result.message.lower())


if __name__ == "__main__":
    unittest.main()
