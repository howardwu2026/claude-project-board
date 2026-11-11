"""Shared helpers for the project board: storage, flow files, matching, focus view.

Kept import-light (no curses, no subprocess) so hook.py starts fast.
"""
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import time
import unicodedata

VERSION = "0.1.0"
CODE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_HOME = "~/.project_progress"                     # your data; the code folder holds none of it
HOME = PROJECTS = STATUS = FLOWS = LOCK = TRASH = ""     # set by set_home()


def set_home(path=None):
    """Point every data path at one folder: $PROJECT_PROGRESS_HOME, else ~/.project_progress.
    Tests use a temp folder, so they never touch real data."""
    global HOME, PROJECTS, STATUS, FLOWS, LOCK, TRASH
    HOME = os.path.abspath(os.path.expanduser(path or os.environ.get("PROJECT_PROGRESS_HOME") or DEFAULT_HOME))
    PROJECTS = os.path.join(HOME, "projects.json")
    STATUS = os.path.join(HOME, "status.json")
    FLOWS = os.path.join(HOME, "flows")
    LOCK = os.path.join(HOME, ".lock")
    TRASH = os.path.join(FLOWS, ".trash")


set_home()

# Claude Code hooks (install.py writes them; proj doctor checks them)
HOOK_MARK = "claude-project-board"
HOOK_EVENTS = {  # event -> matcher (None = no matcher key)
    "SessionStart": None, "UserPromptSubmit": None, "PreToolUse": "*", "PostToolUse": "*",
    "PermissionRequest": "*", "Notification": "permission_prompt|elicitation_dialog",
    "Stop": None, "StopFailure": None, "SessionEnd": None,
}


def is_our_hook(h):
    """One of our hook entries — tagged with HOOK_MARK (or the pre-0.1 ~/.project_progress/hook.py)."""
    cmd = (h.get("command") or "") if isinstance(h, dict) else ""
    return HOOK_MARK in cmd or cmd.rstrip().endswith("/.project_progress/hook.py")


DONE, CUR, TODO, IDEA = "x", ">", " ", "?"
ITEM_RE = re.compile(r"^(\s*)[-*] \[([ xX>?])\] (.*?)\s*$")


# ---------------------------------------------------------------- storage

def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def days_since(iso):
    try:
        return (dt.datetime.now() - dt.datetime.fromisoformat(iso)).days
    except (TypeError, ValueError):
        return 0


def iso_to_epoch(iso):
    try:
        return dt.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return 0.0


class DataError(Exception):
    """A data file exists but can't be used (bad JSON, wrong shape, not UTF-8).
    Callers stop instead of writing, so a broken file is never overwritten with defaults."""


def short(path):
    return path.replace(os.path.expanduser("~"), "~", 1)


def read_json(path, default):
    """Lenient: any problem → default. Only for files that are safe to lose (board prefs)."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_json_strict(path, default):
    """Missing file → default. Unreadable or invalid → DataError."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return default
    except (OSError, UnicodeDecodeError) as e:
        raise DataError("can't read %s: %s" % (short(path), getattr(e, "strerror", None) or e))
    try:
        return json.loads(text)
    except ValueError as e:
        raise DataError("%s is not valid JSON (%s)" % (short(path), e))


def write_atomic(path, text):
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_json(path, data):
    write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def locked(timeout=None):
    """Exclusive lock around read-modify-write of projects.json / status.json / flow files.
    Yields True when the lock is held. With a timeout (seconds) it may yield False: the
    caller must then not write (the hook skips that one update rather than race a writer)."""
    os.makedirs(FLOWS, exist_ok=True)            # first run: create the data folder
    with open(LOCK, "a") as f:
        end = None if timeout is None else time.monotonic() + timeout
        got = False
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if end is None else fcntl.LOCK_NB))
                got = True
                break
            except OSError:
                if time.monotonic() > end:
                    break
                time.sleep(0.005)
        try:
            yield got
        finally:
            if got:
                fcntl.flock(f, fcntl.LOCK_UN)


ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_projects():
    """The registry, checked. Raises DataError instead of guessing, so nothing overwrites it."""
    reg = read_json_strict(PROJECTS, {"projects": []})
    ps = reg.get("projects") if isinstance(reg, dict) else None
    if not isinstance(ps, list):
        raise DataError('%s: expected {"projects": [...]} at the top' % short(PROJECTS))
    seen = set()
    for i, p in enumerate(ps, 1):
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or not ID_RE.match(pid):
            raise DataError("%s: project #%d has no valid \"id\"" % (short(PROJECTS), i))
        if pid in seen:
            raise DataError("%s: id '%s' appears twice" % (short(PROJECTS), pid))
        seen.add(pid)
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            p["name"] = pid
        if not isinstance(p.get("blockers"), list):
            p["blockers"] = []
        p["blockers"] = [b for b in p["blockers"] if isinstance(b, dict) and isinstance(b.get("text"), str)]
    return reg


def load_status():
    """Live session state. It is rebuilt by the hooks anyway, so a broken file is set aside
    (status.json.bad-<time>) and replaced rather than stopping anything."""
    try:
        st = read_json_strict(STATUS, {})
    except DataError:
        st = None
    if not isinstance(st, dict):
        with contextlib.suppress(OSError):
            os.replace(STATUS, "%s.bad-%d" % (STATUS, time.time()))
        st = {}
    if not isinstance(st.get("sessions"), dict):
        st["sessions"] = {}
    st["sessions"] = {k: v for k, v in st["sessions"].items() if isinstance(v, dict)}
    if not isinstance(st.get("last_active"), dict):
        st["last_active"] = {}
    return st


def flow_path(pid):
    return os.path.join(FLOWS, pid + ".md")


# ---------------------------------------------------------------- flow files

class Item:
    def __init__(self, line, indent, mark, text):
        self.line, self.indent, self.mark, self.text = line, indent, mark, text
        self.children = []  # only for stages (top-level items)

    def __repr__(self):
        return "[%s] %s" % (self.mark, self.text)


class Flow:
    """A flow file kept as raw lines; edits rewrite single lines in place."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.lines = f.read().splitlines()
        except UnicodeDecodeError:
            raise DataError("%s is not UTF-8 text" % short(path))
        self.parse()

    def parse(self):
        self.stages = []
        for i, ln in enumerate(self.lines):
            m = ITEM_RE.match(ln)
            if not m:
                continue
            ind, mark, text = len(m.group(1).expandtabs(2)), m.group(2).lower(), m.group(3)
            it = Item(i, ind, mark, text)
            if ind == 0:
                self.stages.append(it)
            elif self.stages:
                self.stages[-1].children.append(it)

    def save(self):
        write_atomic(self.path, "\n".join(self.lines) + "\n")

    def set_mark(self, it, mark):
        ln = self.lines[it.line]
        self.lines[it.line] = re.sub(r"\[[ xX>?]\]", "[%s]" % mark, ln, count=1)
        it.mark = mark

    def insert(self, at, text):
        self.lines.insert(at, text)
        self.parse()

    # --- structural edits (stages carry their sub-steps; trailing notes stay put)
    def span(self, it):
        """Line range [a, b) an item occupies: a sub-step is one line, a stage runs to the next stage
        (the last stage ends after its last sub-step, so the footer comment never moves)."""
        if it.indent:
            return it.line, it.line + 1
        i = next(n for n, s in enumerate(self.stages) if s is it)
        if i + 1 < len(self.stages):
            return it.line, self.stages[i + 1].line
        return it.line, (it.children[-1].line if it.children else it.line) + 1

    def end_of_stages(self):
        last = self.stages[-1] if self.stages else None
        return self.span(last)[1] if last else len(self.lines)

    def remove(self, it):
        a, b = self.span(it)
        del self.lines[a:b]
        self.parse()
        self.fix_current()

    def rename(self, it, text):
        self.lines[it.line] = re.sub(r"(\[[ xX>?]\] ).*$", lambda m: m.group(1) + text, self.lines[it.line], count=1)
        it.text = text

    def move(self, it, to):
        """Move an item's lines so they start at line `to` (counted before the move)."""
        a, b = self.span(it)
        if a <= to <= b:
            return
        chunk = self.lines[a:b]
        del self.lines[a:b]
        if to > a:
            to -= b - a
        self.lines[to:to] = chunk
        self.parse()

    def fix_current(self):
        """Keep exactly one [>] stage: if none is left, the first unfinished real stage becomes current."""
        if not self.current():
            nxt = next((s for s in self.real_stages() if s.mark != DONE), None)
            if nxt:
                self.set_mark(nxt, CUR)

    # --- queries
    def real_stages(self):
        return [s for s in self.stages if s.mark != IDEA]

    def current(self):
        return next((s for s in self.stages if s.mark == CUR), None)

    def next_stages(self, n=2):
        cur = self.current()
        rs = self.real_stages()
        if cur not in rs:
            return [s for s in rs if s.mark != DONE][:n]
        i = rs.index(cur)
        return rs[i + 1:i + 1 + n]

    def progress(self):
        rs = self.real_stages()
        done = sum(1 for s in rs if s.mark == DONE)
        return done, len(rs)

    def next_step(self):
        """First [>] sub-step of the current stage, else first [ ], else next stage."""
        cur = self.current()
        if cur:
            for mark in (CUR, TODO):
                for c in cur.children:
                    if c.mark == mark:
                        return c.text
        nxt = self.next_stages(1)
        return ("stage: " + nxt[0].text) if nxt else None

    # --- matching
    def find(self, query, stages_only=False, steps_only=False):
        """Unique (case-insensitive) substring match; exact match wins.
        Sub-steps are searched first, then stages. "Stage frag::step frag" looks only inside
        that stage (for a step name used in two stages). Raises ValueError."""
        q = query.lower().strip()
        if "::" in q and not stages_only and not any(c.text.lower() == q for s in self.stages for c in s.children):
            sq, q = (x.strip() for x in q.split("::", 1))
            st = self.find(sq, stages_only=True)
            pools = [st.children]
        else:
            subs = [c for s in self.stages for c in s.children]
            pools = [self.stages] if stages_only else [subs] if steps_only else [subs, self.stages]
        for pool in pools:
            exact = [it for it in pool if it.text.lower() == q]
            if len(exact) == 1:
                return exact[0]
            hits = exact or [it for it in pool if q in it.text.lower()]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ValueError("'%s' is ambiguous — use a longer fragment%s:\n  " % (
                    query, " or \"Stage::step\"" if any(h.indent for h in hits) else "")
                    + "\n  ".join(self.label(h) for h in hits))
        raise ValueError("no %s matches '%s'" % ("stage" if stages_only else "sub-step" if steps_only else "step", query))

    def label(self, it):
        st = self.stage_of(it)
        return it.text if it is st or not st else "%s :: %s" % (st.text, it.text)

    def ref(self, it):
        """(stage text, nth stage with that text, step text or None, nth such step) — survives
        duplicate names, unlike plain text."""
        st = self.stage_of(it)
        sn = [s for s in self.stages if s.text == st.text].index(st)
        if it is st:
            return (st.text, sn, None, 0)
        return (st.text, sn, it.text, [c for c in st.children if c.text == it.text].index(it))

    def locate(self, ref):
        stext, sn, ctext, cn = ref
        ss = [s for s in self.stages if s.text == stext]
        if sn >= len(ss):
            return None
        if ctext is None:
            return ss[sn]
        cs = [c for c in ss[sn].children if c.text == ctext]
        return cs[cn] if cn < len(cs) else None

    def stage_of(self, it):
        for s in self.stages:
            if it is s or it in s.children:
                return s
        return None

    # --- transitions
    def advance(self):
        """Mark current stage [x] and the next real stage [>]. Returns new stage or None."""
        cur = self.current()
        rs = self.real_stages()
        if cur is None:
            nxt = next((s for s in rs if s.mark != DONE), None)
        else:
            self.set_mark(cur, DONE)
            i = rs.index(cur) if cur in rs else -1
            nxt = next((s for s in rs[i + 1:] if s.mark != DONE), None)
        if nxt:
            self.set_mark(nxt, CUR)
        return nxt

    def stage_complete(self):
        cur = self.current()
        if not cur:
            return False
        work = [c for c in cur.children if c.mark != IDEA]
        return bool(work) and all(c.mark == DONE for c in work)


def tick_item(p, f, it):
    """Mark item done; auto-advance the stage when its sub-steps are all done. -> new stage or None."""
    st = f.stage_of(it)
    if it.indent == 0:                       # ticking a stage itself
        if it.mark != CUR:
            f.set_mark(it, DONE)
            return None
        return f.advance()
    f.set_mark(it, DONE)
    p["last_tick"] = {"text": it.text, "at": now_iso()}
    if st is f.current() and f.stage_complete():
        return f.advance()
    return None


def untick_item(f, it):
    f.set_mark(it, TODO)
    if it.indent == 0:
        f.fix_current()


def clear_active(pid, texts):
    """Sessions that were working on these steps aren't any more."""
    st = load_status()
    hit = False
    for s in st["sessions"].values():
        if s.get("project_id") == pid and s.get("active_step") in texts:
            s["active_step"] = None
            hit = True
    if hit:
        write_json(STATUS, st)


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _board_write(pid, ref, change):
    """Run change(p, f, it) -> (message, changed) on one item under the lock.
    -> (message, undo record or None). The undo record holds the file before and after."""
    with locked():
        reg = load_projects()
        p = next((x for x in reg["projects"] if x["id"] == pid), None)
        if not p:
            return "project gone", None
        path = flow_path(pid)
        before = _read_text(path)
        f = Flow(path)
        it = f.locate(ref)
        if not it:
            return "step not found (file changed?)", None
        msg, changed = change(p, f, it)
        if not changed:
            return msg, None
        f.save()
        p["updated_at"] = now_iso()
        write_json(PROJECTS, reg)
        return msg, {"pid": pid, "before": before, "after": _read_text(path), "what": msg}


def delete_project(pid):
    """Board edit mode x ⏎: drop a project from the registry; its flow file moves to flows/.trash.
    -> (message, undo record)."""
    with locked():
        reg = load_projects()
        i = next((i for i, x in enumerate(reg["projects"]) if x["id"] == pid), None)
        if i is None:
            return "project gone", None
        p = reg["projects"].pop(i)
        src, trash = flow_path(pid), None
        if os.path.exists(src):
            os.makedirs(TRASH, exist_ok=True)
            trash = os.path.join(TRASH, "%s-%s.md" % (pid, time.strftime("%Y%m%d-%H%M%S")))
            os.replace(src, trash)
        try:
            write_json(PROJECTS, reg)
        except BaseException:                # registry not written: put the flow file back
            if trash:
                os.replace(trash, src)
            raise
        msg = "✗ deleted project: %s%s" % (p["name"], "  (flow → flows/.trash)" if trash else "")
        return msg, {"kind": "project", "pid": pid, "index": i, "entry": p, "trash": trash, "what": msg,
                     "status": _detach_sessions(pid)}


SESSION_LINK = ("project_id", "assigned", "active_step")


def _detach_sessions(pid):
    """A deleted project stops being tracked: its sessions become unassigned (marked `removed` so the hook
    and proj can say why) and its activity time goes. -> what to put back on undo."""
    try:
        st = load_status()
        saved = {"last_active": st["last_active"].pop(pid, None), "sessions": {}}
        for sid, s in st["sessions"].items():
            if pid in (s.get("project_id"), s.get("assigned")):
                saved["sessions"][sid] = {k: s.get(k) for k in SESSION_LINK}
                s.pop("assigned", None)
                s.update(project_id="unassigned", candidates=[], active_step=None, removed=pid)
        write_json(STATUS, st)
        return saved
    except OSError:                          # status is rebuilt by the hooks anyway
        return None


def _reattach_sessions(pid, saved):
    if not saved:
        return
    try:
        st = load_status()
        if saved.get("last_active") is not None:
            st["last_active"].setdefault(pid, saved["last_active"])
        for sid, old in saved.get("sessions", {}).items():
            s = st["sessions"].get(sid)
            if s and s.get("removed") == pid:
                s.pop("removed", None)
                s.update({k: v for k, v in old.items() if v is not None})
        write_json(STATUS, st)
    except OSError:
        pass


def purge_deleted(u):
    """The board is closing or reloading, so `u` can no longer bring this project back: delete its trashed
    flow file for good. Its sessions keep their `removed` note (tells Claude not to re-add it) until they end.
    -> True if a file was deleted."""
    with locked():
        if any(p["id"] == u["pid"] for p in load_projects()["projects"]):
            return False                       # restored (or re-added) — nothing to purge
        path = u.get("trash")
        if path and os.path.dirname(os.path.abspath(path)) == os.path.abspath(TRASH) and os.path.exists(path):
            os.remove(path)
            return True
    return False


def deleted_ids():
    """Ids of projects deleted on the board (their flow files sit in flows/.trash), minus live ones."""
    try:
        names = os.listdir(TRASH)
    except OSError:
        return set()
    out = {n[:-3].rsplit("-", 2)[0] for n in names if n.endswith(".md") and n.count("-") >= 2}
    try:
        live = {p["id"] for p in load_projects()["projects"]}
    except DataError:
        live = set()
    return out - live


def _undo_delete(u):
    reg = load_projects()
    if any(x["id"] == u["pid"] for x in reg["projects"]):
        return "can't undo: a project '%s' exists again" % u["pid"]
    dst = flow_path(u["pid"])
    if u["trash"]:
        if os.path.exists(dst):
            return "can't undo: %s exists again" % short(dst)
        if not os.path.exists(u["trash"]):
            return "can't undo: the trashed flow file is gone"
        os.replace(u["trash"], dst)
    reg["projects"].insert(min(u["index"], len(reg["projects"])), u["entry"])
    write_json(PROJECTS, reg)
    _reattach_sessions(u["pid"], u.get("status"))
    return "↶ restored project: " + u["entry"]["name"]


def undo_write(u):
    """Take back one board edit — only if nothing touched that file since."""
    if u.get("kind") == "project":
        with locked():
            return _undo_delete(u)
    with locked():
        path = flow_path(u["pid"])
        try:
            cur = _read_text(path)
        except (OSError, UnicodeDecodeError):
            return "can't undo: the flow file is gone"
        if cur != u["after"]:
            return "can't undo: the flow file changed since (proj, Claude or an editor)"
        write_atomic(path, u["before"])
        reg = load_projects()
        p = next((x for x in reg["projects"] if x["id"] == u["pid"]), None)
        if p:
            p["updated_at"] = now_iso()
            p.pop("flash", None)
            write_json(PROJECTS, reg)
        return "↶ undone: " + u["what"]


def toggle_done(pid, ref):
    """Board ⏎⏎: tick an open item / untick a done one. -> (message, undo record)."""
    def change(p, f, it):
        if it.mark == DONE:
            untick_item(f, it)
            return "· unticked: " + it.text, True
        nxt = tick_item(p, f, it)
        msg = "✓ " + it.text
        if nxt:
            p["flash"] = {"text": "stage complete → " + nxt.text, "at": now_iso()}
            msg += "   stage complete → " + nxt.text
        clear_active(pid, {it.text})
        return msg, True
    return _board_write(pid, ref, change)


STATUS_WORD = {TODO: "○ open", CUR: "▶ doing", DONE: "✓ done", IDEA: "? idea"}


def set_status(pid, ref, mark):
    """Board edit mode (e then o/p/i): set one item's marker. -> (message, undo record).
    ▶ on a stage or a sub-step also makes that stage the one current stage (the old one goes back to ○).
    ✓ goes through tick_item, so finishing the last sub-step still advances the stage."""
    def change(p, f, it):
        st = f.stage_of(it)
        if it.mark == mark and not (mark == CUR and it is not st and st.mark != CUR):
            return "· already %s: %s" % (STATUS_WORD[mark], it.text), False
        msg = "%s: %s" % (STATUS_WORD[mark], it.text)
        if mark == DONE:
            nxt = tick_item(p, f, it)
            if nxt:
                p["flash"] = {"text": "stage complete → " + nxt.text, "at": now_iso()}
                msg += "   stage complete → " + nxt.text
        elif mark == CUR:
            for s in f.stages:
                if s.mark == CUR and s is not st:
                    f.set_mark(s, TODO)
            f.set_mark(st, CUR)
            f.set_mark(it, CUR)
        else:
            f.set_mark(it, mark)
            if it is st and not f.current():
                msg += "   (no current stage now — p on one to start it)"
        if mark != CUR:
            clear_active(pid, {it.text})
        return msg, True
    return _board_write(pid, ref, change)


def new_flow_text(name, stages):
    out = ["# " + name, ""]
    for i, s in enumerate(stages):
        out.append("- [%s] %s" % (CUR if i == 0 else TODO, s.strip()))
    out += ["", "<!-- markers: [x] done  [>] current  [ ] todo  [?] idea -->"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- matching

def read_branch(cwd):
    """Current git branch by reading .git/HEAD directly (fast; no subprocess)."""
    d = os.path.realpath(cwd)
    while True:
        g = os.path.join(d, ".git")
        if os.path.isdir(g):
            head = os.path.join(g, "HEAD")
            break
        if os.path.isfile(g):  # worktree / submodule: "gitdir: <path>"
            with open(g) as f:
                gd = f.read().split("gitdir:", 1)[-1].strip()
            head = os.path.join(os.path.join(d, gd) if not os.path.isabs(gd) else gd, "HEAD")
            break
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent
    try:
        with open(head) as f:
            ref = f.read().strip()
    except OSError:
        return ""
    return ref[len("ref: refs/heads/"):] if ref.startswith("ref: refs/heads/") else ""


def candidates(cwd, branch, projects):
    """Projects whose rule matches, most specific first-tier only."""
    cwd = os.path.realpath(cwd)
    best, out = None, []
    for p in projects:
        m = p.get("match", {})
        path = os.path.realpath(os.path.expanduser(m.get("path", "")))
        if not path or not (cwd == path or cwd.startswith(path.rstrip("/") + "/")):
            continue
        if m.get("branch") and m["branch"] != branch:
            continue
        score = (len(path), bool(m.get("branch")))
        if best is None or score > best:
            best, out = score, [p["id"]]
        elif score == best:
            out.append(p["id"])
    return out


def resolve(cwd, branch, projects, assigned=None):
    """-> (project_id or 'unassigned', candidate ids)."""
    ids = {p["id"] for p in projects}
    if assigned in ids:
        return assigned, [assigned]
    c = candidates(cwd, branch, projects)
    return (c[0] if len(c) == 1 else "unassigned"), c


# ---------------------------------------------------------------- display helpers

def dw(s):
    """Display width (CJK = 2 cells)."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 0 if unicodedata.combining(ch) else 1
               for ch in s)


def clip(s, width):
    if width <= 0:
        return ""
    if dw(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = dw(ch)
        if w + cw > width - 1:
            break
        out, w = out + ch, w + cw
    return out + "…"


def wrap(s, width):
    """Greedy width-aware wrap (breaks at spaces, hard-splits long words / CJK)."""
    if width < 2:
        return [s]
    lines, cur = [], ""
    for word in re.split(r"(\s+)", s):
        if dw(cur + word) <= width:
            cur += word
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        cur = word.lstrip()
        while dw(cur) > width:
            part = clip(cur, width + 1)[:-1] or cur[0]  # largest prefix that fits
            lines.append(part)
            cur = cur[len(part):]
    if cur.strip() or not lines:
        lines.append(cur.rstrip())
    return lines


SYM = {DONE: "✓", CUR: "▶", TODO: "·", IDEA: "?"}


def focus_lines(p, flow, limit=8):
    """Compact current-stage view, at most `limit` lines."""
    done, total = flow.progress()
    cur = flow.current()
    head = "[%s] %s — stage %d/%d" % (p["id"], p["name"], min(done + 1, total), total)
    blockers = p.get("blockers", [])
    if not cur:
        out = [head, "no current stage (all done?)"]
    else:
        open_ = [c for c in cur.children if c.mark != DONE]
        ndone = len(cur.children) - len(open_)
        room = limit - 2 - (1 if flow.next_stages() else 0) - min(len(blockers), 2)
        shown = open_[:max(room, 0)]
        more = len(open_) - len(shown)
        count = "  (%d/%d done%s)" % (ndone, len(cur.children), ", +%d more" % more if more else "")
        out = [head, "▶ " + cur.text + (count if cur.children else "  (no sub-steps yet)")]
        out += ["   %s %s" % (SYM[c.mark], c.text) for c in shown]
    nxt = flow.next_stages()
    if nxt:
        out.append("Next: " + " → ".join(s.text for s in nxt))
    for b in blockers[:2]:
        out.append("⛔ %s (%dd)" % (b["text"], days_since(b["since"])))
    return out[:limit]
