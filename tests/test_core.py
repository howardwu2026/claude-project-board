"""Flow parsing, matching, transitions, storage checks, locking, board writes and undo."""
import fcntl
import os
import time

from util import Case, core


class FlowTests(Case):
    def test_parse(self):
        f = core.Flow(core.flow_path("alpha"))
        self.assertEqual([s.text for s in f.stages], ["Plan", "Build", "Ship"])
        self.assertEqual(f.current().text, "Build")
        self.assertEqual([c.mark for c in f.stages[1].children], ["x", " ", " ", "?"])
        self.assertEqual(f.progress(), (1, 3))

    def test_find(self):
        f = core.Flow(core.flow_path("alpha"))
        self.assertEqual(f.find("write code").text, "Write code")
        self.assertEqual(f.find("ship", stages_only=True).text, "Ship")
        with self.assertRaises(ValueError) as e:
            f.find("Write tests")                       # same name in two stages
        self.assertIn("Build :: Write tests", str(e.exception))
        self.assertIn("Ship :: Write tests", str(e.exception))
        self.assertIs(f.find("ship::write"), f.stages[2].children[0])
        with self.assertRaises(ValueError):
            f.find("nothing like this")

    def test_ref_locate_duplicates(self):
        self.write_flow("beta", "- [>] A\n  - [ ] Same\n  - [ ] Same\n- [ ] A\n")
        f = core.Flow(core.flow_path("beta"))
        second = f.stages[0].children[1]
        self.assertEqual(f.ref(second), ("A", 0, "Same", 1))
        self.assertIs(f.locate(("A", 0, "Same", 1)), second)
        self.assertIs(f.locate(("A", 1, None, 0)), f.stages[1])
        self.assertIsNone(f.locate(("A", 0, "Same", 2)))

    def test_tick_advances_stage_and_ignores_ideas(self):
        reg = core.load_projects()
        p, f = reg["projects"][0], core.Flow(core.flow_path("alpha"))
        self.assertIsNone(core.tick_item(p, f, f.find("write code")))
        nxt = core.tick_item(p, f, f.find("build::write tests"))
        self.assertEqual(nxt.text, "Ship")              # the [?] idea didn't block it
        f.save()
        m = self.marks()
        self.assertEqual((m["Build"], m["Ship"]), ("x", ">"))


class StorageTests(Case):
    def test_missing_registry_is_empty(self):
        os.remove(core.PROJECTS)
        self.assertEqual(core.load_projects(), {"projects": []})

    def test_broken_registry_raises_and_is_untouched(self):
        for text in ('{"projects": [', '[]', '{"projects": [{"name": "x"}]}',
                     '{"projects": [{"id": "a"}, {"id": "a"}]}'):
            with open(core.PROJECTS, "w") as f:
                f.write(text)
            with self.assertRaises(core.DataError):
                core.load_projects()
            with open(core.PROJECTS) as f:
                self.assertEqual(f.read(), text)

    def test_registry_defaults(self):
        core.write_json(core.PROJECTS, {"projects": [{"id": "a", "blockers": "junk"}]})
        p = core.load_projects()["projects"][0]
        self.assertEqual((p["name"], p["blockers"]), ("a", []))

    def test_broken_status_is_set_aside(self):
        with open(core.STATUS, "w") as f:
            f.write("{oops")
        st = core.load_status()
        self.assertEqual((st["sessions"], st["last_active"]), ({}, {}))
        self.assertTrue(any(n.startswith("status.json.bad-") for n in os.listdir(self.home)))

    def test_flow_not_utf8(self):
        with open(core.flow_path("beta"), "wb") as f:
            f.write(b"- [ ] caf\xe9\n")
        with self.assertRaises(core.DataError):
            core.Flow(core.flow_path("beta"))

    def test_lock_timeout_yields_false(self):
        with open(core.LOCK, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            t0 = time.time()
            with core.locked(timeout=0.2) as got:
                self.assertFalse(got)
            self.assertGreaterEqual(time.time() - t0, 0.2)
            fcntl.flock(held, fcntl.LOCK_UN)
        with core.locked(timeout=0.2) as got:
            self.assertTrue(got)


class BoardWriteTests(Case):
    REF_SHIP = ("Ship", 0, None, 0)
    REF_CODE = ("Build", 0, "Write code", 0)

    def test_toggle_and_undo(self):
        before = self.flow()
        msg, u = core.toggle_done("alpha", self.REF_CODE)
        self.assertTrue(msg.startswith("✓"))
        self.assertEqual(self.marks()["Build/Write code"], "x")
        self.assertTrue(core.undo_write(u).startswith("↶"))
        self.assertEqual(self.flow(), before)

    def test_undo_refuses_after_outside_change(self):
        _, u = core.toggle_done("alpha", self.REF_CODE)
        self.write_flow("alpha", self.flow() + "- [ ] Added elsewhere\n")
        self.assertIn("can't undo", core.undo_write(u))
        self.assertIn("Added elsewhere", self.flow())

    def test_set_status_doing_moves_current_stage(self):
        msg, u = core.set_status("alpha", self.REF_SHIP, core.CUR)
        m = self.marks()
        self.assertEqual((m["Build"], m["Ship"]), (" ", ">"))
        self.assertIsNotNone(u)
        msg, u = core.set_status("alpha", self.REF_SHIP, core.CUR)
        self.assertTrue(msg.startswith("· already"))
        self.assertIsNone(u)                            # no change → nothing to undo

    def test_set_status_idea_and_open(self):
        ref = ("Build", 0, "Try Rust", 0)
        core.set_status("alpha", ref, core.TODO)
        self.assertEqual(self.marks()["Build/Try Rust"], " ")
        core.set_status("alpha", ref, core.IDEA)
        self.assertEqual(self.marks()["Build/Try Rust"], "?")

    def test_missing_item(self):
        msg, u = core.toggle_done("alpha", ("Nope", 0, None, 0))
        self.assertIn("not found", msg)
        self.assertIsNone(u)

    def test_delete_project_and_undo(self):
        n0, flow0 = len(core.load_projects()["projects"]), self.flow()
        msg, u = core.delete_project("alpha")
        self.assertNotIn("alpha", self.ids())
        self.assertFalse(os.path.exists(core.flow_path("alpha")))
        self.assertEqual(len(os.listdir(core.TRASH)), 1)
        self.assertIn("restored", core.undo_write(u))
        self.assertEqual(self.ids(), ["alpha", "beta"])  # back in its old place
        self.assertEqual(self.flow(), flow0)
        self.assertEqual(len(core.load_projects()["projects"]), n0)

    def test_delete_undo_refuses_if_id_reused(self):
        _, u = core.delete_project("alpha")
        reg = core.load_projects()
        reg["projects"].append(self.entry("alpha", "New alpha"))
        core.write_json(core.PROJECTS, reg)
        self.assertIn("can't undo", core.undo_write(u))


class ResolveTests(Case):
    def test_longest_path_wins_and_ties_are_unassigned(self):
        ps = [self.entry("a", "A", "/x"), self.entry("b", "B", "/x/y"), self.entry("c", "C", "/x/y")]
        self.assertEqual(core.resolve("/x/z", "", ps[:2])[0], "a")
        self.assertEqual(core.resolve("/x/y/z", "", ps[:2])[0], "b")
        pid, cands = core.resolve("/x/y", "", ps)
        self.assertEqual((pid, sorted(cands)), ("unassigned", ["b", "c"]))
        self.assertEqual(core.resolve("/x/y", "", ps, assigned="c")[0], "c")
