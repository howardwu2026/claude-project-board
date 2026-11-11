"""The proj CLI, run as a real program against a temp data folder."""
import os

from util import Case, core


class ProjTests(Case):
    def alpha(self, *args):
        return self.proj(*args, cwd=os.path.join(self.work, "alpha"))

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def fails(self, r, text):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(text, r.stderr)

    def test_id_from_folder(self):
        r = self.ok(self.alpha("tick", "write code"))
        self.assertIn("[alpha]", r.stderr)
        self.assertEqual(self.marks()["Build/Write code"], "x")

    def test_no_id_outside_a_project(self):
        self.fails(self.proj("tick", "write code"), "which project?")

    def test_explicit_id_anywhere(self):
        self.ok(self.proj("tick", "beta", "one step"))
        self.assertEqual(self.marks("beta")["Only stage"], "x")   # last step done → stage done
        self.ok(self.proj("show", "--focus", "alpha"))           # id after a flag
        self.ok(self.proj("show", "alpha", "--focus"))

    def test_multi_tick_is_all_or_nothing(self):
        before = self.flow()
        self.fails(self.alpha("tick", "write code", "no such step"), "no ")
        self.assertEqual(self.flow(), before)
        r = self.ok(self.alpha("tick", "write code", "build::write tests"))
        self.assertIn("stage complete → Ship", r.stdout)
        self.assertEqual(self.marks()["Ship"], ">")
        self.ok(self.alpha("untick", "write code"))
        self.assertEqual(self.marks()["Build/Write code"], " ")

    def test_ambiguous_lists_stage_labels(self):
        self.fails(self.alpha("tick", "write tests"), "Ship :: Write tests")

    def test_step_add_rm_rename_mv(self):
        self.ok(self.alpha("step", "add", "ship", "Tag release", "Announce"))
        f = core.Flow(core.flow_path("alpha"))
        self.assertEqual([c.text for c in f.stages[2].children],
                         ["Write tests", "Release notes", "Tag release", "Announce"])
        self.ok(self.alpha("step", "rename", "announce", "Post announcement"))
        self.ok(self.alpha("step", "mv", "post announcement", "first"))
        self.ok(self.alpha("step", "rm", "tag release"))
        f = core.Flow(core.flow_path("alpha"))
        self.assertEqual([c.text for c in f.stages[2].children],
                         ["Post announcement", "Write tests", "Release notes"])

    def test_duplicates_refused(self):
        before = self.flow()
        self.fails(self.alpha("step", "add", "ship", "Release notes"), "already exists")
        self.fails(self.alpha("step", "add", "ship", "New", "New"), "already exists")
        self.fails(self.alpha("step", "rename", "write code", "Set up repo"), "already exists")
        self.fails(self.alpha("step", "mv", "ship::write tests", "--to", "build"), "already exists")
        self.fails(self.alpha("stage", "add", "Ship"), "already exists")
        self.fails(self.alpha("stage", "rename", "plan", "Build"), "already exists")
        self.assertEqual(self.flow(), before)

    def test_stages(self):
        self.ok(self.alpha("stage", "add", "Test", "--before", "ship"))
        self.ok(self.alpha("stage", "mv", "test", "last"))
        self.ok(self.alpha("stage", "rename", "test", "QA"))
        self.assertEqual([s.text for s in core.Flow(core.flow_path("alpha")).stages],
                         ["Plan", "Build", "Ship", "QA"])
        self.ok(self.alpha("stage", "rm", "qa"))
        self.ok(self.alpha("stage", "next"))
        self.assertEqual(self.marks()["Ship"], ">")

    def test_idea_and_blockers(self):
        self.ok(self.alpha("idea", "Try Go"))
        self.assertEqual(self.marks()["Build/Try Go"], "?")
        self.ok(self.alpha("block", "Waiting on API key"))
        self.ok(self.alpha("block", "Waiting on review"))
        self.fails(self.alpha("unblock"), "several blockers")
        self.ok(self.alpha("unblock", "1"))
        self.assertEqual([b["text"] for b in core.load_projects()["projects"][0]["blockers"]],
                         ["Waiting on review"])

    def test_add_project(self):
        path = os.path.join(self.work, "gamma")
        os.makedirs(path)
        self.ok(self.proj("add", "gamma", "Gamma", "--path", path, "--stages", "One,Two"))
        self.assertIn("gamma", self.ids())
        self.assertEqual(self.marks("gamma"), {"One": ">", "Two": " "})
        self.fails(self.proj("add", "gamma", "Again", "--path", path, "--stages", "A"), "already exists")
        self.fails(self.proj("add", "Bad Id", "X", "--path", path, "--stages", "A"), "id must be")

    def test_broken_registry_stops_everything(self):
        with open(core.PROJECTS, "w") as f:
            f.write('{"projects": [')
        before = self.flow()
        self.fails(self.proj("tick", "alpha", "write code"), "not valid JSON")
        self.assertEqual(self.flow(), before)
        with open(core.PROJECTS) as f:
            self.assertEqual(f.read(), '{"projects": [')
