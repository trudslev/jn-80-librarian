import unittest

from jn80_librarian.position import WritePosition
from jn80_librarian.sysex import (
    HEADER_PREFIX,
    patch_bank_slot_in_memory,
    parse_sysex_message,
    parse_sysex_messages,
)


def make_message() -> bytes:
    address = bytes([0x00, 0x00])
    data = bytes([0x01] * 103)
    return HEADER_PREFIX + address + data + bytes([0xF7])


class TestSysex(unittest.TestCase):
    def test_rewrite_only_target_bytes(self) -> None:
        raw = make_message()
        parsed = parse_sysex_message(raw)
        patched = patch_bank_slot_in_memory(parsed, WritePosition("C", 7))

        changed = [i for i, (a, b) in enumerate(zip(raw, patched)) if a != b]
        self.assertEqual(changed, [9, 16, 17])
        self.assertEqual(patched[9], 46)
        self.assertEqual(patched[10], 0)
        self.assertEqual(patched[16], 2)
        self.assertEqual(patched[17], 6)

    def test_rewrite_address_high_bits(self) -> None:
        raw = make_message()
        parsed = parse_sysex_message(raw)
        patched = patch_bank_slot_in_memory(parsed, WritePosition("T", 20))

        self.assertEqual(patched[9], 15)
        self.assertEqual(patched[10], 3)
        self.assertEqual(patched[16], 19)
        self.assertEqual(patched[17], 19)

    def test_invalid_header(self) -> None:
        bad = bytes([0xF0, 0x7D, 0x01, 0xF7])
        with self.assertRaises(ValueError):
            parse_sysex_message(bad)

    def test_missing_f7(self) -> None:
        raw = make_message()[:-1]
        with self.assertRaises(ValueError):
            parse_sysex_message(raw)

    def test_parse_with_leading_and_trailing_bytes(self) -> None:
        raw = bytes([0x01, 0x02]) + make_message() + bytes([0x03, 0x04])
        parsed = parse_sysex_message(raw)
        self.assertEqual(parsed.raw, make_message())

    def test_parse_ascii_hex_dump(self) -> None:
        hex_text = " ".join(f"{b:02X}" for b in make_message()).encode("ascii")
        parsed = parse_sysex_message(hex_text)
        self.assertEqual(parsed.raw, make_message())

    def test_parse_multiple_frames_from_binary(self) -> None:
        frame_a = make_message()
        frame_b = make_message()
        raw = bytes([0x01, 0x02]) + frame_a + bytes([0x03]) + frame_b

        parsed = parse_sysex_messages(raw)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].raw, frame_a)
        self.assertEqual(parsed[1].raw, frame_b)

    def test_parse_multiple_frames_from_ascii_hex_dump(self) -> None:
        frame_a = make_message()
        frame_b = make_message()
        hex_text = " ".join(f"{b:02X}" for b in (frame_a + frame_b)).encode("ascii")

        parsed = parse_sysex_messages(hex_text)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].raw, frame_a)
        self.assertEqual(parsed[1].raw, frame_b)

    def test_parse_sysex_message_returns_first_frame_when_multiple_present(self) -> None:
        frame_a = make_message()
        frame_b = make_message()
        parsed = parse_sysex_message(frame_a + frame_b)
        self.assertEqual(parsed.raw, frame_a)


if __name__ == "__main__":
    unittest.main()
