from __future__ import annotations

import curses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .browser import BrowserEntry, list_entries
from .config import AppConfig, load_config, save_config
from .midi import list_output_ports, receive_sysex, send_sysex
from .position import BANKS, WritePosition, increment_position
from .sysex import load_syx_file, parse_sysex_message, patch_bank_slot_in_memory, to_mido_sysex_data


@dataclass
class AppState:
    cwd: Path
    entries: list[BrowserEntry] = field(default_factory=list)
    cursor: int = 0
    scroll: int = 0
    status: str = "Ready"
    selected_port: Optional[str] = None
    last_written: Optional[WritePosition] = None
    selection_order: dict[Path, int] = field(default_factory=dict)
    select_counter: int = 0

    @property
    def selected_count(self) -> int:
        return len(self.selection_order)


def _safe_cwd(cfg: AppConfig) -> Path:
    if cfg.last_browsed_dir:
        candidate = Path(cfg.last_browsed_dir).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate
    return Path.cwd()


def _refresh_entries(state: AppState) -> None:
    state.entries = list_entries(state.cwd)
    if not state.entries:
        state.cursor = 0
        state.scroll = 0
        return
    state.cursor = max(0, min(state.cursor, len(state.entries) - 1))
    state.scroll = max(0, min(state.scroll, state.cursor))


def _current_entry(state: AppState) -> Optional[BrowserEntry]:
    if not state.entries:
        return None
    if 0 <= state.cursor < len(state.entries):
        return state.entries[state.cursor]
    return None


def _draw(stdscr: curses.window, state: AppState) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    has_box = height >= 7 and width >= 10

    pane_top = 1
    pane_bottom = height - 3
    pane_left = 0
    pane_right = width - 2

    if has_box:
        list_height = max(1, pane_bottom - pane_top - 1)
        row_start = pane_top + 1
        text_col = pane_left + 1
        text_width = max(1, pane_right - pane_left - 1)
    else:
        list_height = max(1, height - 3)
        row_start = 1
        text_col = 0
        text_width = max(1, width - 1)

    stdscr.bkgd(" ", curses.color_pair(4))

    title = f" JN-80 Librarian | {state.cwd} "
    stdscr.attron(curses.color_pair(3))
    stdscr.addnstr(0, 0, title.ljust(width), width - 1)
    stdscr.attroff(curses.color_pair(3))

    if has_box:
        horiz = "─" * max(0, pane_right - pane_left - 1)
        stdscr.attron(curses.color_pair(4))
        stdscr.addstr(pane_top, pane_left, "┌")
        stdscr.addstr(pane_top, pane_left + 1, horiz)
        stdscr.addstr(pane_top, pane_right, "┐")
        for y in range(pane_top + 1, pane_bottom):
            stdscr.addstr(y, pane_left, "│")
            stdscr.addstr(y, pane_right, "│")
        stdscr.addstr(pane_bottom, pane_left, "└")
        stdscr.addstr(pane_bottom, pane_left + 1, horiz)
        stdscr.addstr(pane_bottom, pane_right, "┘")
        stdscr.attroff(curses.color_pair(4))

    if state.cursor < state.scroll:
        state.scroll = state.cursor
    if state.cursor >= state.scroll + list_height:
        state.scroll = state.cursor - list_height + 1

    start = state.scroll
    end = min(len(state.entries), start + list_height)
    for row, idx in enumerate(range(start, end), start=row_start):
        entry = state.entries[idx]
        marker = "/" if entry.is_dir else ("*" if entry.path in state.selection_order else "")
        name = entry.name + ("/" if entry.is_dir and entry.name != ".." else "")
        line = f" {marker}{name}"
        if idx == state.cursor:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addnstr(row, text_col, line.ljust(text_width), text_width)
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
        elif entry.path in state.selection_order:
            stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
            stdscr.addnstr(row, text_col, line.ljust(text_width), text_width)
            stdscr.attroff(curses.color_pair(6) | curses.A_BOLD)
        else:
            stdscr.attron(curses.color_pair(4))
            stdscr.addnstr(row, text_col, line.ljust(text_width), text_width)
            stdscr.attroff(curses.color_pair(4))

    usable_width = max(1, width - 1)
    segments = [
        ("F5", "Send"),
        ("F6", "Next"),
        ("F7", "Receive"),
        ("F9/M", "Port"),
        ("F10", "Quit"),
        ("?", "Help"),
    ]
    seg_count = len(segments)
    base = usable_width // seg_count
    remainder = usable_width % seg_count

    col = 0
    for idx, (key_label, action_label) in enumerate(segments):
        seg_width = base + (1 if idx < remainder else 0)
        if seg_width <= 0:
            continue

        # Paint the whole segment with action background first.
        stdscr.attron(curses.color_pair(5))
        stdscr.addnstr(height - 2, col, " " * seg_width, seg_width)
        stdscr.attroff(curses.color_pair(5))

        # Key block (blue) with white token, including its trailing space.
        key_block = f" {key_label}"
        key_width = min(seg_width, max(4, len(key_block) + 1))
        stdscr.attron(curses.color_pair(7) | curses.A_BOLD)
        stdscr.addnstr(height - 2, col, " " * key_width, key_width)
        stdscr.addnstr(height - 2, col + 1, key_label, max(0, key_width - 1))
        stdscr.attroff(curses.color_pair(7) | curses.A_BOLD)

        action_room = max(0, seg_width - key_width)
        if action_room > 0:
            action_text = (" " + action_label + " ")[:action_room]
            stdscr.attron(curses.color_pair(5))
            stdscr.addnstr(height - 2, col + key_width, action_text, action_room)
            stdscr.attroff(curses.color_pair(5))

        col += seg_width

    port = state.selected_port or "<none>"
    last = (
        f"{state.last_written.bank}{state.last_written.slot:02d}"
        if state.last_written
        else "--"
    )
    status = (
        f" Port: {port} | Last: {last} | Selected: {state.selected_count} | {state.status}"
    )
    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
    stdscr.addnstr(height - 1, 0, status.ljust(width), width - 1)
    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)

    stdscr.refresh()


def _prompt_text(stdscr: curses.window, title: str, initial: str) -> Optional[str]:
    height, width = stdscr.getmaxyx()
    win_h = 7
    win_w = min(60, max(40, width - 4))
    y = (height - win_h) // 2
    x = (width - win_w) // 2
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    text = initial
    pos = len(text)

    prev_cursor_state: Optional[int] = None
    try:
        prev_cursor_state = curses.curs_set(1)
    except curses.error:
        prev_cursor_state = None

    try:
        while True:
            win.erase()
            win.box()
            win.attron(curses.A_BOLD)
            win.addnstr(1, 2, title, win_w - 4)
            win.attroff(curses.A_BOLD)
            hint = "Enter=Confirm  Esc=Cancel"
            win.addnstr(2, 2, hint, win_w - 4)
            win.addnstr(4, 2, text, win_w - 4)
            win.move(4, min(win_w - 3, 2 + pos))
            win.refresh()

            ch = win.getch()
            if ch in (10, 13):
                return text.strip()
            if ch == 27:
                return None
            if ch in (curses.KEY_BACKSPACE, 127):
                if pos > 0:
                    text = text[: pos - 1] + text[pos:]
                    pos -= 1
                continue
            if ch == curses.KEY_LEFT:
                pos = max(0, pos - 1)
                continue
            if ch == curses.KEY_RIGHT:
                pos = min(len(text), pos + 1)
                continue
            if 32 <= ch <= 126:
                text = text[:pos] + chr(ch) + text[pos:]
                pos += 1
    finally:
        try:
            curses.curs_set(0 if prev_cursor_state is None else prev_cursor_state)
        except curses.error:
            pass


def _prompt_bank_slot(stdscr: Optional[curses.window], initial: str) -> Optional[WritePosition]:
    if stdscr is None:
        try:
            return _parse_bank_slot(initial)
        except ValueError:
            return WritePosition("A", 1)

    try:
        starting = _parse_bank_slot(initial)
    except ValueError:
        starting = WritePosition("A", 1)

    height, width = stdscr.getmaxyx()
    win_h = 8
    win_w = min(60, max(44, width - 4))
    y = (height - win_h) // 2
    x = (width - win_w) // 2
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    bank = starting.bank
    slot_text = f"{starting.slot:02d}"
    last_digit_field = getattr(_prompt_bank_slot, "_last_digit_field", 1)
    if last_digit_field not in (1, 2):
        last_digit_field = 1
    field = 0

    prev_cursor_state: Optional[int] = None
    try:
        prev_cursor_state = curses.curs_set(1)
    except curses.error:
        prev_cursor_state = None

    def _move_cursor() -> None:
        bank_col = 8
        tens_col = 17
        ones_col = 18
        if field == 0:
            win.move(4, bank_col)
        elif field == 1:
            win.move(4, tens_col)
        else:
            win.move(4, ones_col)

    def _slot_digits() -> tuple[str, str]:
        return slot_text[0], slot_text[1]

    try:
        while True:
            win.erase()
            win.box()
            win.attron(curses.A_BOLD)
            win.addnstr(1, 2, "Send from bank+slot", win_w - 4)
            win.attroff(curses.A_BOLD)
            win.addnstr(2, 2, "Enter=Confirm  Esc=Cancel  Left/Right=Field  Up/Down=Adjust", win_w - 4)

            tens, ones = _slot_digits()
            bank_attr = curses.A_REVERSE if field == 0 else curses.A_NORMAL
            tens_attr = curses.A_REVERSE if field == 1 else curses.A_NORMAL
            ones_attr = curses.A_REVERSE if field == 2 else curses.A_NORMAL
            win.addnstr(4, 2, "Bank:", win_w - 4)
            win.attron(bank_attr)
            win.addnstr(4, 8, bank, 1)
            win.attroff(bank_attr)
            win.addnstr(4, 11, "Slot:", win_w - 13)
            win.attron(tens_attr)
            win.addnstr(4, 17, tens, 1)
            win.attroff(tens_attr)
            win.attron(ones_attr)
            win.addnstr(4, 18, ones, 1)
            win.attroff(ones_attr)
            win.addnstr(5, 2, "Valid: bank A-T, slot 01-20", win_w - 4)

            _move_cursor()
            win.refresh()

            ch = win.getch()
            if ch in (10, 13):
                slot_value = int(slot_text)
                if 1 <= slot_value <= 20:
                    _prompt_bank_slot._last_digit_field = field if field in (1, 2) else last_digit_field
                    return WritePosition(bank, slot_value)
                curses.beep()
                continue
            if ch == 27:
                _prompt_bank_slot._last_digit_field = field if field in (1, 2) else last_digit_field
                return None
            if ch == 9:
                if field == 0:
                    field = last_digit_field
                else:
                    field = 0
                continue
            if ch == curses.KEY_BTAB:
                if field == 0:
                    field = last_digit_field
                else:
                    field = 0
                continue
            if ch == curses.KEY_LEFT:
                field = max(0, field - 1)
                if field in (1, 2):
                    last_digit_field = field
                continue
            if ch == curses.KEY_RIGHT:
                field = min(2, field + 1)
                if field in (1, 2):
                    last_digit_field = field
                continue
            if ch == curses.KEY_UP:
                if field == 0:
                    idx = BANKS.index(bank)
                    bank = BANKS[(idx + 1) % len(BANKS)]
                else:
                    slot_value = int(slot_text)
                    if 1 <= slot_value < 20:
                        slot_value += 1
                    elif slot_value == 20:
                        slot_value = 1
                    else:
                        slot_value = 1
                    slot_text = f"{slot_value:02d}"
                continue
            if ch == curses.KEY_DOWN:
                if field == 0:
                    idx = BANKS.index(bank)
                    bank = BANKS[(idx - 1) % len(BANKS)]
                else:
                    slot_value = int(slot_text)
                    if 1 < slot_value <= 20:
                        slot_value -= 1
                    elif slot_value == 1:
                        slot_value = 20
                    else:
                        slot_value = 20
                    slot_text = f"{slot_value:02d}"
                continue

            if not (32 <= ch <= 126):
                continue

            char = chr(ch).upper()
            if field == 0:
                if char in BANKS:
                    bank = char
                    field = last_digit_field
                continue

            if not char.isdigit():
                continue

            tens, ones = _slot_digits()
            if field == 1:
                new_tens = int(char)
                if new_tens > 2:
                    continue
                slot_text = f"{new_tens}{ones}"
                field = 2
                last_digit_field = field
                continue

            slot_text = f"{tens}{char}"
    finally:
        try:
            curses.curs_set(0 if prev_cursor_state is None else prev_cursor_state)
        except curses.error:
            pass


def _select_from_menu(stdscr: curses.window, title: str, options: list[str], current: Optional[str]) -> Optional[str]:
    if not options:
        return None

    height, width = stdscr.getmaxyx()
    win_h = min(max(8, len(options) + 4), height - 2)
    win_w = min(80, max(50, width - 4))
    y = (height - win_h) // 2
    x = (width - win_w) // 2
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    idx = 0
    if current in options:
        idx = options.index(current)

    scroll = 0
    visible = win_h - 4

    while True:
        win.erase()
        win.box()
        win.attron(curses.A_BOLD)
        win.addnstr(1, 2, title, win_w - 4)
        win.attroff(curses.A_BOLD)

        if idx < scroll:
            scroll = idx
        if idx >= scroll + visible:
            scroll = idx - visible + 1

        for row, item_index in enumerate(range(scroll, min(scroll + visible, len(options))), start=2):
            value = options[item_index]
            if item_index == idx:
                win.attron(curses.color_pair(1) | curses.A_BOLD)
                win.addnstr(row, 2, value.ljust(win_w - 4), win_w - 4)
                win.attroff(curses.color_pair(1) | curses.A_BOLD)
            else:
                win.addnstr(row, 2, value.ljust(win_w - 4), win_w - 4)

        win.refresh()
        ch = win.getch()
        if ch == curses.KEY_UP:
            idx = max(0, idx - 1)
        elif ch == curses.KEY_DOWN:
            idx = min(len(options) - 1, idx + 1)
        elif ch in (10, 13):
            return options[idx]
        elif ch == 27:
            return None


def _show_help_modal(stdscr: curses.window) -> None:
    lines = [
        "JN-80 Librarian Keys",
        "",
        "Up/Down      Move cursor",
        "Enter        Open directory",
        "Ctrl-T       Toggle selection + move down",
        "F9 or M      Select MIDI output port",
        "F5           Send with bank/slot prompt",
        "F6           Send to next position",
        "F7 or R      Receive SysEx from synth",
        "?            Show this help",
        "Q or F10     Quit",
        "",
        "Press any key to close",
    ]

    height, width = stdscr.getmaxyx()
    win_h = min(height - 2, len(lines) + 4)
    content_width = max(len(line) for line in lines)
    win_w = min(width - 2, max(52, content_width + 4))
    y = max(0, (height - win_h) // 2)
    x = max(0, (width - win_w) // 2)
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    win.erase()
    win.box()
    for idx, line in enumerate(lines, start=1):
        if idx >= win_h - 1:
            break
        attr = curses.A_BOLD if idx == 1 else curses.A_NORMAL
        win.attron(attr)
        win.addnstr(idx, 2, line, win_w - 4)
        win.attroff(attr)
    win.refresh()
    win.getch()


def _show_message_modal(stdscr: Optional[curses.window], title: str, message: str) -> None:
    if stdscr is None:
        return

    body_lines = [line for line in message.split("\n") if line.strip()]
    if not body_lines:
        body_lines = [message]

    lines = [title, ""] + body_lines + ["", "Press any key to close"]
    height, width = stdscr.getmaxyx()
    win_h = min(height - 2, len(lines) + 4)
    content_width = max(len(line) for line in lines)
    win_w = min(width - 2, max(54, content_width + 4))
    y = max(0, (height - win_h) // 2)
    x = max(0, (width - win_w) // 2)
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    win.erase()
    win.box()
    for idx, line in enumerate(lines, start=1):
        if idx >= win_h - 1:
            break
        attr = curses.A_BOLD if idx == 1 else curses.A_NORMAL
        win.attron(attr)
        win.addnstr(idx, 2, line, win_w - 4)
        win.attroff(attr)
    win.refresh()
    win.getch()


def _parse_bank_slot(input_text: str) -> WritePosition:
    text = input_text.strip().upper().replace(" ", "")
    if not text:
        raise ValueError("Empty input")
    if text[0] not in BANKS:
        raise ValueError("Bank must be A-T")
    try:
        slot = int(text[1:])
    except ValueError as exc:
        raise ValueError("Slot must be numeric") from exc
    position = WritePosition(text[0], slot)
    position.validate()
    return position


def _default_receive_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"jn80_dump_{stamp}.syx"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _target_files(state: AppState) -> list[Path]:
    if state.selection_order:
        return [
            path
            for path, _ in sorted(state.selection_order.items(), key=lambda item: item[1])
            if path.exists() and path.is_file()
        ]

    entry = _current_entry(state)
    if not entry or entry.is_dir:
        return []
    return [entry.path]


def _send_files(state: AppState, start_position: WritePosition) -> tuple[bool, str, Optional[WritePosition]]:
    files = _target_files(state)
    if not files:
        return False, "No .syx file selected", None

    current = start_position
    last_success: Optional[WritePosition] = None
    ack_count = 0
    last_ack_message: Optional[str] = None

    for file_path in files:
        try:
            raw = load_syx_file(file_path)
            parsed = parse_sysex_message(raw)
            patched = patch_bank_slot_in_memory(parsed, current)
            sysex_payload = to_mido_sysex_data(patched)
        except OSError as exc:
            return False, f"File error: {exc}", last_success
        except ValueError as exc:
            return False, str(exc), last_success

        result = send_sysex(state.selected_port, sysex_payload)
        if not result.ok:
            return False, result.message, last_success

        if result.ack_received:
            ack_count += 1
            last_ack_message = result.ack_message

        last_success = current
        current = increment_position(current)

    if len(files) == 1 and last_success is not None:
        base = f"Sent {files[0].name} to {last_success.bank}{last_success.slot:02d}"
        if ack_count:
            return True, f"{base} | ACK received", last_success
        return True, f"{base} | No ACK", last_success
    if last_success is not None:
        base = f"Sent {len(files)} files, last {last_success.bank}{last_success.slot:02d}"
        if ack_count:
            ack_suffix = f" | ACK {ack_count}/{len(files)}"
            if ack_count == len(files) and last_ack_message and "JN-80 reply" in last_ack_message:
                ack_suffix += " (JN-80 confirmed)"
            return True, f"{base}{ack_suffix}", last_success
        return True, f"{base} | No ACK", last_success
    return False, "Send failed", None


def _toggle_selection(state: AppState) -> None:
    entry = _current_entry(state)
    if not entry or entry.is_dir:
        state.status = "Selection works on .syx files only"
        return

    if entry.path in state.selection_order:
        del state.selection_order[entry.path]
        state.status = f"Unselected {entry.name}"
    else:
        state.select_counter += 1
        state.selection_order[entry.path] = state.select_counter
        state.status = f"Selected {entry.name}"

    if state.entries:
        state.cursor = min(len(state.entries) - 1, state.cursor + 1)


def _open_entry(state: AppState) -> None:
    entry = _current_entry(state)
    if not entry:
        return
    if not entry.is_dir:
        state.status = "Enter opens directories"
        return

    previous_cwd = state.cwd
    state.cwd = entry.path
    state.cursor = 0
    state.scroll = 0
    _refresh_entries(state)

    # If user navigated to parent via '..', focus the directory we came from.
    if entry.name == "..":
        for idx, candidate in enumerate(state.entries):
            if candidate.path == previous_cwd:
                state.cursor = idx
                break


def _choose_midi_port(stdscr: curses.window, state: AppState) -> None:
    try:
        ports = list_output_ports()
    except RuntimeError as exc:
        state.status = str(exc)
        return

    if not ports:
        state.status = "No MIDI output ports found"
        return

    picked = _select_from_menu(stdscr, "Select MIDI Output Port", ports, state.selected_port)
    if picked is None:
        state.status = "MIDI selection canceled"
        return

    state.selected_port = picked
    state.status = f"MIDI port selected: {picked}"


def _handle_f5(stdscr: curses.window, state: AppState) -> None:
    prefill = "A1"
    if state.last_written:
        prefill = f"{state.last_written.bank}{state.last_written.slot}"

    start_position = _prompt_bank_slot(stdscr, prefill)
    if start_position is None:
        state.status = "Send canceled"
        return

    ok, message, last = _send_files(state, start_position)
    state.status = message
    if ok and last is not None:
        state.last_written = last
        state.selection_order.clear()

    _show_message_modal(stdscr, "Send Result" if ok else "Send Error", message)


def _handle_f6(stdscr: Optional[curses.window], state: AppState) -> None:
    if state.last_written is None:
        start = WritePosition("A", 1)
    else:
        start = increment_position(state.last_written)

    ok, message, last = _send_files(state, start)
    state.status = message
    if ok and last is not None:
        state.last_written = last
        state.selection_order.clear()

    _show_message_modal(stdscr, "Send Result" if ok else "Send Error", message)


def _handle_receive(stdscr: Optional[curses.window], state: AppState) -> None:
    modal: Optional[curses.window] = None
    modal_w = 0
    modal_h = 0

    def _render_modal(title: str, lines: list[str], footer: str = "") -> None:
        if modal is None:
            return
        modal.erase()
        modal.box()
        modal.attron(curses.A_BOLD)
        modal.addnstr(1, 2, title, modal_w - 4)
        modal.attroff(curses.A_BOLD)
        max_rows = modal_h - 4
        for idx, line in enumerate(lines[:max_rows], start=2):
            modal.addnstr(idx, 2, line.ljust(modal_w - 4), modal_w - 4)
        if footer:
            modal.addnstr(modal_h - 2, 2, footer, modal_w - 4)
        modal.refresh()

    def _wait_any_key() -> None:
        if modal is None:
            return
        modal.timeout(-1)
        modal.getch()

    initial = _default_receive_filename()
    if stdscr is None:
        text: Optional[str] = initial
    else:
        height, width = stdscr.getmaxyx()
        modal_h = min(12, max(9, height - 2))
        modal_w = min(78, max(56, width - 4))
        y = (height - modal_h) // 2
        x = (width - modal_w) // 2
        modal = curses.newwin(modal_h, modal_w, y, x)
        modal.keypad(True)

        text_value = initial
        pos = len(text_value)
        prev_cursor_state: Optional[int] = None
        try:
            prev_cursor_state = curses.curs_set(1)
        except curses.error:
            prev_cursor_state = None

        text = None
        try:
            while True:
                _render_modal(
                    "Receive SysEx: filename",
                    ["Enter=Start  Esc=Cancel", "", text_value],
                )
                modal.move(4, min(modal_w - 3, 2 + pos))
                ch = modal.getch()
                if ch in (10, 13):
                    text = text_value.strip()
                    break
                if ch == 27:
                    state.status = "Receive canceled"
                    return
                if ch in (curses.KEY_BACKSPACE, 127, 8):
                    if pos > 0:
                        text_value = text_value[: pos - 1] + text_value[pos:]
                        pos -= 1
                    continue
                if ch == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                    continue
                if ch == curses.KEY_RIGHT:
                    pos = min(len(text_value), pos + 1)
                    continue
                if 32 <= ch <= 126:
                    text_value = text_value[:pos] + chr(ch) + text_value[pos:]
                    pos += 1
        finally:
            try:
                curses.curs_set(0 if prev_cursor_state is None else prev_cursor_state)
            except curses.error:
                pass

    if text is None:
        state.status = "Receive canceled"
        return

    filename = text.strip()
    if not filename:
        state.status = "Filename cannot be empty"
        if stdscr and modal is not None:
            _render_modal("Receive Error", [state.status], "Press any key")
            _wait_any_key()
        return
    if not filename.lower().endswith(".syx"):
        filename += ".syx"

    target = _unique_path(state.cwd / filename)

    progress_received = 0
    progress_jn80 = 0
    state.status = "Waiting for SysEx dump..."
    if stdscr and modal is not None:
        modal.timeout(0)
        _render_modal(
            "Receive SysEx",
            [
                f"Port: {state.selected_port or '<none>'}",
                "Waiting for first SysEx frame...",
                "Patches: 0  JN-80: 0",
                "",
                "Press SHIFT+INITIAL/WRITE",
                "Select Dump Presets",
            ],
            "Any key=Cancel",
        )

    def _receive_cancel_requested() -> bool:
        if modal is None:
            return False
        ch = modal.getch()
        if ch in (-1, curses.KEY_RESIZE):
            return False
        return True

    def _on_receive_progress(received_count: int, jn80_count: int) -> None:
        nonlocal progress_received, progress_jn80
        progress_received = received_count
        progress_jn80 = jn80_count
        state.status = f"Receiving... patches: {received_count} (JN-80: {jn80_count})"
        if stdscr and modal is not None:
            _render_modal(
                "Receive SysEx",
                [
                    f"Port: {state.selected_port or '<none>'}",
                    "Receiving burst...",
                    f"Patches: {received_count}  JN-80: {jn80_count}",
                ],
                "Any key=Cancel  Waiting for idle gap",
            )

    result = receive_sysex(
        state.selected_port,
        timeout_sec=45.0,
        inter_message_timeout_sec=1.2,
        on_progress=_on_receive_progress,
        should_cancel=_receive_cancel_requested,
    )
    captured = result.sysex_messages or ([result.sysex_bytes] if result.sysex_bytes is not None else [])
    if not result.ok or not captured:
        state.status = result.message
        if stdscr and modal is not None:
            _render_modal(
                "Receive Error",
                [
                    f"Port: {state.selected_port or '<none>'}",
                    f"Patches: {progress_received}  JN-80: {progress_jn80}",
                    result.message,
                ],
                "Press any key",
            )
            _wait_any_key()
        return

    saved_names: list[str] = []
    try:
        combined = b"".join(captured)
        target.write_bytes(combined)
        saved_names.append(target.name)
    except OSError as exc:
        state.status = f"Failed to save dump: {exc}"
        if stdscr and modal is not None:
            _render_modal("Receive Error", [state.status], "Press any key")
            _wait_any_key()
        return

    _refresh_entries(state)
    received_count = result.received_count or len(captured)
    jn80_count = result.jn80_count

    saved_summary = saved_names[0]

    details = [
        f"Port: {state.selected_port or '<none>'}",
        f"Received patches: {received_count}",
        f"JN-80 frames: {jn80_count}",
        f"Saved files: {len(saved_names)}",
        f"Saved as: {saved_summary}",
        result.message,
    ]
    message = "\n".join(details)
    state.status = f"Receive OK: {received_count} patch(es), saved {len(saved_names)}"
    if stdscr and modal is not None:
        _render_modal(
            "Receive Result",
            [
                f"Port: {state.selected_port or '<none>'}",
                f"Received patches: {received_count}",
                f"JN-80 frames: {jn80_count}",
                f"Saved files: {len(saved_names)}",
                f"Saved as: {saved_summary}",
                result.message,
            ],
            "Press any key",
        )
        _wait_any_key()


def _persist(state: AppState) -> None:
    save_config(
        AppConfig(
            last_midi_port=state.selected_port,
            last_write=state.last_written,
            last_browsed_dir=str(state.cwd),
        )
    )


def _app_loop(stdscr: curses.window, state: AppState) -> None:
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.start_color()
    curses.use_default_colors()

    fkey_bg_color = curses.COLOR_BLUE
    if curses.can_change_color() and getattr(curses, "COLORS", 0) > 16:
        try:
            dark_blue_color = 16
            curses.init_color(dark_blue_color, 0, 0, 300)
            fkey_bg_color = dark_blue_color
        except curses.error:
            fkey_bg_color = curses.COLOR_BLUE

    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(3, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(4, curses.COLOR_YELLOW, curses.COLOR_BLUE)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLUE)
    curses.init_pair(7, curses.COLOR_WHITE, fkey_bg_color)

    while True:
        _refresh_entries(state)
        _draw(stdscr, state)
        ch = stdscr.getch()

        if ch in (ord("q"), ord("Q"), curses.KEY_F10):
            state.status = "Exiting"
            _persist(state)
            return
        if ch == curses.KEY_UP:
            state.cursor = max(0, state.cursor - 1)
            continue
        if ch == curses.KEY_DOWN:
            if state.entries:
                state.cursor = min(len(state.entries) - 1, state.cursor + 1)
            continue
        if ch == curses.KEY_RESIZE:
            # Resize events are expected; loop redraw will adapt layout.
            continue
        if ch in (10, 13):
            _open_entry(state)
            _persist(state)
            continue
        if ch == 20:  # Ctrl-T
            _toggle_selection(state)
            continue
        if ch in (curses.KEY_F9, ord("m"), ord("M")):
            _choose_midi_port(stdscr, state)
            _persist(state)
            continue
        if ch == curses.KEY_F5:
            _handle_f5(stdscr, state)
            _persist(state)
            continue
        if ch == curses.KEY_F6:
            _handle_f6(stdscr, state)
            _persist(state)
            continue
        if ch in (curses.KEY_F7, ord("r"), ord("R")):
            _handle_receive(stdscr, state)
            _persist(state)
            continue
        if ch == ord("?"):
            _show_help_modal(stdscr)
            state.status = "Help"
            continue
        try:
            key_name = curses.keyname(ch).decode("ascii", errors="ignore")
        except Exception:
            key_name = str(ch)
        state.status = f"Key not mapped: {key_name} (press ? for help)"


def run() -> None:
    cfg = load_config()
    state = AppState(
        cwd=_safe_cwd(cfg),
        selected_port=cfg.last_midi_port,
        last_written=cfg.last_write,
    )
    curses.wrapper(_app_loop, state)
