# JN-80 Librarian

Terminal SysEx librarian for Behringer JN-80 using a full-screen curses TUI.

## Features

- Full-screen file browser showing folders and `.syx` files only
- Multi-select with `Ctrl-T`
- INIT erase range with `F2` (bank/preset to bank/preset)
- MIDI output port selection with `F9` or `M`
- Send with `F5` (choose bank/slot) or `F6` (next position)
- Receive/download SysEx from synth with `F7` or `R`
- Delete selected/highlighted `.syx` file(s) with `F8` (with confirmation)
- In-memory rewrite of JN-80 DATA bank/slot bytes only
- Session persistence for MIDI port, last written position, last F5 target, and last browsed dir

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
- `PgUp/PgDn`: Move cursor by one page
- `Enter`: Open directory
- `Ctrl-T`: Toggle selection on highlighted `.syx` file and move cursor down
- `F2`: INIT erase presets in an inclusive bank/slot range (with confirmation)
- `F9` or `M`: Open MIDI output port menu
- `F5`: Send highlighted/selected with bank+slot dialog
- `F6`: Send highlighted/selected to next slot after last write
- `F7` or `R`: Receive SysEx from synth and save as `.syx` in current folder
- `F8`: Delete selected/highlighted `.syx` file(s) (with confirmation)
- `?`: Show key help modal
- `Q` or `F10`: Quit

## Notes

- `.syx` files on disk are never modified.
- During send, the destination write address and DATA bank/slot are rewritten in memory from the selected target.
- F2 INIT sends an empty/blank JN-80 preset payload to each destination in the selected inclusive range.
- During F2 INIT, one modal handles confirmation, live erase progress (`Progress: X/Y`, `Current: BNN`), and final result.
- After each send, the app listens briefly for a SysEx reply and reports `ACK` or `No ACK`.
- During F5/F6 send, the same dialog first shows live copy progress (`Progress: X/Y`, current file, target slot), then shows the final send result.
- F5/F6 open a result dialog for clear send feedback; status is also mirrored in the bottom status bar.
- When a `.syx` file is highlighted, the status bar shows its detected patch count (`Patches: N`).
- F5/F6 send every SysEx frame found in each selected `.syx` file (not just the first frame).
- Frames are sent in deterministic order: selected file order (selection timestamp), then frame order within each file.
- Before F5/F6 send starts, destination capacity is validated from the selected start slot through `T20`; if there is not enough room for all frames, send is blocked before any write.
- F6 does not wrap after `T20`; if `Last` is `T20`, `F6` stops with an error instead of writing to `A01`.
- F5 remembers the last bank/slot you entered (independent from F6/INIT write history).
- F2 INIT does not update `Last`; only successful file sends (F5/F6) advance write history.
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
- Directory rows are prefixed with `/`, and selected files are shown in yellow.
- The top bar shows the running app version next to the title.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Changelog

### 1.3.0 - 2026-05-31

- New features:
  - Added multi-frame `.syx` send support: `F5`/`F6` now sends all SysEx frames found in each selected file, in deterministic order.
  - Added active file patch-count indicator in the status bar (`Patches: N` / `Patches: invalid`).
  - Added app version display in the top title bar.
- Bug fixes:
  - Prevented `F6` from wrapping from `T20` to `A01`; copy-next now stops with an explicit error at end-of-memory.

### 1.2.0 - 2026-05-30

- New features:
  - Added `F8` delete flow for selected/highlighted `.syx` files with confirmation.
  - Added `PgUp`/`PgDn` navigation in the browser with page-sized movement.
  - Added live progress dialogs for send operations (`F5`/`F6`) and INIT erase (`F2`), with result shown in the same modal flow.
  - Added independent persisted `F5` target memory (last entered bank/slot).
- Bug fixes:
  - INIT no longer updates `Last` write history (reserved for successful file sends).

### 1.1.0 - 2026-05-30

- Added `F2` INIT workflow to erase presets in an inclusive bank/slot range with a confirmation prompt before write.
- Long paths in modal dialog content (port names, saved filenames) are now truncated from the left, keeping the filename tail visible.

### 1.0.0 - 2026-05-29

- Initial terminal-based JN-80 SysEx librarian release with a full-screen curses file browser for folders and `.syx` files.
- Multi-select workflow (`Ctrl-T`), persistent session state (MIDI port, last write position, last browsed directory), and keyboard-first navigation/help.
- MIDI send flows with `F5` (explicit bank/slot) and `F6` (auto-increment next slot), preserving selection order and clearing selections after successful sends.
- In-memory JN-80 destination rewrite before send (write-address + DATA bank/slot); source `.syx` files are never modified.
- Send feedback includes ACK/No ACK detection plus explicit result dialogs and status-bar updates.
- Receive flow with persistent modal UX (`F7`/`R`): filename entry, waiting/progress, any-key cancel, burst capture, and final stats.
- Receive parser supports single and multi-frame dumps, combines captured frames into one output file by default, and handles both binary and ASCII-hex SysEx inputs.
- Local venv-only setup and automated test coverage for app, MIDI transport, SysEx parsing/rewriting, positions, and config persistence.
