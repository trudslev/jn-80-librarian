---
description: "Use when modifying the JN-80 librarian TUI, MIDI send flow, key bindings, or setup docs. Enforces venv-only workflow, destination bank/slot write behavior, ACK feedback, and agreed UX shortcuts."
name: "JN-80 Librarian Behavior Rules"
applyTo: ["jn80_librarian/**/*.py", "README.md"]
---
# JN-80 Librarian Behavior Rules

- Use project-local virtual environment only. Do not add or suggest global package installs.
- Keep MIDI destination semantics correct: on send, rewrite both write-address bytes and DATA bank/slot bytes in memory from the chosen target bank/slot.
- Never modify source .syx files on disk; all patching must remain in-memory.
- Preserve batch order as selection timestamp order (first selected, first sent).
- After successful F5/F6 transfer, clear all selected files.
- Keep these key bindings intact unless explicitly changed by user request:
  - Ctrl-T toggles selection and advances cursor one row down.
  - F9 and M open MIDI output selection.
  - F5 sends with bank/slot prompt.
  - F6 sends to next position after last written slot.
  - Q and F10 quit.
  - ? opens key-help modal.
- Keep status bar feedback explicit for send outcomes:
  - include target result text,
  - include ACK vs No ACK feedback,
  - show meaningful error text for no port, invalid .syx, and send failure.
- When changing behavior, update README controls/notes so runtime behavior and docs stay aligned.

## Release Flow

**Trigger:** User says "Ready to commit"

Follow this workflow in order:

1. **Evaluate Tests** - Review changes and determine if new tests are needed for coverage or behavior validation.

2. **Run Tests** - If new tests were added or changes affect existing behavior, run `.venv/bin/python -m unittest discover -s tests -v`. Block if any test fails.

3. **Determine Version** - Analyze commits since last tag and bump version semver:
  - **Major** (X.0.0) - Breaking changes to API, storage schema, or deployment
  - **Minor** (x.Y.0) - New features, non-breaking additions
  - **Patch** (x.y.Z) - Bug fixes, refactors, docs only
  - Use `git tag` to find last version if unsure

4. **Update Changelog** - Add detailed entry in README.md `## Changelog` section (top of section) with version, date, and bullets listing:
  - New features
  - Bug fixes
  - Breaking changes (if any)
  - Dependencies added/updated
5. **Generate Release Title Proposal** - Propose a release title derived from the changelog highlights (concise, human-readable).

6. **Pause for Curation** - Pause and ask the user to confirm both:
  - changelog content,
  - proposed release title.
  Do not commit/tag/push until user confirms.

7. **Git Commit** - Stage all changes:
  ```
  git add .
  git commit -m "Release <VERSION>: <summary of changes>"
  ```

8. **Tag Version** - Create annotated tag (must be plain semver with no leading 'v', e.g., `1.1.1`):
  ```
  git tag -a <VERSION> -m "Release <VERSION>"
  ```

9. **Push** - Push commits and tags:
  ```
  git push origin $(git rev-parse --abbrev-ref HEAD)
  git push origin --tags
  ```

**Example:**
- Last tag: `5.2.1`
- Changes: Added multi-user auth (minor feature)
- New version: `5.3.0`
- Commit message: `Release 5.3.0: Add user authentication and session management`
