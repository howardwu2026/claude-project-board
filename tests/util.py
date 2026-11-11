"""Shared test setup: every test gets its own temp data folder (never the real data)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

CODE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, CODE)

# Point the data folder somewhere harmless before anything imports core.
import atexit  # noqa: E402

os.environ["PROJECT_PROGRESS_HOME"] = tempfile.mkdtemp(prefix="pp-guard-")
atexit.register(shutil.rmtree, os.environ["PROJECT_PROGRESS_HOME"], True)
import core  # noqa: E402
import board  # noqa: E402

ALPHA = """# Alpha

- [x] Plan
  - [x] Write spec
- [>] Build
  - [x] Set up repo
  - [ ] Write code
  - [ ] Write tests
  - [?] Try Rust
- [ ] Ship
  - [ ] Write tests
  - [ ] Release notes

<!-- markers: [x] done  [>] current  [ ] todo  [?] idea -->
"""
BETA = """# Beta

- [>] Only stage
  - [ ] One step
"""


class Case(unittest.TestCase):
    """A fresh data folder with two projects: alpha (work/alpha) and beta (work/beta)."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pp-test-")
        core.set_home(self.home)
        assert core.HOME != core.CODE
        self.work = os.path.join(self.home, "work")
        for p in ("alpha", "beta", "nowhere"):
            os.makedirs(os.path.join(self.work, p))
        os.makedirs(core.FLOWS)
        self.write_flow("alpha", ALPHA)
        self.write_flow("beta", BETA)
        core.write_json(core.PROJECTS, {"projects": [
            self.entry("alpha", "Alpha"), self.entry("beta", "Beta")]})

    def tearDown(self):
        core.set_home(os.environ["PROJECT_PROGRESS_HOME"])
        shutil.rmtree(self.home, ignore_errors=True)

    # --- data helpers
    def entry(self, pid, name, path=None):
        return {"id": pid, "name": name, "match": {"path": path or os.path.join(self.work, pid)},
                "blockers": [], "updated_at": core.now_iso()}

    def write_flow(self, pid, text):
        with open(core.flow_path(pid), "w", encoding="utf-8") as f:
            f.write(text)

    def flow(self, pid="alpha"):
        with open(core.flow_path(pid), encoding="utf-8") as f:
            return f.read()

    def marks(self, pid="alpha"):
        """{"Stage" or "Stage/step": mark} — easy to assert on."""
        out = {}
        for st in core.Flow(core.flow_path(pid)).stages:
            out[st.text] = st.mark
            for c in st.children:
                out.setdefault(st.text + "/" + c.text, c.mark)
        return out

    def ids(self):
        return [p["id"] for p in core.load_projects()["projects"]]

    # --- running the real programs
    def env(self):
        e = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        e["PROJECT_PROGRESS_HOME"] = self.home
        return e

    def proj(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-I", os.path.join(CODE, "proj.py"), *args],
                              capture_output=True, text=True, env=self.env(),
                              cwd=cwd or os.path.join(self.work, "nowhere"))

    def hook(self, event, sid="s1", cwd=None):
        ev = {"session_id": sid, "cwd": cwd or os.path.join(self.work, "alpha"), "hook_event_name": event}
        return subprocess.run([sys.executable, "-S", os.path.join(CODE, "hook.py")], input=json.dumps(ev),
                              capture_output=True, text=True, env=self.env())


class Board:
    """Drive the board without a terminal: keys go to on_key, frames go to a TextCanvas."""

    def __init__(self, sel=None, fold="focus", W=160, H=30):
        self.d, self.W, self.H = board.Data(), W, H
        self.d.refresh()
        self.ui = board.new_ui(fold, sel)
        self.frame()

    def frame(self):
        self.cv = board.TextCanvas(self.H, self.W)
        board.draw(self.cv, self.d, self.ui, time.time(), self.H, self.W)
        return self.cv.text()

    def key(self, *ks):
        out = None
        for k in ks:
            out = board.on_key(None, self.d, self.ui, ord(k) if isinstance(k, str) else k)
            self.frame()
        return out

    def cur(self):
        return self.ui["cur"].get(self.ui["sel_id"])

    def goto(self, text, stage=False):
        """Move the cursor to the row with this text (stage row if stage=True)."""
        self.key(board.GO_TOP)
        for _ in range(100):
            c = self.cur()
            if c and ((stage and c[0] == "s" and c[1] == text) or (not stage and c[0] == "c" and c[3] == text)):
                return
            before = c
            self.key(board.curses.KEY_DOWN)
            if self.cur() == before:
                break
        raise AssertionError("row not found: " + text)

    def msg(self):
        return (self.ui.get("msg") or ("",))[0]
