# Changelog

## 0.1.0

First public version.

- `board`: live terminal board — one column per project, stages and steps, live Claude Code
  session state; tick with ⏎⏎, edit statuses (`e`), undo (`u`), minimize (`m`), fit the window
  (`h` / `v`), help (`?`).
- `proj`: CLI for Claude Code (and you) to keep the plan up to date; `proj doctor` checks the install.
- Hooks: record each session's state; give sessions on the board their current stage; nothing for
  sessions off the board (0 tokens).
- `install.py`: one-command install and `--uninstall`; data lives in `~/.project_progress`.
