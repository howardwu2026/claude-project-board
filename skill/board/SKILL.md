---
name: board
disable-model-invocation: true
description: Update the local project board (`proj` CLI) with minimal tokens - start/tick steps, add or restructure steps and stages, ideas, blockers, new projects, 存档/checkpoint, planning a flow. Use when the user asks to update the board/progress/steps, says 存档/checkpoint, or types /board.
---

# Project board (`proj`)

Goal: minimum tokens. Everything you need is here — don't explore.

## Rules
1. **Omit the project id.** `proj` infers it from this session/folder and prints `[id]`. Pass one only for another project, or when `proj` says `which project?` (take it from the SessionStart line `[<id>] …`, or ask). Never run `proj list` or `--help`.
2. **Don't read** flow files or `projects.json`. If SessionStart context isn't enough, run `proj show --focus` (≤8 lines); full `proj show` only when planning.
3. **One Bash call per update**: multi-value commands plus `&&`. Never re-check — the output confirms each change. A bad fragment makes that command change nothing.
4. **Match by a short unique fragment** (case-insensitive; exact wins): `proj tick "login"`. If ambiguous it lists `Stage :: step` candidates; retry once with a longer fragment or `"Stage::step"`.
5. **Deleted on the board = gone.** If `proj` or the SessionStart line says a project was deleted on the board, stop: don't `proj add` it back, don't pass its id again. Tell the user in one line (`u` in the same board window undoes it, until they quit or `s r`). Re-add only if they ask.
6. **Reply in one line**, e.g. `Board: +3 steps under Build · ticked "login form"`. Don't paste output.

## Commands (`[id]` optional)
| Need | Command |
|---|---|
| add sub-steps (appended in order) | `proj step add [id] "<stage>" "<step 1>" "<step 2>" …` |
| delete / rename sub-step | `proj step rm [id] "<frag>" …` · `proj step rename [id] "<frag>" "<new>"` |
| move sub-step | `proj step mv [id] "<frag>" up\|down\|first\|last` · `--before/--after "<frag>"` · `--to "<stage>"` |
| add stage(s) (default: end) | `proj stage add [id] "<A>" "<B>" … [--before/--after "<stage>"]` |
| delete (with sub-steps) / rename / move stage | `proj stage rm [id] "<frag>"` · `stage rename [id] "<frag>" "<new>"` · `stage mv [id] "<frag>" up\|down\|first\|last\|--before/--after "<stage>"` |
| begin a step | `proj start [id] "<frag>"` |
| done (stage auto-advances when all sub-steps are done) / undo | `proj tick [id] "<frag>" …` · `proj untick [id] "<frag>" …` |
| finish current stage → next | `proj stage next [id]` |
| idea (current stage / given stage / top level) | `proj idea [id] "<text>"` · `--stage "<frag>"` · `--top` |
| blocker / remove blocker n | `proj block [id] "<text>"` · `proj unblock [id] <n>` |
| state, ≤8 lines | `proj show --focus [id]` |
| new project (folder = cwd) | `proj add <id> "<name>" --path "$PWD" --stages "A,B,C" [--branch <b>]` — id: `a-z 0-9 - _` |
| folder matches several projects | `proj use <id>` |
| flow file path (planning only) | `proj flow [id]` |

`rm` can't be undone, so remove only what the user named. Duplicate names in one stage (or duplicate stages) are refused.

## Writing steps
- Verb first, **≤8 words**, *what* not *how*: `Add login form`, `确认 PATH 设置`.
- **2–5 sub-steps** per stage; only the next concrete ones.
- No numbering, trailing punctuation or status words — markers carry status.
- Distinct first words (short fragments stay unique); the user's language.

## Recipes (each ONE Bash call)
**Next steps** ("加接下来的 steps", "/board next"): `proj step add "<stage>" "Step one" "Step two"`
**Finished something** — tick only when clearly complete (file written, test passed, decision made); unsure → ask: `proj tick "<done>" && proj start "<next>"`
**存档 / checkpoint** — summarize the session in ≤5 lines (done · next · blockers), ask to confirm, then e.g. `proj tick "<a>" "<b>" && proj step add "<stage>" "<new>" && proj unblock 1 && proj block "<new blocker>"`
**Restructure** ("删掉…", "改成…", "移到…前面", "加一个 stage"): chain the commands above, e.g. `proj stage add "Deploy" --after "Test" && proj step mv "write docs" --to "Deploy" && proj step rename "fix bug" "Fix login bug"`
**Plan / brainstorm** (only when asked): `proj flow`, Read the file once, discuss, apply everything in one Edit. A chosen idea: `[?]` → `[ ]`; delete rejected alternatives.

The user ticks (`⏎⏎`), sets statuses (`e` then `o` ○ / `p` ▶ / `?` idea) and deletes whole projects (`e` `x` `⏎`; `u` undoes) in `board`; `?` there lists every key. Everything else structural is yours via `proj`; edit the flow file directly only for what `proj` can't express.

## Flow file format (planning edits only)
```
- [x] Stage done          ← no indent = stage
- [>] Current stage       ← exactly one [>] among stages
  - [x] Sub-step done     ← 2 spaces = sub-step
  - [ ] Sub-step todo
  - [?] Idea (not counted in progress)
- [ ] Later stage
```
Other lines are ignored notes.
