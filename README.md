# claude-project-board

A terminal board for people who run several [Claude Code](https://claude.com/claude-code) sessions
across several projects. At a glance it shows where each project is (stages and steps) **and**
what each Claude session is doing right now: working, waiting for your approval, or done and
waiting for you.

```
 PROJECT BOARD  14:02:11   focus  2 ›
 Recipe app                  │ Blog redesign               │ Tax paperwork               │ CLI tool
 ■■■□□ 3/5                   │ ■■■■■■□ 6/7                 │ ■□□□ 1/4                    │ ■■□□ 2/4
 ◑ working 18s               │ ⚠ needs approval 1m         │ ✓ done 13:40, your turn     │ ○ idle 3h
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━│─────────────────────────────│─────────────────────────────│──────────────
 ▾ ▶ Build  2/4              │ ▸ ✓ Design  3/3             │ ▾ ▶ Collect  1/3            │   ✓ Parse
     ✓ Add search            │ ▾ ▶ Launch  1/2             │     ✓ Download statements   │   ▶ Output
     ◑ Write recipe parser   │     · Fix image sizes       │     · Find donation receipts│   ○ Release
```

- **You** watch `board` and tick, re-status and fold things with the keyboard.
- **Claude Code** keeps the plan up to date through the small `proj` CLI (and the `/board` skill).
- **Hooks** tell the board what every session is doing. Everything stays on your machine.

| Part | What it is | Who uses it |
|---|---|---|
| `board` | the live terminal board (curses) | you |
| `proj` | a small CLI that edits the plan | mostly Claude Code; you can too |
| `hook.py` | Claude Code hooks that record each session's state | runs automatically |

## Requirements

- macOS (tested) or Linux (should work; not tested). Not Windows.
- Python 3.9 or newer, standard library only — on macOS the one from the Xcode Command Line Tools
  (`xcode-select --install`) is enough.
- [Claude Code](https://claude.com/claude-code).
- A terminal with Unicode: Terminal.app, iTerm2, Ghostty, WezTerm, Kitty… The window-fit keys
  (`h` / `v`) need a terminal that allows resizing (not full screen, not tmux).

## Install

```bash
git clone https://github.com/howardwu2026/claude-project-board.git
cd claude-project-board
python3 install.py
```

`install.py` puts `board` and `proj` in `~/.local/bin` (it tells you if that isn't on your PATH),
adds the hooks to `~/.claude/settings.json` (backing it up first, never touching other hooks), and
installs the `/board` skill. Your data goes to `~/.project_progress` (or `$PROJECT_PROGRESS_HOME`).
Nothing is added to your `CLAUDE.md`.

Check everything with:

```bash
proj doctor
```

**Update:** `git pull && python3 install.py`, then press `s` `r` in the board.
**Uninstall:** `python3 install.py --uninstall` (your data folder is kept; delete it yourself if you want).

## Quick start

1. In a terminal, `cd` into a project folder and run `proj new` — it asks for a name and the
   stages (e.g. `Design, Build, Ship`). Or, in a Claude Code session in that folder, type `/board`
   and say *"add this folder to the board with stages Design, Build, Ship"*.
2. Run `board` in a separate terminal window.
3. Start (or resume) a Claude Code session in the project folder. It now appears on the board, and
   Claude knows the current stage. Say *"update the board"* or *"add the next steps"* whenever you
   like.

## The board

Run `board` in its own terminal window. It refreshes itself every second. Press `?` inside it for this list.

**Reading it.** One column per project, at most 4 at a time (the rest wait to the right: `2 ›`).
Projects with a working session sit left-most; the further right, the longer untouched. Columns
only re-order when a session changes state, never while you watch.
Stage marks: `✓` done · `▶` current · `○` not started · `?` idea. Sub-steps: `✓` · `▶` · `·` · `?`.
A spinner marks the step a Claude session is on.

| Key | Does |
|---|---|
| `←` `→` | pick a project |
| `↑` `↓` | one row (stage or sub-step) |
| `Space` | open / close a stage |
| `z` | overview (all closed) ⇄ zoom (all open) |
| `m` | minimize: only each project's status (name, progress bar, session), no stages or steps; `m` or `z` brings them back |
| `v` | fit the window's height to the longest column shown, so no blank rows are left (Terminal.app, iTerm2; not in full screen or tmux) |
| `h` | fit the width: the set number of projects (4, or `s` + digit), each column as wide as its longest line (at most 42); the height follows |
| `e` | edit mode (yellow cursor) — then `o` ○ open · `p` ▶ doing · `?` idea · `x` then `⏎` delete the project (`u` brings it back until you quit or `s r`; then it is gone for good) |
| `u` | undo the last board change (refused if something else changed that file since) |
| `s` then `↑` `↓` | previous / next stage |
| `s` then `←` `→` | first / last project |
| `s` then `t` `b` | top / bottom of the column |
| `s` then `o` | only this project ⇄ the whole board |
| `s` then `1`–`9` | how many projects side by side (back to 4 on reload) |
| `s` then `r` | reload (new code, fresh data, same view) |
| `Esc` | leave edit mode → cancel → back to the whole board |
| `?` | help · `q` quit |

Also: `⇧↑` `⇧↓` `PgUp` `PgDn` `{` `}` jump stages, `Home` `End` jump to top / bottom.
The mouse wheel is ignored on purpose.

Adding, renaming, moving and removing steps or stages is done with `proj` — just ask Claude Code.

## `proj`

The project id can be left out: `proj` takes it from the current Claude session or folder and prints
`[id]`. Steps are matched by a short case-insensitive fragment; use `"Stage::step"` when two stages
have a step with the same name. A command with one bad fragment changes nothing.

| Need | Command |
|---|---|
| see | `proj list` · `proj show [id]` · `proj show --focus [id]` |
| new project | `proj add <id> "<name>" --path <dir> --stages "A,B,C" [--branch <b>]` · `proj new` (asks) |
| work | `proj start "<frag>"` · `proj tick "<a>" "<b>"` · `proj untick "<a>"` · `proj stage next` |
| steps | `proj step add "<stage>" "<step>" …` · `step rm` · `step rename "<frag>" "<new>"` · `step mv "<frag>" up\|down\|first\|last\|--before\|--after\|--to "<stage>"` |
| stages | `proj stage add "<A>" … [--before\|--after "<stage>"]` · `stage rm` · `stage rename` · `stage mv` |
| notes | `proj idea "<text>" [--stage "<frag>" \| --top]` · `proj block "<text>"` · `proj unblock [n]` |
| sessions | `proj use <id>` (when a folder matches several projects) · `proj flow [id]` (file path) |

Names that would duplicate another in the same stage (or another stage) are refused.

## How Claude Code fits in

- **Hooks** record each session's state in `status.json` (working / needs approval / done / idle).
  At session start they also give Claude the project's current stage (≤8 lines), or ask it to
  check with you when the folder matches several projects. Hooks never fail Claude Code, and they
  skip an update rather than write while `proj` or the board is writing.
- **Deleting a project on the board stops tracking it.** Its sessions become unassigned, `proj`
  refuses its id ("deleted on the board"), and a resumed session is told not to re-add it.
  `u` in the board undoes the delete and reconnects the sessions — until you quit the board or
  press `s r`; then the flow file is deleted for good.
- **The `board` skill** tells Claude the exact commands and a token-saving routine: no id, one
  Bash call, no re-reading, one-line reply. In a session on the board, say "update the board",
  "加接下来的 steps" or "存档" — the hook already told Claude where the skill is. In a session that
  isn't on the board yet, Claude knows nothing about it: type `/board` in that session first (then
  e.g. "add this folder to the board"), or run `proj new` in a terminal yourself.

### Picking up work later

The board is an index, not a memory. Steps are ≤8 words, and a new session only receives the
current stage's step titles. The *why* (decisions, numbers, dead ends, which files matter) stays
in the session where it happened. So, to keep the board small and lose nothing:

- **Same work → resume the same session**: `claude -c` (latest session in this folder),
  `claude --resume` (pick one), or reopen it in the desktop app. The full context is still there.
- **Must start fresh** (context full, new machine): first ask the old session for a handoff,
  e.g. *"write a handoff for the next session: done, next, decisions, gotchas, key files"*, and
  paste it into the new session.
- **Details that must outlive any session** (decisions, terms, numbers) go in the project's own
  files (`notes.md`, docs). The board only tracks where you are.

Token cost: 0 for sessions not on the board. A session on the board gets the SessionStart lines
(stage + rules, about 200 tokens), plus about 1,300 the first time the skill is read and a few
hundred per update. Using the board yourself costs nothing.

## Files

Your data lives in one folder, `~/.project_progress` (or `$PROJECT_PROGRESS_HOME`). The code
folder holds none of it.

| File | Holds |
|---|---|
| `projects.json` | projects, their folders, blockers |
| `flows/<id>.md` | each project's plan (below) |
| `status.json` | live session state — rebuilt by the hooks; a broken one is set aside as `status.json.bad-*` |
| `flows/.trash/` | flow files of projects deleted in the board, kept only until the board quits or reloads |

Tip: `git init` the data folder if you want a history of your plans.

A broken `projects.json` or flow file is never overwritten: `proj` stops with a clear message,
the hook tells Claude, and the board keeps running (last good project list, or the error in that
project's column).

Flow file format — plain markdown:

```markdown
- [x] Stage done          ← no indent = stage
- [>] Current stage       ← exactly one [>] among stages
  - [x] Sub-step done     ← 2 spaces = sub-step
  - [ ] Sub-step todo
  - [?] Idea (not counted in progress)
- [ ] Later stage
```

Other lines are kept as notes.

## Privacy

Everything is local files. The board, `proj` and the hooks make no network calls. The only text
that reaches Claude is what Claude Code already sends: the few SessionStart lines for sessions in a
folder on the board, and the output of `proj` commands Claude runs.

## Tests

```bash
python3 -m unittest discover -s tests
```

Every test runs against its own temporary data folder (and a temporary home for the installer
tests), so your real projects and settings are never touched.
`board --dump [--size 120x40] [--fold zoom] [--sel <id>] [--solo] [--min] [--cols N]` prints one
frame as text, which is handy for checking the layout without a terminal.

## License

MIT — see [LICENSE](LICENSE).
