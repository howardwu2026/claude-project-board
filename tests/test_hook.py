"""hook.py, run the way Claude Code runs it (JSON on stdin)."""
import fcntl
import os
import time

from util import Case, core


class HookTests(Case):
    def session(self, sid="s1"):
        return core.load_status()["sessions"].get(sid)

    def test_states(self):
        self.hook("SessionStart")
        self.assertEqual((self.session()["state"], self.session()["project_id"]), ("idle", "alpha"))
        self.hook("UserPromptSubmit")
        s = self.session()
        self.assertEqual(s["state"], "processing")
        self.assertIn("turn_at", s)
        self.hook("PermissionRequest")
        self.assertEqual(self.session()["state"], "waiting_approval")
        self.hook("PostToolUse")                        # approval given → back to working
        self.assertEqual(self.session()["state"], "processing")
        self.hook("Stop")
        self.assertEqual(self.session()["state"], "done")
        self.hook("SessionEnd")
        self.assertIsNone(self.session())

    def test_session_start_tells_claude_the_stage(self):
        r = self.hook("SessionStart")
        self.assertEqual(r.returncode, 0)
        self.assertIn("[alpha] Alpha", r.stdout)
        self.assertIn("Build", r.stdout)
        self.assertIn("skills/board/SKILL.md", r.stdout)   # the rules come with the stage, not from CLAUDE.md
        self.assertIn("proj start", r.stdout)

    def test_session_off_the_board_gets_nothing(self):
        r = self.hook("SessionStart", cwd=os.path.join(self.work, "nowhere"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))   # 0 tokens for sessions not on the board

    def test_shared_folder_asks_which(self):
        shared = os.path.join(self.work, "shared")
        os.makedirs(shared)
        reg = core.load_projects()
        reg["projects"] += [self.entry("c1", "C1", shared), self.entry("c2", "C2", shared)]
        core.write_json(core.PROJECTS, reg)
        r = self.hook("SessionStart", cwd=shared)
        self.assertIn("matches several projects", r.stdout)
        self.assertEqual(self.session()["project_id"], "unassigned")

    def test_busy_lock_skips_instead_of_clobbering(self):
        with open(core.LOCK, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            t0 = time.time()
            r = self.hook("UserPromptSubmit")
            waited = time.time() - t0
            fcntl.flock(held, fcntl.LOCK_UN)
        self.assertEqual(r.returncode, 0)
        self.assertGreater(waited, 0.9)
        self.assertIsNone(self.session())
        self.hook("UserPromptSubmit")
        self.assertIsNotNone(self.session())

    def test_broken_registry_is_reported_not_touched(self):
        self.hook("SessionStart")
        with open(core.PROJECTS, "w") as f:
            f.write("{bad")
        with open(core.STATUS) as f:
            st0 = f.read()
        r = self.hook("SessionStart", sid="s2")
        self.assertEqual(r.returncode, 0)
        self.assertIn("broken", r.stdout)
        with open(core.STATUS) as f:
            self.assertEqual(f.read(), st0)
        with open(core.PROJECTS) as f:
            self.assertEqual(f.read(), "{bad")

    def test_deleted_project_stops_tracking(self):
        self.hook("SessionStart")
        self.hook("UserPromptSubmit")
        msg, undo = core.delete_project("alpha")
        s = self.session()
        self.assertEqual((s["project_id"], s["removed"]), ("unassigned", "alpha"))
        self.assertNotIn("alpha", core.load_status()["last_active"])
        self.hook("UserPromptSubmit")                     # later events don't bring it back
        self.assertEqual(self.session()["project_id"], "unassigned")
        r = self.hook("SessionStart")                     # resume / compact: Claude is told why
        self.assertIn("deleted on the board", r.stdout)
        here = os.path.join(self.work, "alpha")
        r = self.proj("tick", "write code", cwd=here)     # Claude can't write to it (no id given)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("deleted on the board", r.stderr)
        r = self.proj("tick", "alpha", "write code")      # … or with the old id
        self.assertIn("deleted on the board", r.stderr)
        self.assertIn("deleted on the board", self.proj("show", "alpha").stderr)
        core.undo_write(undo)                             # board u: tracked again
        s = self.session()
        self.assertEqual(s["project_id"], "alpha")
        self.assertNotIn("removed", s)
        self.assertEqual(self.proj("tick", "write code", cwd=here).returncode, 0)

    def test_garbage_input_never_fails(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, "-S", os.path.join(core.CODE, "hook.py")], input="not json",
                           capture_output=True, text=True, env=self.env())
        self.assertEqual((r.returncode, r.stdout), (0, ""))
