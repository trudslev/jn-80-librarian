from __future__ import annotations

from dataclasses import dataclass

BANKS = "ABCDEFGHIJKLMNOPQRST"
SLOTS_PER_BANK = 20


@dataclass(frozen=True)
class WritePosition:
    bank: str
    slot: int

    def validate(self) -> None:
        if self.bank not in BANKS:
            raise ValueError(f"Invalid bank: {self.bank}")
        if not (1 <= self.slot <= SLOTS_PER_BANK):
            raise ValueError(f"Invalid slot: {self.slot}")

    @property
    def bank_index(self) -> int:
        self.validate()
        return BANKS.index(self.bank)

    @property
    def slot_index(self) -> int:
        self.validate()
        return self.slot - 1


def from_indices(bank_index: int, slot_index: int) -> WritePosition:
    if not (0 <= bank_index < len(BANKS)):
        raise ValueError(f"Invalid bank index: {bank_index}")
    if not (0 <= slot_index < SLOTS_PER_BANK):
        raise ValueError(f"Invalid slot index: {slot_index}")
    return WritePosition(BANKS[bank_index], slot_index + 1)


def increment_position(position: WritePosition) -> WritePosition:
    position.validate()
    bank_index = position.bank_index
    slot = position.slot + 1
    if slot > SLOTS_PER_BANK:
        slot = 1
        bank_index = (bank_index + 1) % len(BANKS)
    return WritePosition(BANKS[bank_index], slot)
