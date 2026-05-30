# JN-80 Librarian

Terminal SysEx librarian for Behringer JN-80 using a full-screen curses TUI.

## Features

- Full-screen file browser showing folders and `.syx` files only
- Multi-select with `Ctrl-T`
- MIDI output port selection with `F9` or `M`
- Send with `F5` (choose bank/slot) or `F6` (next position)
- Receive/download SysEx from synth with `F7` or `R`
- In-memory rewrite of JN-80 DATA bank/slot bytes only
- Session persistence for MIDI port, last written position, and last browsed dir

## Local setup (venv only)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python -m jn80_librarian
```

## Controls

- `Arrow Up/Down`: Move cursor
- `Enter`: Open directory
- `Ctrl-T`: Toggle selection on highlighted `.syx` file and move cursor down
- `F9` or `M`: Open MIDI output port menu
- `F5`: Send highlighted/selected with bank+slot dialog
- `F6`: Send highlighted/selected to next slot after last write
- `F7` or `R`: Receive SysEx from synth and save as `.syx` in current folder
- `?`: Show key help modal
- `Q` or `F10`: Quit

## Notes

- `.syx` files on disk are never modified.
- During send, the destination write address and DATA bank/slot are rewritten in memory from the selected target.
- After each send, the app listens briefly for a SysEx reply and reports `ACK` or `No ACK`.
- F5/F6 open a result dialog for clear send feedback; status is also mirrored in the bottom status bar.
- F5 bank/slot entry is constrained to valid values only (bank `A-T`, slot `01-20`).
- In F5 bank/slot entry, typing a valid bank letter auto-advances focus to the slot digits.
- F7/R opens a filename prompt, listens for incoming SysEx burst data, and saves captured dumps in the current directory.
  - The same fixed-size dialog stays open for filename entry, waiting/progress, and final result stats.
  - During waiting/receive, press any key to cancel the dump.
  - On the JN-80: press SHIFT+INITIAL/WRITE, then select Dump Presets.
  - Single patch: saves one file using the provided name.
  - Multiple patches: combines all received SysEx frames into the same output file.
  - Capture waits up to 45s total and ends after about 1.2s of no new SysEx frames.
  - While capturing, the status bar shows live progress (`Receiving... patches: N`).
- Receive result dialog shows details: selected port, received patch count, JN-80 frame count, saved file count, and saved filename range.
- Errors are displayed in the send result dialog and the bottom status bar.
- A dedicated F-key help strip is shown above the status bar for quick command reminders.
- If an unmapped key is pressed, status shows the key name and suggests pressing `?` for help.
- Directory rows are prefixed with `/`, and selected files are shown in yellow.

## Changelog

### 1.0.0 - 2026-05-29

- Initial terminal-based JN-80 SysEx librarian release with a full-screen curses file browser for folders and `.syx` files.
- Multi-select workflow (`Ctrl-T`), persistent session state (MIDI port, last write position, last browsed directory), and keyboard-first navigation/help.
- MIDI send flows with `F5` (explicit bank/slot) and `F6` (auto-increment next slot), preserving selection order and clearing selections after successful sends.
- In-memory JN-80 destination rewrite before send (write-address + DATA bank/slot); source `.syx` files are never modified.
- Send feedback includes ACK/No ACK detection plus explicit result dialogs and status-bar updates.
- Receive flow with persistent modal UX (`F7`/`R`): filename entry, waiting/progress, any-key cancel, burst capture, and final stats.
- Receive parser supports single and multi-frame dumps, combines captured frames into one output file by default, and handles both binary and ASCII-hex SysEx inputs.
- Local venv-only setup and automated test coverage for app, MIDI transport, SysEx parsing/rewriting, positions, and config persistence.
