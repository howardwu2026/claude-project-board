#!/usr/bin/env python3
"""board — live terminal dashboard for the project board (curses; data in ~/.project_progress).

Every project is a column. Its stages run down the column, and each stage folds
open to show its sub-steps. Working projects sit left-most (newest turn first);
the further right, the longer a project has been untouched.

Keys: ←/→  pick a project (column)        ↑/↓  move one row (stage or sub-step)
      Enter  1st press: the row turns into a green ○ (pending) · 2nd press: tick it (or untick a ✓)
             any other key cancels the pending tick
      Space  open or close the selected stage
      z      overview (all stages closed) ⇄ zoom (all open)
      m      minimize: only each project's status (name, progress, session); m again shows the steps
      v      fit the window's height to the longest column shown (no blank rows below)
      h      fit the width: the set number of columns, each as wide as its longest line (≤ 42); height follows
      e      edit mode (stays on until Esc or e): move with the arrows, then press
             o ○ open/todo   p ▶ doing (its stage becomes the current one)   ? idea   (✓ = ⏎⏎)
             x  delete the selected project — asks first, ⏎ confirms; u brings it back until you
                quit or reload (s r) — then it is gone for good
      u      undo the last board edit (⏎⏎ / o / p / i / delete), as long as nothing else changed it since
      Esc    leave edit mode / cancel / back to the full board        ?  all keys        q  quit
      s    shortcut key: press s, let go, then press one of
             ↑ ↓  previous / next stage          ← →  first (newest) / last project
             t b  top / bottom of the column     o    this project on its own ⇄ back to the board
             1…9  show at most that many projects side by side (back to 4 on reload / next launch)
             r    reload (restart: new code, fresh data, same view)
      Also: ⇧↑/⇧↓ PgUp/PgDn { } (stage), Home/End (top/bottom). Mouse wheel / trackpad is ignored.
      Adding, removing, renaming and reordering stages/steps is done with `proj` (ask Claude Code).
Testing: board --dump [--size 120x40] [--fold focus|overview|zoom] [--sel ID] [--solo] [--min] [--cols N]
"""
import argparse
import curses
import locale
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import core  # noqa: E402
from core import CUR, DONE, IDEA, TODO, clip, dw, wrap  # noqa: E402

SPIN = "◐◓◑◒"
ORDER = {"waiting_approval": 0, "processing": 1, "done": 2, "idle": 3}
TICK_FLASH = 2          # seconds a freshly ticked step stays highlighted
MIN_COL = 24            # narrowest column; fewer columns fit → scroll sideways
MAX_COLS = 4            # projects shown at once on every launch (s + 1…9 changes it until you quit); the rest wait to the right
SOLO_W = 72             # width of the single-project window
STAGE_SYM = {DONE: ("✓", "green"), CUR: ("▶", "cyan b"), TODO: ("○", ""), IDEA: ("?", "yellow b")}
STAGE_TXT = {DONE: "d", CUR: "b", TODO: "", IDEA: "yellow"}
FOLD_LABEL = {"focus": "focus", "overview": "overview", "zoom": "zoom"}


# ---------------------------------------------------------------- data

class Data:
    """Re-reads files only when their mtime changes."""

    def __init__(self):
        self.mt, self.flows, self.flow_err = {}, {}, {}
        self.reg, self.st, self.err = {"projects": []}, core.load_status(), None

    def _changed(self, path):
        try:
            m = os.stat(path).st_mtime_ns
        except OSError:
            m = None
        changed = self.mt.get(path, -1) != m
        self.mt[path] = m
        return changed

    def refresh(self, force=False):
        if force:
            self.mt.clear()
        if self._changed(core.PROJECTS):
            try:
                self.reg, self.err = core.load_projects(), None
            except core.DataError as e:      # keep showing the last good registry
                self.err = str(e)
        if self._changed(core.STATUS):
            self.st = core.load_status()
        for p in self.reg["projects"]:
            fp = core.flow_path(p["id"])
            if self._changed(fp) or p["id"] not in self.flows:
                try:
                    self.flows[p["id"]], self.flow_err[p["id"]] = core.Flow(fp), None
                except FileNotFoundError:
                    self.flows[p["id"]], self.flow_err[p["id"]] = None, "flow file missing"
                except (OSError, core.DataError) as e:
                    self.flows[p["id"]], self.flow_err[p["id"]] = None, str(e)

    def sessions(self, pid):
        ss = [s for s in self.st["sessions"].values() if s.get("project_id") == pid]
        return sorted(ss, key=lambda s: (ORDER.get(s.get("state"), 9), -s.get("updated", 0)))

    def top(self, pid):
        ss = self.sessions(pid)
        return ss[0] if ss else None

    def recency(self, p):
        t = [core.iso_to_epoch(p.get("updated_at")), self.st["last_active"].get(p["id"], 0)]
        return max(t + [s.get("updated", 0) for s in self.sessions(p["id"])])

    def ordered(self):
        """Working projects first, newest turn left-most; then the rest, latest status change first.
        Keys only change when a session changes state (a turn starts, finishes, needs approval,
        ends) — never on tool calls or step edits — so columns stay put while you watch."""
        def key(p):
            ss = self.sessions(p["id"])
            busy = [s for s in ss if s.get("state") in ("processing", "waiting_approval")]
            if busy:
                return (0, -max(s.get("turn_at", s.get("since", 0)) for s in busy))
            if ss:
                return (1, -max(s.get("since", 0) for s in ss))
            return (1, -self.st["last_active"].get(p["id"], core.iso_to_epoch(p.get("updated_at"))))
        return sorted(self.reg["projects"], key=key)


# ---------------------------------------------------------------- formatting

def ago(sec):
    sec = int(max(sec, 0))
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm%02ds" % divmod(sec, 60)
    return "%dh%02dm" % (sec // 3600, sec % 3600 // 60)


def idle_for(sec):
    if sec >= 86400:
        return "%dd" % (sec // 86400)
    return "%dh" % (sec // 3600) if sec >= 3600 else "%dm" % (sec // 60)


def fresh(event, now, window):
    return bool(event) and now - core.iso_to_epoch(event.get("at")) < window


def active_step(d, p):
    s = d.top(p["id"])
    return s.get("active_step") if s and s.get("state") == "processing" else None


def live_segs(d, p, now):
    ss = d.sessions(p["id"])
    s = ss[0] if ss else None
    more = [(" +%d" % (len(ss) - 1), "d")] if len(ss) > 1 else []
    state = s.get("state") if s else None
    if state in (None, "idle"):
        return [("○ idle " + idle_for(now - d.recency(p)), "d")] + more
    el = " " + ago(now - s.get("since", now))
    if state == "processing":
        return [(SPIN[int(now) % 4] + " working", "cyan b"), (el, "d")] + more
    if state == "waiting_approval":
        return [("⚠ needs approval" if int(now) % 2 == 0 else "  needs approval", "yellow b"), (el, "d")] + more
    hhmm = time.strftime("%H:%M", time.localtime(s.get("since", now)))
    return [("✓ done %s, your turn" % hhmm, "green")] + more


def bar_segs(f):
    segs = []
    for s in f.real_stages():
        segs.append(("■", "green") if s.mark == DONE else ("■", "cyan b") if s.mark == CUR else ("□", "d"))
    done, n = f.progress()
    return segs + [(" %d/%d" % (done, n), "d")]


def blocker_segs(b):
    days = core.days_since(b["since"])
    return [("⛔ %s (%dd)" % (b["text"], days), "red b" if days > 3 else "yellow")]


def item_sym(it, now, p, active):
    """(symbol, style) for one sub-step."""
    lt = p.get("last_tick")
    if it.mark == DONE:
        flash = fresh(lt, now, TICK_FLASH) and lt.get("text") == it.text
        return "✓", "green r b" if flash else "green"
    if it.mark == CUR:
        return (SPIN[int(now) % 4], "cyan b") if active == it.text else ("▶", "cyan")
    if it.mark == IDEA:
        return "?", "yellow"
    return "·", ""


def put_segs(cv, y, x, segs, width):
    """Draw segments left to right from (y, x), clipped to width cells."""
    used = 0
    for text, style in segs:
        if width - used <= 0:
            break
        t = clip(text, width - used)
        cv.put(y, x + used, t, style)
        used += dw(t)


# ---------------------------------------------------------------- one column

def stage_open(ui, pid, st, sref=None):
    k = (pid, sref or (st.text, 0))
    if k in ui["open"]:
        return ui["open"][k]
    return {"zoom": True, "overview": False}.get(ui["fold"], st.mark == CUR)


def col_nodes(d, ui, p):
    """Visible rows of a column: every stage, plus the sub-steps of open stages."""
    f, out = d.flows.get(p["id"]), []
    for st in (f.stages if f else []):
        sref = f.ref(st)[:2]                  # (text, nth stage with that text): duplicate names stay apart
        op = bool(st.children) and stage_open(ui, p["id"], st, sref)
        out.append({"key": ("s",) + sref, "ref": f.ref(st), "it": st, "stage": st, "sref": sref, "open": op})
        if op:
            for c in st.children:
                r = f.ref(c)
                out.append({"key": ("c",) + r, "ref": r, "it": c, "stage": st, "sref": sref, "open": False})
    return out


def resolve_cur(d, ui, p, nodes):
    """Keep the column's cursor on a visible row (a hidden sub-step falls back to its stage)."""
    keys = [n["key"] for n in nodes]
    ck = ui["cur"].get(p["id"])
    if ck not in keys:
        f = d.flows.get(p["id"])
        cur = ("s",) + f.ref(f.current())[:2] if f and f.current() else None
        if ck and ck[0] == "c" and ("s",) + ck[1:3] in keys:
            ck = ("s",) + ck[1:3]
        elif cur in keys:
            ck = cur
        else:
            ck = keys[0] if keys else None
    ui["cur"][p["id"]] = ck
    return ck


def col_head(d, p, now, w, selected, deleting=False, edit=False):
    f = d.flows.get(p["id"])
    name = clip(" " + ("✗ " if deleting else "") + p["name"], w)
    lines = [[(name + " " * max(w - dw(name), 0), "red b r" if deleting else (EDIT_CURSOR if edit else "b r") if selected else "b")]]
    if f:
        lines.append([(" ", "")] + bar_segs(f))
    else:                                      # unreadable flow: say why, wrapped so it isn't cut off
        err = (d.flow_err.get(p["id"]) or "flow file missing").replace(core.FLOWS + os.sep, "")
        lines += [[(" ", ""), (ln, "red")] for ln in wrap(err, max(w - 1, 4))[:4]]
    lines.append([(" ", "")] + live_segs(d, p, now))
    lines += [[(" ", "")] + blocker_segs(b) for b in p.get("blockers", [])]
    return lines


EDIT_CURSOR = "yellow b r"                    # the cursor (and selected column name) in edit mode


def col_body(d, p, nodes, now, w, cur_key, show_cursor, pending=None, edit=False):
    """-> (rows of segments, (first, last+1) rows of the cursor)."""
    act, rows, span = active_step(d, p), [], (0, 0)
    for n in nodes:
        it, top = n["it"], len(rows)
        if n["key"] == pending:                     # ⏎ pressed once: waiting for the second ⏎
            text = it.text
            prefix = ([(" " * 3, ""), ("○ ", "green b")] if it is n["stage"] else [("     ", ""), ("○ ", "green b")])
            tstyle = "green b"
        elif it is n["stage"]:
            kids = [c.text for c in it.children]
            chev = ("▾ " if n["open"] else "▸ ") if kids else "  "
            sym, style = STAGE_SYM[it.mark]
            if not n["open"] and act in kids:      # work is happening inside a closed stage
                sym, style = SPIN[int(now) % 4], "cyan b"
            work = [c for c in it.children if c.mark != IDEA]
            text = it.text + ("  %d/%d" % (sum(c.mark == DONE for c in work), len(work)) if work else "")
            prefix, tstyle = [(" " + chev, "d"), (sym + " ", style)], STAGE_TXT[it.mark]
        else:
            sym, style = item_sym(it, now, p, act)
            text, prefix, tstyle = it.text, [("     ", ""), (sym + " ", style)], style
        pw = sum(dw(t) for t, _ in prefix)
        sel = show_cursor and n["key"] == cur_key
        for i, ln in enumerate(wrap(text, max(w - pw, 4))):
            style = (EDIT_CURSOR if edit else tstyle + " r") if sel else tstyle
            rows.append((prefix if i == 0 else [(" " * pw, "")]) + [(ln, style)])
        if n["key"] == cur_key:
            span = (top, len(rows))
    return rows, span


# ---------------------------------------------------------------- drawing

HELP = [
    ("Move", None),
    ("← →", "pick a project (column)"),
    ("↑ ↓", "one row (stage or sub-step)"),
    ("Space", "open / close the selected stage"),
    ("z", "overview (all closed) ⇄ zoom (all open)"),
    ("m", "minimize: only each project's status, no stages or steps (m again shows them)"),
    ("v", "fit the window's height to the longest column shown"),
    ("h", "fit the width: the set number of projects, each column as wide as its longest line; height follows"),
    ("⇧↑ ⇧↓  PgUp PgDn  { }", "previous / next stage"),
    ("Home End", "top / bottom of the column"),
    ("Change", None),
    ("⏎ ⏎", "tick ✓ (or untick); 1st ⏎ shows a green ○, any other key cancels"),
    ("e", "edit mode on / off (yellow cursor) — then:"),
    ("   o", "○ open (not started)"),
    ("   p", "▶ doing — its stage becomes the current one"),
    ("   ?", "? idea (not counted in progress)"),
    ("   x  then ⏎", "delete the selected project — u brings it back until you quit or s r, then it is gone for good"),
    ("u", "undo the last board change (if nothing else changed that file since)"),
    ("s, then …", None),
    ("↑ ↓", "previous / next stage"),
    ("← →", "first (newest) / last project"),
    ("t b", "top / bottom of the column"),
    ("o", "only this project ⇄ back to the board"),
    ("1 … 9", "show at most that many projects (4 again on reload / launch)"),
    ("r", "reload: new code, fresh data, same view"),
    ("Other", None),
    ("Esc", "leave edit mode → cancel → back to the full board"),
    ("?", "this help        q  quit"),
    ("", "Add / rename / move / delete steps and stages: ask Claude Code (it runs proj)."),
]


def draw_help(cv, ui, H, W):
    title = " BOARD KEYS "
    cv.put(0, 0, title + " " * max(W - dw(title), 0), "r b")
    kw = max(dw(k) for k, v in HELP if v) + 4
    rows = []
    for k, v in HELP:
        if v is None:
            rows += [[("", "")], [(" " + k, "cyan b")]]
        else:
            for i, ln in enumerate(wrap(v, max(W - kw - 2, 10))):
                rows.append([("  " + (k if i == 0 else ""), "b"), (" " * (kw - dw(k if i == 0 else "") - 2), ""),
                             (ln, "")])
    body = H - 2
    top = ui["help_top"] = max(0, min(ui.get("help_top", 0), len(rows) - body))
    for i, segs in enumerate(rows[top:top + body]):
        put_segs(cv, 1 + i, 0, segs, W)
    more = "   ↑↓ scroll" if len(rows) > body else ""
    cv.put(H - 1, 0, clip("any key closes" + more, W), "d")


FIRST_RUN = [
    ("No projects yet.", "yellow b"),
    ("", ""),
    ("1. In a terminal, cd into a project folder and run:   proj new", ""),
    ("   (or in Claude Code in that folder: type /board, then \"add this folder to the board\")", "d"),
    ("2. Start a Claude Code session in that folder — it shows up here with its status.", ""),
    ("3. Press ? for keys.  Check the install with:   proj doctor", ""),
]


def draw(cv, d, ui, now, H, W):
    if W < 24 or H < MIN_H:
        cv.put(0, 0, clip("board: terminal too small", W), "yellow")
        return
    if ui["help"]:
        draw_help(cv, ui, H, W)
        return
    projs = ui["order"] = d.ordered()
    ui["width"] = W
    ids = [p["id"] for p in projs]
    if ui.get("sel_id") not in ids:
        ui["sel_id"] = ids[min(ui.get("col", 0), len(ids) - 1)] if ids else None
    ci = ui["col"] = ids.index(ui["sel_id"]) if ids else 0
    solo = bool(ui["solo"]) and bool(ids)
    if solo:                                   # one project in a narrower window
        per, colw, off = 1, min(W, SOLO_W), ci
        more = "  %d/%d" % (ci + 1, len(ids))
    else:
        per = max(1, min(len(projs), W // MIN_COL, ui["cols"]))
        colw = W // per
        off = ui["hoff"]
        off = ci if ci < off else ci - per + 1 if ci >= off + per else off
        off = ui["hoff"] = max(0, min(off, len(projs) - per))
        more = ("  ‹ %d" % off if off else "") + ("  %d ›" % (len(projs) - off - per) if off + per < len(projs) else "")
    title = " PROJECT BOARD  %s   %s%s%s " % (time.strftime("%H:%M:%S", time.localtime(now)),
                                           "minimized" if ui["min"] else FOLD_LABEL[ui["fold"]], more,
                                           "   s…" if ui["leader"] else "   ✎ EDIT" if ui["edit"] else "")
    cv.put(0, 0, title + " " * max(W - dw(title), 0), "r b")
    fit = ui.get("fit")
    if fit and ((H, W) == fit[:2] or now - fit[2] > FIT_WAIT):
        ui["fit"] = None
        if (H, W) != fit[:2]:
            ui["msg"] = ("⚠ the terminal didn't resize (full screen, tmux, or window resizing turned off)", now)
    msg = ui.get("msg")
    gone = next((p for p in projs if p["id"] == ui["del"]), None)
    if gone:
        footer, fstyle = ("⚠ delete project “%s”?  ⏎ = delete (u undoes it until quit / s r)   ·   any other key = cancel"
                          % gone["name"]), "red b"
    elif ui["leader"]:
        footer, fstyle = LEADER_HELP, "cyan b"
    elif ui.get("pending"):
        footer, fstyle = "⏎ again = confirm   ·   any other key = cancel", "green b"
    elif msg and now - msg[1] < MSG_SECS:
        footer, fstyle = msg[0], "green" if msg[0].startswith(("✓", "·", "○", "▶", "?", "↶")) else "yellow"
    elif ui["edit"]:
        footer, fstyle = EDIT_HELP, "yellow b"
    elif ui["min"]:
        footer, fstyle = ("▬ MINIMIZED   m show steps   ←→ project   v/h fit window   e x delete   ? help   q quit"), "d"
    else:
        footer, fstyle = ("? help  " + ("←→ other project  " if solo else "←→ project  ")
                          + "↑↓ step  ⏎⏎ tick/untick  space open/close  z overview⇄zoom  m minimize  h/v fit  e edit  u undo  s… shortcuts"
                          + ("  esc board" if solo else "") + "  q quit"), "d"
    cv.put(H - 1, 0, clip(footer, W), fstyle)
    if not projs:
        if d.err:
            for i, ln in enumerate(wrap("⚠ " + d.err + " — fix the file; the board picks it up",
                                        max(W - 4, 10))[:H - 3]):
                cv.put(2 + i, 2, ln, "red b")
        else:
            for i, (ln, st) in enumerate(FIRST_RUN):
                cv.put(2 + i, 2, clip(ln, W - 4), st)
        return

    unassigned = [s for s in d.st["sessions"].values()
                  if s.get("project_id") == "unassigned" and len(s.get("candidates", [])) > 1]
    warn = None
    if d.err:                                  # broken registry: say so, keep the last good view
        warn = ("⚠ %s — showing the last good copy; fix the file, the board picks it up" % d.err,
                "red b")
    elif unassigned:
        s = unassigned[0]
        warn = ("? session in %s is unassigned — run `proj use %s` there" % (
            s.get("cwd", "?").replace(os.path.expanduser("~"), "~"), "|".join(s["candidates"])), "yellow")
    if warn:
        cv.put(H - 2, 0, clip(warn[0], W), warn[1])

    vis, w = projs[off:off + per], colw - 2
    ui["shown"], ui["warn"] = vis, bool(warn)
    heads = [col_head(d, p, now, w, p["id"] == ui["sel_id"], p["id"] == ui["del"], ui["edit"]) for p in vis]
    hh = max(len(h) for h in heads)
    body_top = 1 + hh + 1
    body_h = max(H - 1 - body_top - (1 if warn else 0), 1)
    longest = 0
    for c, p in enumerate(vis):
        x, pid, selected = c * colw, p["id"], p["id"] == ui["sel_id"]
        for r, segs in enumerate(heads[c]):
            put_segs(cv, 1 + r, x, segs, w)
        cv.put(1 + hh, x, ("━" if selected else "─") * w, "b" if selected else "d")
        nodes = ui["nodes"][pid] = col_nodes(d, ui, p)
        ck = resolve_cur(d, ui, p, nodes)
        pend = ui.get("pending")
        rows, span = ([], (0, 0)) if ui["min"] else col_body(
            d, p, nodes, now, w, ck, selected, pend[1] if pend and pend[0] == pid else None, ui["edit"])
        longest = max(longest, len(rows))
        s = ui["vs"].get(pid, 0)
        s = span[0] if span[0] < s else span[1] - body_h if span[1] > s + body_h else s
        s = ui["vs"][pid] = max(0, min(s, len(rows) - body_h))
        for i, segs in enumerate(rows[s:s + body_h]):
            put_segs(cv, body_top + i, x, segs, w)
        if c < len(vis) - 1 or (solo and colw < W):
            for y in range(1, H - 1 - (1 if warn else 0)):
                cv.put(y, x + colw - 1, "│", "d")
        if s:                                      # scroll hints sit on the column divider
            cv.put(body_top, x + colw - 1, "↑", "r")
        if s + body_h < len(rows):
            cv.put(body_top + body_h - 1, x + colw - 1, "↓", "r")
    ui["need_h"] = need_height(hh, longest, warn)


class CursesCanvas:
    ATTR = {"b": curses.A_BOLD, "d": curses.A_DIM, "r": curses.A_REVERSE}

    def __init__(self, scr):
        self.scr, self.colors = scr, {}
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                bg = curses.COLOR_BLACK
            for i, (name, c) in enumerate([("green", curses.COLOR_GREEN), ("yellow", curses.COLOR_YELLOW),
                                           ("red", curses.COLOR_RED), ("cyan", curses.COLOR_CYAN)], 1):
                curses.init_pair(i, c, bg)
                self.colors[name] = curses.color_pair(i)

    def put(self, y, x, text, style=""):
        H, W = self.scr.getmaxyx()
        if not (0 <= y < H and 0 <= x < W):
            return
        attr = 0
        for tok in style.split():
            attr |= self.colors.get(tok, 0) | self.ATTR.get(tok, 0)
        try:
            self.scr.addstr(y, x, clip(text, W - x), attr)
        except curses.error:   # writing the bottom-right cell raises; harmless
            pass


class TextCanvas:
    def __init__(self, H, W):
        self.H, self.W = H, W
        self.grid = [[" "] * W for _ in range(H)]

    def put(self, y, x, text, style=""):
        if not (0 <= y < self.H and 0 <= x < self.W):
            return
        for ch in clip(text, self.W - x):
            w = dw(ch)
            if x + w > self.W:
                break
            self.grid[y][x] = ch
            if w == 2:
                self.grid[y][x + 1] = ""
            x += w

    def text(self):
        return "\n".join("".join(r).rstrip() for r in self.grid)


# ---------------------------------------------------------------- input

MSG_SECS = 4                                  # how long a footer message (e.g. "✓ ticked …") stays
LEADER_HELP = "s +  ↑↓ stage  ←→ first/last project  t/b top/bottom  o one project⇄board  1…9 columns  r reload  esc cancel"
EDIT_HELP = "✎ EDIT  o ○ open   p ▶ doing   ? idea   ⏎⏎ ✓ done   x delete project   u undo   ·   ↑↓←→ move   esc/e leave edit"
UNDO_KEEP = 50                                # board edits that u can take back (this board session only)


def board_write(d, ui, fn, *args):
    """Run a core board write; keep its undo record; never let a write error crash the board."""
    try:
        msg, u = fn(*args)
    except (core.DataError, OSError, ValueError) as e:
        msg, u = "⚠ not saved: %s" % e, None
    if u:
        ui["undo"].append(u)
        del ui["undo"][:-UNDO_KEEP]
    ui["msg"] = (msg, time.time())
    d.refresh(force=True)
EDIT_MARK = {ord("o"): TODO, ord("O"): TODO, ord("p"): CUR, ord("P"): CUR, ord("?"): IDEA}


FIT_WAIT = 1.5                                # seconds to wait for the terminal to take a resize
FIT_COL_MAX = 42                              # h: widest column (longer lines still wrap)


MIN_H = 6                                     # shortest window the board draws in


def need_height(head_rows, body_rows, warn):
    """Window rows that show every column whole: title, header, rule, body, (warning), footer."""
    return max(1 + head_rows + 1 + body_rows + (1 if warn else 0) + 1, MIN_H)


def fit_width(d, ui, now):
    """h: (columns, rows) for the set number of projects, each column as wide as its longest line."""
    projs = ui["order"]
    if ui["solo"]:
        vis = [p for p in projs if p["id"] == ui["sel_id"]]
    else:                                      # the set number of columns, even if the window is too narrow now
        per = min(len(projs), ui["cols"])
        ci = [p["id"] for p in projs].index(ui["sel_id"])
        off = max(0, min(ui["hoff"], ci, len(projs) - per))
        off = max(off, ci - per + 1)
        vis = projs[off:off + per]
    wide = 1
    body = (lambda p, w: []) if ui["min"] else (lambda p, w: col_body(d, p, col_nodes(d, ui, p), now, w, None, False)[0])
    for p in vis:                              # unwrapped width of every line in the column
        lines = col_head(d, p, now, 999, False) + body(p, 999)
        wide = max([wide] + [dw("".join(t for t, _ in segs).rstrip()) for segs in lines])
    colw = max(MIN_COL, min(wide + 2, FIT_COL_MAX))   # + space on the right + the divider
    w = colw - 2
    heads = [col_head(d, p, now, w, False) for p in vis]
    longest = max(len(body(p, w)) for p in vis)
    return colw * len(vis), need_height(max(len(h) for h in heads), longest, ui.get("warn"))


def fit_window(scr, d, ui, both):
    """v: ask the terminal to make the window as tall as the longest column shown; h: fit the width too."""
    h, w = ui.get("need_h"), ui.get("width")
    if not h or not w or not ui.get("order"):
        return
    now = time.time()
    if both:
        w, h = fit_width(d, ui, now)
        ui["msg"] = ("· window → %d columns wide, %d rows" % (w, h), now)
    else:
        ui["msg"] = ("· window height → %d rows" % h, now)
    ui["fit"] = (h, w, now)
    if scr is not None:                        # xterm "resize window" sequence (Terminal.app, iTerm2)
        try:
            os.write(sys.stdout.fileno(), ("\x1b[8;%d;%dt" % (h, w)).encode())
        except OSError:
            pass


def leader_key(ui, k):
    """The key pressed right after s. -> key for the normal handler, "reload", or None (handled)."""
    if k in LEADER_MAP:
        return LEADER_MAP[k]
    if k in (ord("o"), ord("O")):
        ui["solo"] = not ui["solo"]
    elif k in (ord("r"), ord("R")):
        return "reload"
    elif ord("1") <= k <= ord("9"):
        ui["cols"] = k - ord("0")
        fit = max(1, ui.get("width", 0) // MIN_COL)
        ui["msg"] = ("· showing up to %d project%s%s" % (ui["cols"], "s" * (ui["cols"] > 1),
                     "  (this window fits %d — widen it for more)" % fit if 0 < fit < ui["cols"] else ""), time.time())
    elif k != 27:
        ui["msg"] = ("s + %s: not a shortcut  (%s)" % (key_label(k), LEADER_HELP), time.time())
    return None


def on_key(scr, d, ui, k):
    """-> "reload" when the board should restart, else None."""
    if k == curses.KEY_RESIZE:                 # the window changed size: just redraw (keeps a pending ⏎)
        return None
    if ui["help"]:                             # help screen: ↑↓ scroll, any other key closes it
        if k in (curses.KEY_UP, curses.KEY_DOWN):
            ui["help_top"] = max(0, ui.get("help_top", 0) + (1 if k == curses.KEY_DOWN else -1))
        else:
            ui["help"] = False
        return None
    if k == ord("?") and not ui["edit"] and not ui["leader"] and not ui["del"]:
        ui["help"], ui["help_top"], ui["pending"] = True, 0, None
        return None
    ids = [p["id"] for p in ui.get("order") or d.reg["projects"]]   # columns as last drawn
    if not ids:
        return None
    if ui["leader"]:
        ui["leader"] = False
        k = leader_key(ui, k)
        if k is None or k == "reload":
            return k
    elif ui["del"]:                            # x was pressed in edit mode: only ⏎ deletes
        pid, ui["del"] = ui["del"], None
        if k in (10, 13, curses.KEY_ENTER):
            board_write(d, ui, core.delete_project, pid)
        else:
            ui["msg"] = ("· delete cancelled", time.time())
        return None
    elif k in (ord("s"), ord("S")):
        ui["leader"], ui["pending"] = True, None
        return None
    pending, ui["pending"] = ui.get("pending"), None   # any key but the confirming ⏎ cancels it
    ci = ui["col"]
    enter = k in (10, 13, curses.KEY_ENTER)
    if k in (ord("z"), ord("Z")):              # overview (all closed) ⇄ zoom (all open)
        if ui["min"]:                          # minimized: z just brings the stages back
            ui["min"] = False
            return None
        ui["fold"] = "zoom" if ui["fold"] == "overview" else "overview"
        ui["open"].clear()
        return None
    if k in (ord("e"), ord("E")):              # edit mode on / off
        ui["edit"], ui["msg"] = not ui["edit"], None
        return None
    if k in (ord("u"), ord("U")):              # undo the last board edit (if the file wasn't changed since)
        if not ui["undo"]:
            ui["msg"] = ("· nothing to undo", time.time())
        else:
            board_write(d, ui, lambda u: (core.undo_write(u), None), ui["undo"].pop())
        return None
    if k in (27, curses.KEY_BACKSPACE, 127):   # leave edit mode, else cancel a pending tick, else back to the board
        if ui["edit"]:
            ui["edit"] = False
        elif not pending:
            ui["solo"] = False
        return None
    if k == curses.KEY_LEFT:
        ui["sel_id"] = ids[max(ci - 1, 0)]
        return
    if k == curses.KEY_RIGHT:
        ui["sel_id"] = ids[min(ci + 1, len(ids) - 1)]
        return
    if k in (ord("m"), ord("M")):              # minimize: project status only ⇄ stages and steps
        ui["min"] = not ui["min"]
        return None
    if k in (ord("h"), ord("H"), ord("v"), ord("V")):   # fit the window: h width (+ height), v height
        fit_window(scr, d, ui, both=k in (ord("h"), ord("H")))
        return None
    if k in (PROJ_FIRST, PROJ_LAST):           # s← newest (left-most) project, s→ the last one
        ui["sel_id"] = ids[0] if k == PROJ_FIRST else ids[-1]
        return
    pid = ui["sel_id"]
    if ui["min"] and not (ui["edit"] and k in (ord("x"), ord("X"))):   # rows are hidden: nothing to move or tick
        if k in ROW_KEYS or (ui["edit"] and k in EDIT_MARK):
            ui["msg"] = ("· steps are hidden — m shows them", time.time())
        return None
    nodes = ui["nodes"].get(pid, [])
    keys = [n["key"] for n in nodes]
    i = keys.index(ui["cur"].get(pid)) if ui["cur"].get(pid) in keys else 0
    n = nodes[i] if nodes else None
    step = {curses.KEY_UP: -1, curses.KEY_DOWN: 1}.get(k)
    if step and nodes:
        ui["cur"][pid] = keys[max(0, min(i + step, len(keys) - 1))]
    elif k in (STAGE_UP, STAGE_DOWN) and nodes:   # s↑ / s↓: jump a whole stage
        stages = [j for j, m in enumerate(nodes) if m["it"] is m["stage"]]
        if k == STAGE_UP:
            j = max([j for j in stages if j < i] or [i])
        else:
            j = min([j for j in stages if j > i] or [i])
        ui["cur"][pid] = keys[j]
    elif k in (GO_TOP, GO_BOTTOM) and nodes:   # s t / s b: first / last row of the column
        ui["cur"][pid] = keys[0] if k == GO_TOP else keys[-1]
    elif ui["edit"] and k in (ord("x"), ord("X")):   # edit mode: delete this project (asks first)
        ui["del"] = pid
    elif ui["edit"] and k in EDIT_MARK and n:   # edit mode: set this row's status
        board_write(d, ui, core.set_status, pid, n["ref"], EDIT_MARK[k])
    elif k == ord(" ") and n and n["stage"].children:
        st = n["stage"]
        ui["open"][(pid, n["sref"])] = not stage_open(ui, pid, st, n["sref"])
        ui["cur"][pid] = ("s",) + n["sref"]
    elif enter and n:
        if pending == (pid, n["key"]):       # second ⏎ on the same row: write it
            board_write(d, ui, core.toggle_done, pid, n["ref"])
        else:                                  # first ⏎: show the green ○ and wait
            ui["pending"] = (pid, n["key"])
    return None


STAGE_UP, STAGE_DOWN = -101, -102           # synthetic keys (s↑ / s↓ and the extras below)
GO_TOP, GO_BOTTOM = -103, -104               # first / last row of the column
PROJ_FIRST, PROJ_LAST = -105, -106           # left-most (newest) / right-most project
STAGE_KEYS = {"KEY_SR": STAGE_UP, "KEY_SF": STAGE_DOWN,    # ⇧↑ / ⇧↓
              "KEY_PPAGE": STAGE_UP, "KEY_NPAGE": STAGE_DOWN, "{": STAGE_UP, "}": STAGE_DOWN,
              "KEY_HOME": GO_TOP, "KEY_END": GO_BOTTOM}
ROW_KEYS = {curses.KEY_UP, curses.KEY_DOWN, STAGE_UP, STAGE_DOWN, GO_TOP, GO_BOTTOM, ord(" "), 10, 13, curses.KEY_ENTER}
LEADER_MAP = {curses.KEY_UP: STAGE_UP, curses.KEY_DOWN: STAGE_DOWN,     # s then …
              curses.KEY_LEFT: PROJ_FIRST, curses.KEY_RIGHT: PROJ_LAST,
              ord("t"): GO_TOP, ord("T"): GO_TOP, ord("b"): GO_BOTTOM, ord("B"): GO_BOTTOM}


def _drain_esc(scr):
    """Read the rest of an escape sequence without waiting (it is already in the input buffer)."""
    scr.nodelay(True)
    seq = []
    try:
        while len(seq) < 16:
            c = scr.getch()
            if c == -1:
                break
            seq.append(c)
            head = seq[1:] if seq[0] == 27 else seq
            if not head:
                continue
            if head[0] not in (ord("["), ord("O")):          # ESC b, ESC + decoded key, …
                break
            if len(head) >= 2 and 0x40 <= c <= 0x7e:        # final byte of a CSI / SS3 sequence
                break
    finally:
        scr.timeout(1000)
    return seq


def read_key(scr):
    """getch() with mouse/trackpad events and unknown key combos swallowed. -1 = nothing to do."""
    k = scr.getch()
    if k == curses.KEY_MOUSE:                # wheel / clicks: ignored on purpose
        try:
            curses.getmouse()
        except curses.error:
            pass
        return -1
    if k == 27:                              # bare Esc; a longer escape sequence is a combo we don't use
        return -1 if _drain_esc(scr) else 27
    try:
        name = curses.keyname(k).decode("ascii", "replace") if k >= 0 else ""
    except ValueError:
        name = ""
    return STAGE_KEYS.get(name, k)


def key_label(k):
    if k in (10, 13, curses.KEY_ENTER):
        return "⏎"
    if 32 < k < 127:
        return chr(k)
    try:
        return curses.keyname(k).decode("ascii", "replace")
    except (curses.error, ValueError, OverflowError):
        return str(k)


def new_ui(fold="focus", sel=None, cols=None):
    return {"cols": cols or MAX_COLS, "del": None, "help": False, "fold": fold, "open": {}, "sel_id": sel, "col": 0, "hoff": 0, "cur": {}, "vs": {}, "nodes": {},
            "solo": False, "min": False, "order": [], "leader": False, "edit": False, "pending": None, "msg": None, "undo": []}


def purge_deletes(ui):
    """Quit or s r: undo history ends here, so projects deleted in this board are deleted for good."""
    for u in ui["undo"]:
        if u.get("kind") == "project":
            try:
                core.purge_deleted(u)
            except (core.DataError, OSError):
                pass                             # broken registry: keep the file; safer than guessing
    ui["undo"] = [u for u in ui["undo"] if u.get("kind") != "project"]


def reload_args(ui):
    """What s r keeps: fold, selected project, single-project view, minimized. Not the column count (→ 4)."""
    return ["--fold", ui["fold"]] + (["--sel", ui["sel_id"]] if ui["sel_id"] else []) + (
        ["--solo"] if ui["solo"] else []) + (["--min"] if ui["min"] else [])


def run(scr, args):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    scr.timeout(1000)
    scr.keypad(True)
    try:                                     # take mouse events so the wheel can't act as ↑/↓
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
    except curses.error:
        pass
    cv, d, ui = CursesCanvas(scr), Data(), new_ui(args.fold, args.sel, args.cols)
    ui["solo"], ui["min"] = args.solo, args.min
    frames = 0
    while True:
        d.refresh()
        H, W = scr.getmaxyx()
        scr.erase()
        try:
            draw(cv, d, ui, time.time(), H, W)
        except Exception as e:               # keep running: show the error, r/q still work
            scr.erase()
            for i, ln in enumerate(wrap("board error: %s: %s — press s r to reload, q to quit" % (
                    type(e).__name__, e), max(W - 1, 10))[:max(H - 1, 1)]):
                cv.put(i, 0, ln, "red b")
        scr.refresh()
        frames += 1
        if args.frames and frames >= args.frames:
            return None
        k = read_key(scr)
        if k in (ord("q"), ord("Q")):
            purge_deletes(ui)
            return None
        if k != -1 and on_key(scr, d, ui, k) == "reload":   # s r: restart with new code and every file
            purge_deletes(ui)
            return reload_args(ui)


def main():
    ap = argparse.ArgumentParser(prog="board", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", action="store_true", help="print one frame as plain text and exit")
    ap.add_argument("--size", default="120x40", help="WxH for --dump")
    ap.add_argument("--fold", choices=list(FOLD_LABEL), default="focus", help="which stages start open")
    ap.add_argument("--sel", help="project selected at start")
    ap.add_argument("--solo", action="store_true", help="start showing only the selected project")
    ap.add_argument("--min", action="store_true", help="start minimized (project status only)")
    ap.add_argument("--cols", type=int, choices=range(1, 10), metavar="1-9", help="most projects shown side by side")
    ap.add_argument("--frames", type=int, default=0, help=argparse.SUPPRESS)
    args = ap.parse_args()
    locale.setlocale(locale.LC_ALL, "")
    if args.dump:
        W, H = (int(v) for v in args.size.lower().split("x"))
        d = Data()
        d.refresh()
        cv = TextCanvas(H, W)
        ui = new_ui(args.fold, args.sel, args.cols)
        ui["solo"], ui["min"] = args.solo, args.min
        draw(cv, d, ui, time.time(), H, W)
        print(cv.text())
        return
    os.environ.setdefault("ESCDELAY", "25")
    again = curses.wrapper(run, args)
    if again is not None:                      # R: start a fresh process (new code, same view)
        me = os.path.realpath(__file__)
        os.execv(sys.executable, [sys.executable, me] + again)


if __name__ == "__main__":
    main()
