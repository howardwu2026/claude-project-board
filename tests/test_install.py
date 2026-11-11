"""install.py and proj doctor, run against a temporary home folder (never your real ~/.claude)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from util import CODE

PY = sys.executable


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pp-home-")
        self.settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w") as f:           # someone else's hook and setting must survive
            json.dump({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}, f)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def env(self):
        e = {k: v for k, v in os.environ.items() if k not in ("PROJECT_PROGRESS_HOME", "CLAUDE_CODE_SESSION_ID")}
        e["HOME"] = self.home
        e["PATH"] = os.path.join(self.home, ".local", "bin") + os.pathsep + e.get("PATH", "")
        return e

    def run_(self, *args):
        return subprocess.run([PY, *args], capture_output=True, text=True, env=self.env(), cwd=self.home)

    def ours(self, ev):
        with open(self.settings) as f:
            groups = json.load(f).get("hooks", {}).get(ev, [])
        return [h["command"] for g in groups for h in g["hooks"]]

    def test_install_twice_then_uninstall(self):
        for _ in range(2):                              # running it again changes nothing extra
            r = self.run_(os.path.join(CODE, "install.py"))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        stop = self.ours("Stop")
        self.assertEqual(stop[0], "say hi")
        self.assertEqual(len(stop), 2)
        self.assertIn("claude-project-board", stop[1])
        self.assertTrue(os.path.islink(os.path.join(self.home, ".local", "bin", "board")))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "skills", "board", "SKILL.md")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".project_progress", "flows")))

        r = self.run_(os.path.join(CODE, "proj.py"), "doctor")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("all good", r.stdout)

        r = self.run_(os.path.join(CODE, "install.py"), "--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.ours("Stop"), ["say hi"])
        with open(self.settings) as f:
            self.assertEqual(json.load(f)["model"], "x")
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".local", "bin", "board")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "skills", "board")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, ".project_progress")))   # data kept

    def test_doctor_reports_what_is_missing(self):
        r = self.run_(os.path.join(CODE, "proj.py"), "doctor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("hooks missing", r.stdout)
        self.assertIn("no /board skill", r.stdout)

    def test_first_hook_creates_the_data_folder(self):
        ev = json.dumps({"session_id": "s1", "cwd": self.home, "hook_event_name": "SessionStart"})
        r = subprocess.run([PY, "-S", os.path.join(CODE, "hook.py")], input=ev, capture_output=True, text=True,
                           env=self.env())
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".project_progress", "status.json")))


if __name__ == "__main__":
    unittest.main()
