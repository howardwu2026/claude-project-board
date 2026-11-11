#!/usr/bin/env python3
"""Claude Code hook: records live session state in status.json (data folder, ~/.project_progress by default).

Reads the hook JSON from stdin. Prints only on SessionStart (that text is added
to Claude's context). Always exits 0 and never raises.
"""
import json
import os
import sys
import time

STATE = {
    "UserPromptSubmit": "processing",
    "PreToolUse": "processing",
    "PostToolUse": "processing",
    "PermissionRequest": "waiting_approval",
    "Notification": "waiting_approval",   # matcher limits it to permission/dialog prompts
    "Stop": "done",
    "StopFailure": "done",
    "SessionStart": "idle",
}
STALE = 3 * 86400  # drop sessions with no event for 3 days (crashed without SessionEnd)
LOCK_WAIT = 1.0    # seconds to wait for proj/board to finish a write; past that, skip this one update


SKILL = os.path.join(os.path.expanduser("~"), ".claude", "skills", "board", "SKILL.md")
RULES = [   # only sessions on the board see these (nothing in CLAUDE.md, so other sessions cost 0 tokens)
    "Board rules: before your first proj command, Read %s once (every command; never `proj --help`, "
    "never read flow files)." % SKILL,
    "When you begin a step above, run `proj start \"<step>\"`; `proj tick` only when clearly done "
    "(file written, test passed, decision made) — unsure → ask. Don't run proj every turn.",
]


def main():
    ev = json.load(sys.stdin)
    sid, cwd, name = ev.get("session_id"), ev.get("cwd") or os.getcwd(), ev.get("hook_event_name")
    if not sid or not name:
        return
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import core

    now = time.time()
    out = None
    with core.locked(timeout=LOCK_WAIT) as got:
        st = core.load_status()
        ss = st["sessions"]
        old = ss.get(sid, {})
        bad = None
        try:
            projects = core.load_projects()["projects"]
        except core.DataError as e:         # never touch a broken registry; tell Claude at session start
            projects, bad = [], str(e)
        branch = core.read_branch(cwd)
        pid, cands = core.resolve(cwd, branch, projects, old.get("assigned"))
        if pid != "unassigned":
            st["last_active"][pid] = now

        if name == "SessionEnd":
            ss.pop(sid, None)
        elif name in STATE:
            state = STATE[name]
            if name == "PostToolUse" and old.get("state") not in ("waiting_approval", None):
                state = old["state"]  # only used to clear a waiting state after approval
            s = dict(old, session_id=sid, cwd=cwd, branch=branch, project_id=pid,
                     candidates=cands if pid == "unassigned" else [], updated=now)
            if old.get("state") != state:
                s.update(state=state, since=now)
            if name == "UserPromptSubmit":
                s["turn_at"] = now        # board: newest work goes left-most

            if pid != "unassigned":
                s.pop("removed", None)       # the folder now belongs to a live project
            s.setdefault("active_step", None)
            if old.get("project_id") not in (None, pid):
                s["active_step"] = None
            ss[sid] = s
        else:
            return

        if name == "SessionStart" and bad:
            out = ["Project board is broken: %s." % bad,
                   "Tell the user; don't run proj until the file is fixed (it is in the board's data folder)."]
        elif name == "SessionStart":
            for k in [k for k, v in ss.items() if now - v.get("updated", now) > STALE]:
                del ss[k]
            if pid != "unassigned":
                p = next(p for p in projects if p["id"] == pid)
                try:
                    out = core.focus_lines(p, core.Flow(core.flow_path(pid)))
                except (OSError, core.DataError):
                    out = None
                if out:
                    out = ["Project board (proj CLI) — this session is on:"] + out + RULES
            elif ss[sid].get("removed") and not cands:
                out = ["Project board: this session's project '%s' was deleted on the board, so it is no longer "
                       "tracked. Don't run proj for it or re-add it unless the user asks." % ss[sid]["removed"]]
            elif len(cands) > 1:
                out = ["Project board: this folder matches several projects: %s." % ", ".join(cands),
                       "Ask the user which one, then run `proj use <id>`."]
        if got and not bad:                  # unlocked → someone is mid-write; don't clobber it
            core.write_json(core.STATUS, st)
    if out:
        print("\n".join(out))


if __name__ == "__main__":
    try:
        main()
    except BaseException:  # never break Claude Code
        pass
    sys.exit(0)
