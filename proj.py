#!/usr/bin/env python3
"""proj — manage the local project board (data in ~/.project_progress, or $PROJECT_PROGRESS_HOME)."""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import core  # noqa: E402
from core import CUR, DONE, IDEA, TODO, Flow  # noqa: E402


def die(msg):
    sys.exit("proj: " + msg)


def get(reg, pid):
    for p in reg["projects"]:
        if p["id"] == pid:
            return p
    die("unknown project '%s' (have: %s)" % (pid, ", ".join(p["id"] for p in reg["projects"]) or "none"))


def flow_of(pid):
    try:
        return Flow(core.flow_path(pid))
    except FileNotFoundError:
        die("flow file missing: " + core.flow_path(pid))
    except OSError as e:
        die("can't read %s: %s" % (core.flow_path(pid), e.strerror))


def no_dupes(new, existing, what):
    """Refuse names that would make two identical items in one place (they can't be told apart)."""
    seen = set(existing)
    for t in new:
        if t in seen:
            die("%s '%s' already exists there — pick a different name (nothing changed)" % (what, t))
        seen.add(t)


def dup_steps(f):
    out = set()
    for st in f.stages:
        names = [c.text for c in st.children]
        out |= {(st.text, t) for t in names if names.count(t) > 1}
    return out


def clean(texts):
    out = [" ".join(t.split()) for t in texts]
    if any(not t for t in out):
        die("empty text (nothing changed)")
    return out


def find(flow, q, **kw):
    try:
        return flow.find(q, **kw)
    except ValueError as e:
        die(str(e))


def save(reg, p, flow=None):
    if flow:
        flow.save()
    p["updated_at"] = core.now_iso()
    core.write_json(core.PROJECTS, reg)


def flash(p, text):
    p["flash"] = {"text": text, "at": core.now_iso()}


def this_session(status):
    """Current Claude Code session: $CLAUDE_CODE_SESSION_ID, else newest session in this cwd."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        return sid
    here = os.path.realpath(os.getcwd())
    mine = [s for s in status["sessions"].values() if os.path.realpath(s.get("cwd", "")) == here]
    return max(mine, key=lambda s: s.get("updated", 0))["session_id"] if mine else None


def update_session(sid, **fields):
    st = core.load_status()
    s = st["sessions"].setdefault(sid, {"session_id": sid, "cwd": os.getcwd(), "branch": "",
                                        "state": "idle", "since": time.time()})
    s.update(fields)
    core.write_json(core.STATUS, st)


# ---------------------------------------------------------------- commands

def cmd_list(a, reg):
    st = core.load_status()
    live = {}
    for s in st["sessions"].values():
        live.setdefault(s.get("project_id"), []).append(s["state"])
    for p in reg["projects"]:
        f = flow_of(p["id"])
        d, n = f.progress()
        cur = f.current()
        states = ",".join(live.get(p["id"], [])) or "-"
        blk = "  ⛔%d" % len(p["blockers"]) if p.get("blockers") else ""
        bar = "".join("□" if s.mark == TODO else "■" for s in f.real_stages())
        print("%-20s %s %d/%d %-22s %-16s %s%s" % (
            p["id"], bar, d, n, core.clip(cur.text if cur else "—", 22),
            states, core.clip(p["name"], 40), blk))
    un = [s for s in st["sessions"].values()
          if s.get("project_id") == "unassigned" and len(s.get("candidates", [])) > 1]
    for s in un:
        print("? unassigned session in %s (%s) — run `proj use %s` there" % (
            s["cwd"], s["state"], "|".join(s["candidates"])))


def cmd_show(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    if a.focus:
        print("\n".join(core.focus_lines(p, f)))
        return
    m = p["match"]
    print("%s — %s\n  path: %s%s\n  flow: %s" % (
        p["id"], p["name"], m["path"], "  branch: " + m["branch"] if m.get("branch") else "", f.path))
    for s in f.stages:
        print("%s %s" % (core.SYM[s.mark], s.text))
        for c in s.children:
            print("    %s %s" % (core.SYM[c.mark], c.text))
    for i, b in enumerate(p.get("blockers", []), 1):
        print("⛔ %d. %s (since %s, %dd)" % (i, b["text"], b["since"][:10], core.days_since(b["since"])))


def add_project(reg, pid, name, path, branch, stages):
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", pid):
        die("id must be lowercase letters/digits/-/_")
    if any(p["id"] == pid for p in reg["projects"]):
        die("project '%s' already exists" % pid)
    stages = [s.strip() for s in stages if s.strip()]
    if not stages:
        die("need at least one stage")
    no_dupes(stages, [], "stage")
    match = {"path": os.path.abspath(os.path.expanduser(path))}
    if branch:
        match["branch"] = branch
    p = {"id": pid, "name": name, "match": match, "blockers": [], "updated_at": core.now_iso()}
    reg["projects"].append(p)
    fp = core.flow_path(pid)
    if not os.path.exists(fp):
        core.write_atomic(fp, core.new_flow_text(name, stages))
    core.write_json(core.PROJECTS, reg)
    print("added %s → %s" % (pid, fp))


def cmd_add(a, reg):
    add_project(reg, a.id, a.name, a.path, a.branch, a.stages.split(","))


def cmd_new(a):
    def ask(q, default=""):
        v = input("%s%s: " % (q, " [%s]" % default if default else "")).strip()
        return v or default
    try:
        pid = ask("id (e.g. my-project)")
        name = ask("name", pid)
        path = ask("folder", os.getcwd())
        branch = ask("git branch (blank = any)")
        stages = ask("stages, comma-separated")
    except (EOFError, KeyboardInterrupt):
        die("cancelled")
    with core.locked():  # take the lock only after prompting
        add_project(core.load_projects(), pid, name, path, branch, stages.split(","))


def doctor():
    """proj doctor: is everything installed and readable? -> exit code (1 if something is wrong)."""
    import json
    import shutil
    bad = 0

    def check(ok, good, fix):
        nonlocal bad
        print(" %s %s" % ("✓" if ok else "✗", good if ok else fix))
        bad += not ok

    print("claude-project-board %s  (code: %s)" % (core.VERSION, core.short(core.CODE)))
    check(sys.version_info >= (3, 9), "Python %s" % sys.version.split()[0],
          "Python %s is too old — 3.9 or newer is needed" % sys.version.split()[0])
    check(os.path.isdir(core.HOME) and os.access(core.HOME, os.W_OK), "data folder %s" % core.short(core.HOME),
          "data folder %s is missing or not writable — run install.py" % core.short(core.HOME))
    try:
        ps = core.load_projects()["projects"]
        missing = [p["id"] for p in ps if not os.path.exists(core.flow_path(p["id"]))]
        check(not missing, "%d project%s registered" % (len(ps), "" if len(ps) == 1 else "s"),
              "flow file missing for: %s" % ", ".join(missing))
    except core.DataError as e:
        check(False, "", str(e))
    for name, script in (("board", "board.py"), ("proj", "proj.py")):
        found = shutil.which(name)
        check(bool(found) and os.path.realpath(found) == os.path.join(core.CODE, script), "`%s` on PATH" % name,
              "`%s` %s — run install.py (and put ~/.local/bin on PATH)" % (
                  name, "points at another copy: " + os.path.realpath(found) if found else "not on PATH"))
    settings = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(settings, encoding="utf-8") as f:
            hooks = json.load(f).get("hooks", {})
    except (OSError, ValueError, AttributeError):
        hooks = {}
    have = {ev for ev, groups in hooks.items() if isinstance(groups, list)
            for g in groups if isinstance(g, dict) for h in g.get("hooks", []) if core.is_our_hook(h)}
    lost = [ev for ev in core.HOOK_EVENTS if ev not in have]
    check(not lost, "Claude Code hooks (%d events)" % len(core.HOOK_EVENTS),
          "hooks missing for %s — run install.py" % ", ".join(lost))
    skill = os.path.expanduser("~/.claude/skills/board/SKILL.md")
    check(os.path.exists(skill), "/board skill", "no /board skill at %s — run install.py" % core.short(skill))
    print(" · terminal: %s" % (os.environ.get("TERM_PROGRAM") or os.environ.get("TERM") or "unknown"))
    print("all good" if not bad else "%d problem%s" % (bad, "" if bad == 1 else "s"))
    return 1 if bad else 0


def cmd_flow(a, reg):
    print(core.flow_path(get(reg, a.id)["id"]))


def cmd_use(a, reg):
    p = get(reg, a.id)
    st = core.load_status()
    sid = this_session(st)
    if not sid:
        die("no Claude Code session found (run this from inside Claude Code)")
    update_session(sid, assigned=p["id"], project_id=p["id"])
    print("session %s… → %s" % (sid[:8], p["id"]))


def cmd_start(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    it = find(f, a.step)
    if it.indent and it.mark != CUR:
        f.set_mark(it, CUR)
    save(reg, p, f)
    sid = this_session(core.load_status())
    if sid:
        update_session(sid, project_id=p["id"], active_step=it.text)
    print("started: %s%s" % (it.text, "" if sid else "  (no Claude session found)"))


def cmd_tick(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    items = [find(f, q) for q in a.step]     # resolve all first: one bad fragment changes nothing
    out = []
    for it in items:
        nxt = core.tick_item(p, f, it)
        out.append("✓ " + it.text)
        if nxt:
            flash(p, "stage complete → " + nxt.text)
            out.append("stage complete → " + nxt.text)
    save(reg, p, f)
    core.clear_active(p["id"], {it.text for it in items})
    print("\n".join(out))


def cmd_untick(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    items = [find(f, q) for q in a.step]
    for it in items:
        core.untick_item(f, it)
    save(reg, p, f)
    print("\n".join("· " + it.text for it in items))


# --- structure: add / remove / rename / move stages and sub-steps

def where_to(f, it, a, stage_mode):
    """Target line for a move: up/down/first/last, or --before/--after another item, or --to <stage>."""
    pool = f.stages if stage_mode else f.stage_of(it).children
    if a.before or a.after:
        ref = find(f, a.before or a.after, stages_only=stage_mode, steps_only=not stage_mode)
        if ref is it:
            die("can't move an item next to itself")
        return f.span(ref)[0] if a.before else f.span(ref)[1]
    if getattr(a, "to", None):
        st = find(f, a.to, stages_only=True)
        return (st.children[-1].line if st.children else st.line) + 1
    i = next(n for n, x in enumerate(pool) if x is it)
    pos = a.pos or ""
    if pos == "up":
        if i == 0:
            die("already first")
        return f.span(pool[i - 1])[0]
    if pos == "down":
        if i == len(pool) - 1:
            die("already last")
        return f.span(pool[i + 1])[1]
    if pos == "first":
        return f.span(pool[0])[0]
    if pos == "last":
        return f.span(pool[-1])[1]
    die("say where: up | down | first | last | --before <frag> | --after <frag>"
        + ("" if stage_mode else " | --to <stage>"))


def cmd_step_rm(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    items = [find(f, q, steps_only=True) for q in a.step]
    texts = [it.text for it in items]
    for t in texts:                          # remove() re-parses, so look each one up again
        f.remove(next(c for s in f.stages for c in s.children if c.text == t))
    save(reg, p, f)
    print("removed: " + " · ".join(texts))


def cmd_step_rename(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    it = find(f, a.step, steps_only=True)
    old, (a.text,) = it.text, clean([a.text])
    no_dupes([a.text], [c.text for c in f.stage_of(it).children if c is not it], "step")
    f.rename(it, a.text)
    save(reg, p, f)
    print("renamed: %s → %s" % (old, a.text))


def cmd_step_mv(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    it = find(f, a.step, steps_only=True)
    had = dup_steps(f)
    f.move(it, where_to(f, it, a, False))
    if dup_steps(f) - had:                   # checked before saving, so nothing is written
        die("step '%s' already exists in that stage — rename one first (nothing changed)" % it.text)
    save(reg, p, f)
    print("moved: %s → under %s" % (it.text, next(s.text for s in f.stages for c in s.children if c.text == it.text)))


def cmd_stage_add(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    a.text = clean(a.text)
    no_dupes(a.text, [s.text for s in f.stages], "stage")
    if a.before or a.after:
        ref = find(f, a.before or a.after, stages_only=True)
        at = f.span(ref)[0] if a.before else f.span(ref)[1]
    else:
        at = f.end_of_stages()
    for t in reversed(a.text):               # same insert point → reverse keeps the given order
        f.insert(at, "- [ ] " + t)
    f.fix_current()
    save(reg, p, f)
    print("added stage(s): " + " · ".join(a.text))


def cmd_stage_rm(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    st = find(f, a.stage, stages_only=True)
    n = len(st.children)
    f.remove(st)
    save(reg, p, f)
    print("removed stage: %s%s" % (st.text, " (+%d sub-steps)" % n if n else ""))


def cmd_stage_rename(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    st = find(f, a.stage, stages_only=True)
    old, (a.text,) = st.text, clean([a.text])
    no_dupes([a.text], [s.text for s in f.stages if s is not st], "stage")
    f.rename(st, a.text)
    save(reg, p, f)
    print("renamed stage: %s → %s" % (old, a.text))


def cmd_stage_mv(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    st = find(f, a.stage, stages_only=True)
    f.move(st, where_to(f, st, a, True))
    save(reg, p, f)
    print("moved stage: " + st.text)


def cmd_step_add(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    st0 = find(f, a.stage, stages_only=True)
    name, a.text = st0.text, clean(a.text)
    no_dupes(a.text, [c.text for c in st0.children], "step")
    for t in a.text:                         # insert() re-parses, so look the stage up again each time
        s = next(x for x in f.stages if x.text == name)
        f.insert((s.children[-1].line if s.children else s.line) + 1, "  - [ ] " + t)
    save(reg, p, f)
    print("added under %s: %s" % (name, " · ".join(a.text)))


def cmd_idea(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    (a.text,) = clean([a.text])
    if a.top:
        no_dupes([a.text], [s.text for s in f.stages], "stage")
        last = f.stages[-1]
        at = (last.children[-1].line if last.children else last.line) + 1
        f.insert(at, "- [?] " + a.text)
        where = "top level"
    else:
        s = find(f, a.stage, stages_only=True) if a.stage else f.current()
        if not s:
            die("no current stage; use --stage or --top")
        no_dupes([a.text], [c.text for c in s.children], "step")
        at = (s.children[-1].line if s.children else s.line) + 1
        f.insert(at, "  - [?] " + a.text)
        where = s.text
    save(reg, p, f)
    print("idea (%s): %s" % (where, a.text))


def cmd_stage_next(a, reg):
    p = get(reg, a.id)
    f = flow_of(p["id"])
    nxt = f.advance()
    flash(p, "stage complete → " + (nxt.text if nxt else "all done"))
    save(reg, p, f)
    print("now: " + (nxt.text if nxt else "all stages done"))


def cmd_block(a, reg):
    p = get(reg, a.id)
    p.setdefault("blockers", []).append({"text": a.text, "since": core.now_iso()})
    save(reg, p)
    print("⛔ " + a.text)


def cmd_unblock(a, reg):
    p = get(reg, a.id)
    bl = p.get("blockers", [])
    if not bl:
        die("%s has no blockers" % p["id"])
    if a.n is None and len(bl) > 1:
        die("several blockers, pick one:\n" + "\n".join("  %d. %s" % (i, b["text"]) for i, b in enumerate(bl, 1)))
    n = a.n or 1
    if not 1 <= n <= len(bl):
        die("blocker number must be 1..%d" % len(bl))
    b = bl.pop(n - 1)
    save(reg, p)
    print("unblocked: " + b["text"])


# ---------------------------------------------------------------- default project id

ID_CMDS = {"show", "flow", "start", "tick", "untick", "idea", "block", "unblock"}   # `use` always needs an id
SUBCMDS = {"step": {"add", "rm", "rename", "mv"}, "stage": {"next", "add", "rm", "rename", "mv"}}


def gone(pid):
    die("project '%s' was deleted on the board, so it is no longer tracked — nothing changed. "
        "Don't re-add it unless the user asks (they can undo in the board with u)." % pid)


def default_id(reg):
    """Project for this shell: the current Claude session's project, else the unique folder/branch match."""
    st = core.load_status()
    sid = this_session(st)
    s = st["sessions"].get(sid, {}) if sid else {}
    pid = s.get("project_id")
    ids = [p["id"] for p in reg["projects"]]
    if pid in ids:
        return pid
    cwd = os.getcwd()
    pid, cands = core.resolve(cwd, core.read_branch(cwd), reg["projects"])
    if pid != "unassigned":
        return pid
    if s.get("removed"):
        gone(s["removed"])
    die("which project? this folder matches %s — pass the id first (have: %s)" % (
        " / ".join(cands) if cands else "none", ", ".join(ids) or "none"))


VALUE_OPTS = {"--after", "--before", "--branch", "--path", "--stage", "--stages", "--to"}   # flags that take a value


def with_default_id(argv, reg):
    """Let the project id be left out: `proj tick "SVI"` → `proj tick <id> "SVI"`."""
    if argv[:1] and argv[0] in ID_CMDS:
        pos = 1
    elif argv[:1] in (["step"], ["stage"]) and len(argv) > 1 and argv[1] in SUBCMDS[argv[0]]:
        pos = 2
    else:
        return argv
    ids = {p["id"] for p in reg["projects"]}
    if any(a in ("-h", "--help") for a in argv[pos:]):
        return argv
    i = pos                                  # first positional after the command, skipping flags
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in VALUE_OPTS else 1
    if i < len(argv) and argv[i] in ids:     # id given (possibly after a flag): move it into place
        return argv[:pos] + [argv[i]] + argv[pos:i] + argv[i + 1:]
    if i < len(argv) and argv[i] in core.deleted_ids():
        gone(argv[i])
    pid = default_id(reg)
    print("[%s]" % pid, file=sys.stderr)
    return argv[:pos] + [pid] + argv[pos:]


# ---------------------------------------------------------------- argparse

def main():
    ap = argparse.ArgumentParser(prog="proj", description=__doc__,
                                 epilog="Steps/stages match by unique case-insensitive substring.")
    sp = ap.add_subparsers(dest="cmd", metavar="command")
    sp.required = True

    def cmd(name, fn, help_, *args):
        c = sp.add_parser(name, help=help_)
        for a in args:
            c.add_argument(*a[0], **a[1])
        c.set_defaults(fn=fn)
        return c

    ID = (["id"], {})
    cmd("list", cmd_list, "list projects")
    sp.add_parser("doctor", help="check the install (PATH, hooks, skill, data)")
    cmd("new", cmd_new, "add a project interactively")
    cmd("add", cmd_add, "add a project", ID, (["name"], {}), (["--path"], {"required": True}),
        (["--branch"], {}), (["--stages"], {"required": True, "help": 'e.g. "A,B,C"'}))
    cmd("show", cmd_show, "show a project's flow", ID,
        (["--focus"], {"action": "store_true", "help": "current stage + next 2 (≤8 lines)"}))
    cmd("flow", cmd_flow, "print flow file path", ID)
    cmd("use", cmd_use, "assign this Claude Code session to a project", ID)
    cmd("start", cmd_start, "mark a step active for this session", ID, (["step"], {}))
    cmd("tick", cmd_tick, "mark step(s) done", ID, (["step"], {"nargs": "+"}))
    cmd("untick", cmd_untick, "mark step(s) not done", ID, (["step"], {"nargs": "+"}))
    cmd("idea", cmd_idea, "add a [?] idea (default: under current stage)", ID, (["text"], {}),
        (["--stage"], {}), (["--top"], {"action": "store_true", "help": "as a top-level stage idea"}))
    cmd("block", cmd_block, "add a blocker", ID, (["text"], {}))
    cmd("unblock", cmd_unblock, "remove blocker n (1-based)", ID, (["n"], {"type": int, "nargs": "?"}))

    def sub(parent, name, fn, help_, *args):
        c = parent.add_parser(name, help=help_)
        c.add_argument("id")
        for a in args:
            c.add_argument(*a[0], **a[1])
        c.set_defaults(fn=fn)

    POS = (["pos"], {"nargs": "?", "choices": ["up", "down", "first", "last"]})
    BEFORE, AFTER = (["--before"], {"metavar": "FRAG"}), (["--after"], {"metavar": "FRAG"})

    step = sp.add_parser("step", help="step add|rm|rename|mv [id] … (sub-steps)")
    ssp = step.add_subparsers(dest="sub", metavar="add|rm|rename|mv")
    ssp.required = True
    sub(ssp, "add", cmd_step_add, "add sub-step(s) to a stage, in order", (["stage"], {}), (["text"], {"nargs": "+"}))
    sub(ssp, "rm", cmd_step_rm, "delete sub-step(s)", (["step"], {"nargs": "+"}))
    sub(ssp, "rename", cmd_step_rename, "change a sub-step's text", (["step"], {}), (["text"], {}))
    sub(ssp, "mv", cmd_step_mv, "move a sub-step", (["step"], {}), POS, BEFORE, AFTER,
        (["--to"], {"metavar": "STAGE", "help": "end of another stage"}))

    stage = sp.add_parser("stage", help="stage next|add|rm|rename|mv [id] …")
    tsp = stage.add_subparsers(dest="sub", metavar="next|add|rm|rename|mv")
    tsp.required = True
    sub(tsp, "next", cmd_stage_next, "mark current stage done, advance to the next")
    sub(tsp, "add", cmd_stage_add, "add stage(s) (default: at the end)", (["text"], {"nargs": "+"}), BEFORE, AFTER)
    sub(tsp, "rm", cmd_stage_rm, "delete a stage and its sub-steps", (["stage"], {}))
    sub(tsp, "rename", cmd_stage_rename, "change a stage's text", (["stage"], {}), (["text"], {}))
    sub(tsp, "mv", cmd_stage_mv, "move a stage (with its sub-steps)", (["stage"], {}), POS, BEFORE, AFTER)

    if sys.argv[1:2] == ["doctor"]:            # needs no lock and must work with broken data
        sys.exit(doctor())
    if sys.argv[1:2] == ["--version"]:
        print("claude-project-board", core.VERSION)
        return
    try:
        a = ap.parse_args(with_default_id(sys.argv[1:], core.load_projects()))
        os.makedirs(core.FLOWS, exist_ok=True)
        if a.fn is cmd_new:
            return cmd_new(a)
        with core.locked():
            a.fn(a, core.load_projects())
    except core.DataError as e:              # broken data file: stop, change nothing
        die("%s\nnothing changed — fix that file first (data folder: %s)" % (e, core.short(core.HOME)))


if __name__ == "__main__":
    main()
