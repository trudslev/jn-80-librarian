from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .position import WritePosition

F0 = 0xF0
F7 = 0xF7
HEADER_PREFIX = bytes([0xF0, 0x00, 0x20, 0x32, 0x00, 0x01, 0x1D, 0x00, 0x78])
ADDRESS_LEN = 2
DATA_START_INDEX = len(HEADER_PREFIX) + ADDRESS_LEN
BANK_DATA_OFFSET = 5
SLOT_DATA_OFFSET = 6
HEX_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]{2}$")


@dataclass(frozen=True)
class ParsedSysEx:
    raw: bytes

    @property
    def data_start(self) -> int:
        return DATA_START_INDEX


def load_syx_file(path: Path) -> bytes:
    data = path.read_bytes()
    return data


def _parse_ascii_hex(raw: bytes) -> bytes | None:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None

    normalized = text.replace("\n", " ").replace("\r", " ")
    tokens = [token for token in re.split(r"[\s,;]+", normalized) if token]
    if not tokens:
        return None

    parsed: list[int] = []
    for token in tokens:
        token = token.strip()
        if token.startswith(("0x", "0X")):
            token = token[2:]
        if not HEX_TOKEN_RE.match(token):
            return None
        parsed.append(int(token, 16))
    return bytes(parsed)


def _extract_first_frame(raw: bytes) -> bytes:
    try:
        start = raw.index(F0)
    except ValueError as exc:
        raise ValueError("Invalid .syx file: missing F0 start byte") from exc

    try:
        end = raw.index(F7, start + 1)
    except ValueError as exc:
        raise ValueError("Invalid .syx file: missing F7 end byte") from exc

    return raw[start : end + 1]


def parse_sysex_message(raw: bytes) -> ParsedSysEx:
    normalized = raw
    if not normalized:
        raise ValueError("Invalid .syx file: empty file")

    if normalized[0] != F0 or normalized[-1] != F7:
        parsed_hex = _parse_ascii_hex(normalized)
        if parsed_hex is not None:
            normalized = parsed_hex

    normalized = _extract_first_frame(normalized)

    if len(normalized) < DATA_START_INDEX + SLOT_DATA_OFFSET + 1 + 1:
        raise ValueError("Invalid .syx file: message too short")
    if normalized[0] != F0:
        raise ValueError("Invalid .syx file: missing F0 start byte")
    if normalized[-1] != F7:
        raise ValueError("Invalid .syx file: missing F7 end byte")
    if bytes(normalized[: len(HEADER_PREFIX)]) != HEADER_PREFIX:
        raise ValueError("Invalid .syx file: unsupported JN-80 SysEx header")

    for idx, value in enumerate(normalized[1:-1], start=1):
        if value > 0x7F:
            raise ValueError(f"Invalid .syx file: non-7-bit payload at index {idx}")

    return ParsedSysEx(raw=normalized)


def patch_bank_slot_in_memory(parsed: ParsedSysEx, position: WritePosition) -> bytes:
    position.validate()
    patched = bytearray(parsed.raw)

    # JN-80 uses write address (7+7 bits) as destination 0..399.
    storage_address = (position.bank_index * 20) + position.slot_index
    address_low = storage_address & 0x7F
    address_high = (storage_address >> 7) & 0x7F
    patched[len(HEADER_PREFIX)] = address_low
    patched[len(HEADER_PREFIX) + 1] = address_high

    bank_idx = parsed.data_start + BANK_DATA_OFFSET
    slot_idx = parsed.data_start + SLOT_DATA_OFFSET
    patched[bank_idx] = position.bank_index
    patched[slot_idx] = position.slot_index
    return bytes(patched)


def to_mido_sysex_data(message_bytes: bytes) -> list[int]:
    if len(message_bytes) < 2 or message_bytes[0] != F0 or message_bytes[-1] != F7:
        raise ValueError("Invalid SysEx bytes for MIDI send")
    return list(message_bytes[1:-1])
