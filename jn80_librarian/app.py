from __future__ import annotations

import curses
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .browser import BrowserEntry, list_entries
from .config import AppConfig, load_config, save_config
from .midi import list_output_ports, receive_sysex, send_sysex
from .position import BANKS, WritePosition, increment_position
from .sysex import HEADER_PREFIX, load_syx_file, parse_sysex_message, patch_bank_slot_in_memory, to_mido_sysex_data


@dataclass
class AppState:
    cwd: Path
    entries: list[BrowserEntry] = field(default_factory=list)
    cursor: int = 0
    scroll: int = 0
    status: str = "Ready"
    selected_port: Optional[str] = None
    last_written: Optional[WritePosition] = None
    last_f5_target: WritePosition = field(default_factory=lambda: WritePosition("A", 1))
    last_init_from: WritePosition = field(default_factory=lambda: WritePosition("A", 1))
    last_init_to: WritePosition = field(default_factory=lambda: WritePosition("A", 1))
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


def _visible_list_height(stdscr: curses.window) -> int:
    height, width = stdscr.getmaxyx()
    has_box = height >= 7 and width >= 10
    if has_box:
        pane_top = 1
        pane_bottom = height - 3
        return max(1, pane_bottom - pane_top - 1)
    return max(1, height - 3)


def _move_cursor_page(state: AppState, page_size: int, direction: int) -> None:
    if not state.entries:
        return

    step = max(1, page_size)
    delta = step if direction > 0 else -step
    state.cursor = max(0, min(len(state.entries) - 1, state.cursor + delta))


def _page_down_pin_top(state: AppState, page_size: int) -> None:
    _move_cursor_page(state, page_size, 1)
    if state.entries:
        max_scroll = max(0, len(state.entries) - max(1, page_size))
        state.scroll = min(state.cursor, max_scroll)


def _format_header_title(cwd: Path, draw_width: int) -> str:
    if draw_width <= 0:
        return ""

    prefix = " JN-80 Librarian | "
    suffix = " "
    cwd_text = str(cwd)
    available = draw_width - len(prefix) - len(suffix)
    if available <= 0:
        return prefix.strip()

    if len(cwd_text) > available:
        if available <= 3:
            cwd_text = cwd_text[-available:]
        else:
            cwd_text = "..." + cwd_text[-(available - 3):]

    return f"{prefix}{cwd_text}{suffix}"


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

    draw_width = max(1, width - 1)
    title = _format_header_title(state.cwd, draw_width)
    stdscr.attron(curses.color_pair(3))
    stdscr.addnstr(0, 0, title.ljust(draw_width), draw_width)
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
        ("F2", "Init"),
        ("F5", "Send"),
        ("F6", "Next"),
        ("F7", "Receive"),
        ("F8", "Delete"),
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
            win.addnstr(2, 2, "Enter=Confirm  Esc=Cancel  Tab/Shift-Tab=Field  Up/Down=Adjust", win_w - 4)

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


def _prompt_yes_no(stdscr: Optional[curses.window], title: str, message: str) -> bool:
    if stdscr is None:
        return False

    lines = [title, "", message, "", "Enter/Y=Yes  Esc/N=No"]
    height, width = stdscr.getmaxyx()
    win_h = min(height - 2, len(lines) + 4)
    content_width = max(len(line) for line in lines)
    win_w = min(width - 2, max(64, content_width + 4))
    y = max(0, (height - win_h) // 2)
    x = max(0, (width - win_w) // 2)
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    while True:
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

        ch = win.getch()
        if ch in (10, 13, ord("y"), ord("Y")):
            return True
        if ch in (27, ord("n"), ord("N")):
            return False


def _truncate_line(line: str, max_width: int) -> str:
    """Shorten a line to max_width, preserving the tail for path-like content."""
    if max_width <= 0:
        return ""
    if len(line) <= max_width:
        return line
    # If the line has a path separator, keep the tail so the filename stays visible.
    if "/" in line or "\\" in line:
        colon_pos = line.find(": ")
        if colon_pos != -1:
            prefix = line[: colon_pos + 2]  # e.g. "Saved as: "
            rest = line[colon_pos + 2 :]
            rest_budget = max_width - len(prefix)
            if rest_budget > 3:
                return prefix + "..." + rest[-(rest_budget - 3) :]
            return (prefix + rest)[: max_width]
        return "..." + line[-(max_width - 3) :]
    return line[: max_width]


def _position_label(position: WritePosition) -> str:
    return f"{position.bank}{position.slot:02d}"


def _position_ordinal(position: WritePosition) -> int:
    return (position.bank_index * 20) + position.slot_index


def _positions_inclusive(start: WritePosition, end: WritePosition) -> list[WritePosition]:
    start_ordinal = _position_ordinal(start)
    end_ordinal = _position_ordinal(end)
    if end_ordinal < start_ordinal:
        return []

    positions: list[WritePosition] = []
    for absolute in range(start_ordinal, end_ordinal + 1):
        bank_index = absolute // 20
        slot_index = absolute % 20
        positions.append(WritePosition(BANKS[bank_index], slot_index + 1))
    return positions


def _prompt_init_range(
    stdscr: Optional[curses.window],
    from_initial: str,
    to_initial: str,
) -> Optional[tuple[WritePosition, WritePosition]]:
    if stdscr is None:
        try:
            return _parse_bank_slot(from_initial), _parse_bank_slot(to_initial)
        except ValueError:
            return WritePosition("A", 1), WritePosition("A", 1)

    try:
        from_pos = _parse_bank_slot(from_initial)
    except ValueError:
        from_pos = WritePosition("A", 1)
    try:
        to_pos = _parse_bank_slot(to_initial)
    except ValueError:
        to_pos = from_pos

    height, width = stdscr.getmaxyx()
    win_h = 10
    win_w = min(72, max(54, width - 4))
    y = (height - win_h) // 2
    x = (width - win_w) // 2
    win = curses.newwin(win_h, win_w, y, x)
    win.keypad(True)

    from_bank = from_pos.bank
    from_slot = f"{from_pos.slot:02d}"
    to_bank = to_pos.bank
    to_slot = f"{to_pos.slot:02d}"
    last_from_digit_field = getattr(_prompt_init_range, "_last_from_digit_field", 1)
    if last_from_digit_field not in (1, 2):
        last_from_digit_field = 1
    last_to_digit_field = getattr(_prompt_init_range, "_last_to_digit_field", 4)
    if last_to_digit_field not in (4, 5):
        last_to_digit_field = 4
    # Always start with the first bank field selected when opening.
    field = 0

    prev_cursor_state: Optional[int] = None
    try:
        prev_cursor_state = curses.curs_set(1)
    except curses.error:
        prev_cursor_state = None

    def _render_slot(value: str, active_tens: bool, active_ones: bool, row: int, col: int) -> None:
        win.addnstr(row, col - 6, "Slot:", 5)
        win.attron(curses.A_REVERSE if active_tens else curses.A_NORMAL)
        win.addnstr(row, col, value[0], 1)
        win.attroff(curses.A_REVERSE if active_tens else curses.A_NORMAL)
        win.attron(curses.A_REVERSE if active_ones else curses.A_NORMAL)
        win.addnstr(row, col + 1, value[1], 1)
        win.attroff(curses.A_REVERSE if active_ones else curses.A_NORMAL)

    def _move_cursor() -> None:
        col_map = {0: 14, 1: 24, 2: 25, 3: 14, 4: 24, 5: 25}
        row = 4 if field < 3 else 6
        win.move(row, col_map[field])

    def _adjust_slot(slot_text: str, delta: int) -> str:
        slot_value = int(slot_text)
        slot_value += delta
        if slot_value > 20:
            slot_value = 1
        if slot_value < 1:
            slot_value = 20
        return f"{slot_value:02d}"

    try:
        while True:
            win.erase()
            win.box()
            win.attron(curses.A_BOLD)
            win.addnstr(1, 2, "INIT range", win_w - 4)
            win.attroff(curses.A_BOLD)
            win.addnstr(2, 2, "Enter=Confirm  Esc=Cancel  Tab/Shift-Tab=Field  Up/Down=Adjust", win_w - 4)

            win.addnstr(4, 2, "From Bank:", win_w - 4)
            win.attron(curses.A_REVERSE if field == 0 else curses.A_NORMAL)
            win.addnstr(4, 14, from_bank, 1)
            win.attroff(curses.A_REVERSE if field == 0 else curses.A_NORMAL)
            _render_slot(from_slot, field == 1, field == 2, 4, 24)

            win.addnstr(6, 2, "To   Bank:", win_w - 4)
            win.attron(curses.A_REVERSE if field == 3 else curses.A_NORMAL)
            win.addnstr(6, 14, to_bank, 1)
            win.attroff(curses.A_REVERSE if field == 3 else curses.A_NORMAL)
            _render_slot(to_slot, field == 4, field == 5, 6, 24)

            win.addnstr(7, 2, "Valid: bank A-T, slot 01-20", win_w - 4)
            _move_cursor()
            win.refresh()

            ch = win.getch()
            if ch in (10, 13):
                _prompt_init_range._last_from_digit_field = last_from_digit_field
                _prompt_init_range._last_to_digit_field = last_to_digit_field
                from_value = WritePosition(from_bank, int(from_slot))
                to_value = WritePosition(to_bank, int(to_slot))
                return from_value, to_value
            if ch == 27:
                _prompt_init_range._last_from_digit_field = last_from_digit_field
                _prompt_init_range._last_to_digit_field = last_to_digit_field
                return None
            if ch == curses.KEY_LEFT:
                field = max(0, field - 1)
                if field in (1, 2):
                    last_from_digit_field = field
                elif field in (4, 5):
                    last_to_digit_field = field
                continue
            if ch == curses.KEY_RIGHT:
                field = min(5, field + 1)
                if field in (1, 2):
                    last_from_digit_field = field
                elif field in (4, 5):
                    last_to_digit_field = field
                continue
            if ch == 9:
                if field == 0:
                    field = last_from_digit_field
                elif field in (1, 2):
                    field = 3
                elif field == 3:
                    field = last_to_digit_field
                else:
                    field = 0
                continue
            if ch == curses.KEY_BTAB:
                if field == 0:
                    field = last_to_digit_field
                elif field in (1, 2):
                    field = 0
                elif field == 3:
                    field = last_from_digit_field
                else:
                    field = 3
                continue
            if ch == curses.KEY_UP:
                if field == 0:
                    idx = BANKS.index(from_bank)
                    from_bank = BANKS[(idx + 1) % len(BANKS)]
                elif field in (1, 2):
                    from_slot = _adjust_slot(from_slot, 1)
                elif field == 3:
                    idx = BANKS.index(to_bank)
                    to_bank = BANKS[(idx + 1) % len(BANKS)]
                else:
                    to_slot = _adjust_slot(to_slot, 1)
                continue
            if ch == curses.KEY_DOWN:
                if field == 0:
                    idx = BANKS.index(from_bank)
                    from_bank = BANKS[(idx - 1) % len(BANKS)]
                elif field in (1, 2):
                    from_slot = _adjust_slot(from_slot, -1)
                elif field == 3:
                    idx = BANKS.index(to_bank)
                    to_bank = BANKS[(idx - 1) % len(BANKS)]
                else:
                    to_slot = _adjust_slot(to_slot, -1)
                continue

            if not (32 <= ch <= 126):
                continue

            char = chr(ch).upper()
            if field in (0, 3):
                if char in BANKS:
                    if field == 0:
                        from_bank = char
                        field = last_from_digit_field
                    else:
                        to_bank = char
                        field = last_to_digit_field
                continue

            if not char.isdigit():
                continue

            if field in (1, 2):
                tens = from_slot[0]
                if field == 1:
                    new_tens = int(char)
                    if new_tens > 2:
                        continue
                    from_slot = f"{new_tens}{from_slot[1]}"
                    field = 2
                    last_from_digit_field = field
                else:
                    from_slot = f"{tens}{char}"
                    last_from_digit_field = field
                    field = 3
                continue

            tens = to_slot[0]
            if field == 4:
                new_tens = int(char)
                if new_tens > 2:
                    continue
                to_slot = f"{new_tens}{to_slot[1]}"
                field = 5
                last_to_digit_field = field
            else:
                to_slot = f"{tens}{char}"
                last_to_digit_field = field
                field = 0
    finally:
        try:
            curses.curs_set(0 if prev_cursor_state is None else prev_cursor_state)
        except curses.error:
            pass


def _show_help_modal(stdscr: curses.window) -> None:
    lines = [
        "JN-80 Librarian Keys",
        "",
        "Up/Down      Move cursor",
        "PgUp/PgDn    Move one page",
        "Enter        Open directory",
        "Ctrl-T       Toggle selection + move down",
        "F2           INIT (erase) range",
        "F5           Send with bank/slot prompt",
        "F6           Send to next position",
        "F7 or R      Receive SysEx from synth",
        "F8           Delete selected/highlighted file(s)",
        "F9 or M      Select MIDI output port",
        "F10          Quit",
        "?            Show this help",
        "Q            Quit",
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

    inner_w = win_w - 4
    win.erase()
    win.box()
    for idx, line in enumerate(lines, start=1):
        if idx >= win_h - 1:
            break
        attr = curses.A_BOLD if idx == 1 else curses.A_NORMAL
        win.attron(attr)
        win.addnstr(idx, 2, _truncate_line(line, inner_w), inner_w)
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


def _delete_files(state: AppState, files: list[Path]) -> tuple[bool, str]:
    deleted_count = 0
    errors: list[str] = []

    for file_path in files:
        try:
            file_path.unlink()
            deleted_count += 1
            state.selection_order.pop(file_path, None)
        except OSError as exc:
            errors.append(f"{file_path.name}: {exc}")

    if deleted_count == 0:
        detail = errors[0] if errors else "No files were deleted"
        return False, f"Delete failed: {detail}"

    if len(files) == 1:
        base = f"Deleted {files[0].name}"
    else:
        base = f"Deleted {deleted_count}/{len(files)} files"

    if errors:
        return False, f"{base} | Errors: {errors[0]}"
    return True, base


def _send_files(state: AppState, start_position: WritePosition) -> tuple[bool, str, Optional[WritePosition]]:
    files = _target_files(state)
    if not files:
        return False, "No .syx file selected", None

    return _send_files_with_progress(state, files, start_position)


def _send_files_with_progress(
    state: AppState,
    files: list[Path],
    start_position: WritePosition,
    on_progress: Optional[Callable[[int, int, Path, WritePosition], None]] = None,
) -> tuple[bool, str, Optional[WritePosition]]:
    if not files:
        return False, "No .syx file selected", None

    current = start_position
    last_success: Optional[WritePosition] = None
    ack_count = 0
    last_ack_message: Optional[str] = None

    total_files = len(files)
    for index, file_path in enumerate(files, start=1):
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
        if on_progress is not None:
            on_progress(index, total_files, file_path, current)
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


def _send_init_range(
    state: AppState,
    start_position: WritePosition,
    end_position: WritePosition,
    on_progress: Optional[Callable[[int, int, WritePosition], None]] = None,
) -> tuple[bool, str, Optional[WritePosition]]:
    positions = _positions_inclusive(start_position, end_position)
    if not positions:
        return False, "Invalid range: FROM must be before or equal to TO", None

    # Empty JN-80 patch payload with blank data (including unnamed patch text).
    empty_message = HEADER_PREFIX + bytes([0x00, 0x00]) + bytes([0x00] * 103) + bytes([0xF7])
    parsed_empty = parse_sysex_message(empty_message)

    last_success: Optional[WritePosition] = None
    ack_count = 0
    last_ack_message: Optional[str] = None

    total_positions = len(positions)
    for index, position in enumerate(positions, start=1):
        patched = patch_bank_slot_in_memory(parsed_empty, position)
        sysex_payload = to_mido_sysex_data(patched)

        result = send_sysex(state.selected_port, sysex_payload)
        if not result.ok:
            if last_success is not None:
                return (
                    False,
                    f"Erase stopped at {_position_label(position)}: {result.message}",
                    last_success,
                )
            return False, result.message, None

        if result.ack_received:
            ack_count += 1
            last_ack_message = result.ack_message
        last_success = position

        if on_progress is not None:
            on_progress(index, total_positions, position)

    if len(positions) == 1 and last_success is not None:
        base = f"Erased preset {_position_label(last_success)}"
        if ack_count:
            return True, f"{base} | ACK received", last_success
        return True, f"{base} | No ACK", last_success

    if last_success is not None:
        base = f"Erased {len(positions)} presets, last {_position_label(last_success)}"
        if ack_count:
            ack_suffix = f" | ACK {ack_count}/{len(positions)}"
            if ack_count == len(positions) and last_ack_message and "JN-80 reply" in last_ack_message:
                ack_suffix += " (JN-80 confirmed)"
            return True, f"{base}{ack_suffix}", last_success
        return True, f"{base} | No ACK", last_success

    return False, "Erase failed", None


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


def _handle_send_with_progress_dialog(
    stdscr: Optional[curses.window],
    state: AppState,
    start: WritePosition,
) -> None:
    files = _target_files(state)
    if not files:
        state.status = "No .syx file selected"
        _show_message_modal(stdscr, "Send Error", state.status)
        return

    modal: Optional[curses.window] = None
    modal_w = 0
    modal_h = 0

    def _render_send_modal(title: str, lines: list[str], footer: str) -> None:
        if modal is None:
            return
        inner_w = modal_w - 4
        modal.erase()
        modal.box()
        modal.attron(curses.A_BOLD)
        modal.addnstr(1, 2, _truncate_line(title, inner_w), inner_w)
        modal.attroff(curses.A_BOLD)
        for idx, line in enumerate(lines, start=2):
            if idx >= modal_h - 1:
                break
            modal.addnstr(idx, 2, _truncate_line(line, inner_w), inner_w)
        if footer:
            modal.addnstr(modal_h - 2, 2, _truncate_line(footer, inner_w), inner_w)
        modal.refresh()

    def _render_send_progress(done: int, total: int, file_path: Path, current: WritePosition) -> None:
        state.status = f"Sending {done}/{total}: {file_path.name} -> {_position_label(current)}"
        _render_send_modal(
            "Send Progress",
            [
                f"Progress: {done}/{total}",
                f"File: {file_path.name}",
                f"Target: {_position_label(current)}",
            ],
            "Please wait...",
        )

    if stdscr is not None:
        height, width = stdscr.getmaxyx()
        modal_h = min(10, max(8, height - 2))
        modal_w = min(72, max(52, width - 4))
        y = (height - modal_h) // 2
        x = (width - modal_w) // 2
        modal = curses.newwin(modal_h, modal_w, y, x)
        modal.keypad(True)
        _render_send_progress(0, len(files), files[0], start)

    ok, message, last = _send_files_with_progress(state, files, start, on_progress=_render_send_progress)
    state.status = message
    if ok and last is not None:
        state.last_written = last
        state.selection_order.clear()

    if modal is not None:
        _render_send_modal(
            "Send Result" if ok else "Send Error",
            [message],
            "Press any key to close",
        )
        modal.timeout(-1)
        modal.getch()
        return

    _show_message_modal(stdscr, "Send Result" if ok else "Send Error", message)


def _handle_f5(stdscr: Optional[curses.window], state: AppState) -> None:
    prefill = f"{state.last_f5_target.bank}{state.last_f5_target.slot}"

    start_position = _prompt_bank_slot(stdscr, prefill)
    if start_position is None:
        state.status = "Send canceled"
        return

    # F5 remembers last entered target independently from write-history tracking.
    state.last_f5_target = start_position

    _handle_send_with_progress_dialog(stdscr, state, start_position)


def _handle_f6(stdscr: Optional[curses.window], state: AppState) -> None:
    if state.last_written is None:
        start = WritePosition("A", 1)
    else:
        start = increment_position(state.last_written)

    _handle_send_with_progress_dialog(stdscr, state, start)


def _handle_f2(stdscr: Optional[curses.window], state: AppState) -> None:
    modal: Optional[curses.window] = None
    modal_w = 0
    modal_h = 0

    def _render_init_progress(done: int, total: int, current: Optional[WritePosition]) -> None:
        if modal is None:
            return
        inner_w = modal_w - 4
        range_line = f"Range: {_position_label(from_position)} -> {_position_label(to_position)}"
        current_line = f"Current: {_position_label(current)}" if current is not None else "Current: --"
        lines = [
            range_line,
            f"Progress: {done}/{total}",
            current_line,
        ]
        modal.erase()
        modal.box()
        modal.attron(curses.A_BOLD)
        modal.addnstr(1, 2, _truncate_line("INIT Progress", inner_w), inner_w)
        modal.attroff(curses.A_BOLD)
        for idx, line in enumerate(lines, start=2):
            if idx >= modal_h - 1:
                break
            modal.addnstr(idx, 2, _truncate_line(line, inner_w), inner_w)
        modal.addnstr(modal_h - 2, 2, _truncate_line("Please wait...", inner_w), inner_w)
        modal.refresh()

    selected = _prompt_init_range(
        stdscr,
        f"{state.last_init_from.bank}{state.last_init_from.slot}",
        f"{state.last_init_to.bank}{state.last_init_to.slot}",
    )
    if selected is None:
        state.status = "INIT canceled"
        return

    from_position, to_position = selected
    state.last_init_from = from_position
    state.last_init_to = to_position
    if _position_ordinal(to_position) < _position_ordinal(from_position):
        message = "Invalid range: FROM must be before or equal to TO"
        state.status = message
        _show_message_modal(stdscr, "INIT Error", message)
        return

    prompt = (
        "Are you sure you want to erase presets "
        f"from {_position_label(from_position)} to {_position_label(to_position)}?"
    )
    if not _prompt_yes_no(stdscr, "Confirm INIT", prompt):
        state.status = "INIT canceled"
        return

    total_targets = len(_positions_inclusive(from_position, to_position))
    state.status = f"INIT erasing 0/{total_targets}..."
    if stdscr is not None:
        height, width = stdscr.getmaxyx()
        modal_h = min(10, max(8, height - 2))
        modal_w = min(72, max(52, width - 4))
        y = (height - modal_h) // 2
        x = (width - modal_w) // 2
        modal = curses.newwin(modal_h, modal_w, y, x)
        modal.keypad(True)
        _render_init_progress(0, total_targets, None)

    def _on_init_progress(done: int, total: int, current: WritePosition) -> None:
        state.status = f"INIT erasing {done}/{total}: {_position_label(current)}"
        _render_init_progress(done, total, current)

    ok, message, last = _send_init_range(state, from_position, to_position, on_progress=_on_init_progress)
    state.status = message

    _show_message_modal(stdscr, "INIT Result" if ok else "INIT Error", message)


def _handle_f8(stdscr: Optional[curses.window], state: AppState) -> None:
    files = _target_files(state)
    if not files:
        state.status = "No .syx file selected"
        return

    if len(files) == 1:
        prompt = f"Delete file {files[0].name}?"
    else:
        prompt = f"Delete {len(files)} selected files?"

    if not _prompt_yes_no(stdscr, "Confirm Delete", prompt):
        state.status = "Delete canceled"
        return

    ok, message = _delete_files(state, files)
    state.status = message
    _refresh_entries(state)


def _handle_receive(stdscr: Optional[curses.window], state: AppState) -> None:
    modal: Optional[curses.window] = None
    modal_w = 0
    modal_h = 0

    def _render_modal(title: str, lines: list[str], footer: str = "") -> None:
        if modal is None:
            return
        inner_w = modal_w - 4
        modal.erase()
        modal.box()
        modal.attron(curses.A_BOLD)
        modal.addnstr(1, 2, _truncate_line(title, inner_w), inner_w)
        modal.attroff(curses.A_BOLD)
        max_rows = modal_h - 4
        for idx, line in enumerate(lines[:max_rows], start=2):
            truncated = _truncate_line(line, inner_w)
            modal.addnstr(idx, 2, truncated.ljust(inner_w), inner_w)
        if footer:
            modal.addnstr(modal_h - 2, 2, _truncate_line(footer, inner_w), inner_w)
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
            last_f5_target=state.last_f5_target,
            last_browsed_dir=str(state.cwd),
            last_init_from=state.last_init_from,
            last_init_to=state.last_init_to,
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
        if ch == curses.KEY_PPAGE:
            _move_cursor_page(state, _visible_list_height(stdscr), -1)
            continue
        if ch == curses.KEY_NPAGE:
            _page_down_pin_top(state, _visible_list_height(stdscr))
            continue
        if ch == curses.KEY_RESIZE:
            # Force a clean repaint on resize so all panes reflow immediately.
            try:
                curses.update_lines_cols()
            except curses.error:
                pass
            stdscr.clear()
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
        if ch == curses.KEY_F2:
            _handle_f2(stdscr, state)
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
        if ch == curses.KEY_F8:
            _handle_f8(stdscr, state)
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
        last_f5_target=cfg.last_f5_target or WritePosition("A", 1),
        last_init_from=cfg.last_init_from or WritePosition("A", 1),
        last_init_to=cfg.last_init_to or WritePosition("A", 1),
    )
    curses.wrapper(_app_loop, state)
