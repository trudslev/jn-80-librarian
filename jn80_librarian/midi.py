from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Optional

import mido


@dataclass
class MidiResult:
    ok: bool
    message: str
    ack_received: bool = False
    ack_message: Optional[str] = None


@dataclass
class MidiReceiveResult:
    ok: bool
    message: str
    sysex_bytes: Optional[bytes] = None
    sysex_messages: Optional[list[bytes]] = None
    received_count: int = 0
    jn80_count: int = 0


def list_output_ports() -> list[str]:
    try:
        return list(mido.get_output_names())
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to list MIDI ports: {exc}") from exc


def list_input_ports() -> list[str]:
    try:
        return list(mido.get_input_names())
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Failed to list MIDI input ports: {exc}") from exc


def _match_input_port(output_port_name: str, input_ports: list[str]) -> Optional[str]:
    if output_port_name in input_ports:
        return output_port_name

    output_norm = output_port_name.lower()
    for name in input_ports:
        if output_norm in name.lower() or name.lower() in output_norm:
            return name
    return None


def _format_sysex(data: list[int]) -> str:
    return " ".join(f"{value:02X}" for value in data)


def _classify_reply(message: mido.Message) -> Optional[str]:
    if message.type != "sysex":
        return None

    data = list(message.data)
    if len(data) >= 6 and data[:6] == [0x00, 0x20, 0x32, 0x00, 0x01, 0x1D]:
        return f"JN-80 reply: {_format_sysex(data)}"
    return f"SysEx reply: {_format_sysex(data)}"


def _message_to_sysex_bytes(message: mido.Message) -> bytes:
    return bytes([0xF0] + list(message.data) + [0xF7])


def send_sysex(port_name: Optional[str], sysex_data: list[int], ack_timeout_sec: float = 0.7) -> MidiResult:
    if not port_name:
        return MidiResult(False, "No MIDI port selected")

    try:
        available = set(list_output_ports())
    except RuntimeError as exc:
        return MidiResult(False, str(exc))

    if port_name not in available:
        return MidiResult(False, f"MIDI port unavailable: {port_name}")

    input_port_name: Optional[str] = None
    try:
        input_ports = list_input_ports()
        input_port_name = _match_input_port(port_name, input_ports)
    except RuntimeError:
        input_port_name = None

    try:
        if input_port_name:
            with mido.open_input(input_port_name) as in_port:
                # Drain stale messages so any detected reply belongs to this send.
                for _ in in_port.iter_pending():
                    pass

                with mido.open_output(port_name) as out_port:
                    out_port.send(mido.Message("sysex", data=sysex_data))

                deadline = time.monotonic() + ack_timeout_sec
                while time.monotonic() < deadline:
                    for pending in in_port.iter_pending():
                        reply = _classify_reply(pending)
                        if reply:
                            return MidiResult(True, "Send successful", ack_received=True, ack_message=reply)
                    time.sleep(0.01)

            return MidiResult(True, f"Send successful (no reply within {int(ack_timeout_sec * 1000)}ms)")

        with mido.open_output(port_name) as out_port:
            out_port.send(mido.Message("sysex", data=sysex_data))
        return MidiResult(True, "Send successful (input port not found for reply check)")
    except Exception as exc:  # pragma: no cover
        return MidiResult(False, f"MIDI send failed: {exc}")


def receive_sysex(
    port_name: Optional[str],
    timeout_sec: float = 45.0,
    inter_message_timeout_sec: float = 1.2,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> MidiReceiveResult:
    if not port_name:
        return MidiReceiveResult(False, "No MIDI port selected")

    try:
        input_ports = list_input_ports()
    except RuntimeError as exc:
        return MidiReceiveResult(False, str(exc))

    input_port_name = _match_input_port(port_name, input_ports)
    if not input_port_name:
        return MidiReceiveResult(False, f"MIDI input port unavailable for: {port_name}")

    try:
        with mido.open_input(input_port_name) as in_port:
            for _ in in_port.iter_pending():
                pass

            deadline = time.monotonic() + timeout_sec
            captures: list[bytes] = []
            jn80_count = 0
            last_rx_time: Optional[float] = None

            while True:
                now = time.monotonic()
                if now >= deadline:
                    break

                if should_cancel is not None and should_cancel():
                    return MidiReceiveResult(
                        False,
                        "Receive canceled by user",
                        sysex_messages=captures if captures else None,
                        received_count=len(captures),
                        jn80_count=jn80_count,
                    )

                for pending in in_port.iter_pending():
                    if pending.type != "sysex":
                        continue

                    sysex_bytes = _message_to_sysex_bytes(pending)
                    captures.append(sysex_bytes)
                    last_rx_time = now
                    if len(pending.data) >= 6 and list(pending.data)[:6] == [0x00, 0x20, 0x32, 0x00, 0x01, 0x1D]:
                        jn80_count += 1
                    if on_progress is not None:
                        on_progress(len(captures), jn80_count)

                if captures and last_rx_time is not None and (now - last_rx_time) >= inter_message_timeout_sec:
                    break

                time.sleep(0.01)

        if captures:
            if len(captures) == 1:
                if jn80_count == 1:
                    return MidiReceiveResult(
                        True,
                        "Received JN-80 SysEx",
                        sysex_bytes=captures[0],
                        sysex_messages=captures,
                        received_count=1,
                        jn80_count=1,
                    )
                return MidiReceiveResult(
                    True,
                    "Received SysEx",
                    sysex_bytes=captures[0],
                    sysex_messages=captures,
                    received_count=1,
                    jn80_count=0,
                )

            if jn80_count == len(captures):
                message = f"Received {len(captures)} JN-80 SysEx messages"
            elif jn80_count > 0:
                message = f"Received {len(captures)} SysEx messages ({jn80_count} JN-80)"
            else:
                message = f"Received {len(captures)} SysEx messages"
            return MidiReceiveResult(
                True,
                message,
                sysex_bytes=captures[0],
                sysex_messages=captures,
                received_count=len(captures),
                jn80_count=jn80_count,
            )

        return MidiReceiveResult(False, f"No SysEx received within {int(timeout_sec)}s")
    except Exception as exc:  # pragma: no cover
        return MidiReceiveResult(False, f"MIDI receive failed: {exc}")
