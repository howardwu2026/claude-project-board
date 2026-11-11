"""The board UI: keys → state and files, and what gets drawn (no terminal needed)."""
import os
import subprocess
import sys

from util import CODE, Board, Case, board, core

curses = board.curses
ENTER, ESC, UP, DOWN, LEFT, RIGHT = 10, 27, curses.KEY_UP, curses.KEY_DOWN, curses.KEY_LEFT, curses.KEY_RIGHT


class StyleSpy(board.TextCanvas):
    def __init__(self, H, W):
        super().__init__(H, W)
        self.styles = []

    def put(self, y, x, text, style=""):
        self.styles.append((style, text.strip()))
        super().put(y, x, text, style)


class ViewTests(Case):
    def test_columns_and_selection(self):
        b = Board(sel="alpha")
        self.assertEqual(b.ui["cols"], 4)
        self.assertIn("Alpha", b.cv.text())
        self.assertIn("Beta", b.cv.text())
        b.key(RIGHT)
        second = b.ui["order"][1]["id"]
        self.assertEqual(b.ui["sel_id"], second)
        b.key("l", "j", "k")                            # vim keys do nothing
        self.assertEqual(b.ui["sel_id"], second)

    def test_column_cap_and_sideways_scroll(self):
        reg = core.load_projects()
        for i in range(5):
            reg["projects"].append(self.entry("p%d" % i, "P%d" % i))
            self.write_flow("p%d" % i, "- [>] S\n")
        core.write_json(core.PROJECTS, reg)
        b = Board(W=300)
        self.assertIn("3 ›", b.cv.text().splitlines()[0])     # 7 projects, 4 shown
        b.key("s", "2")
        self.assertEqual(b.ui["cols"], 2)
        self.assertIn("5 ›", b.cv.text().splitlines()[0])
        b.key("s", RIGHT)                                       # jump to the last project
        self.assertIn("‹ 5", b.cv.text().splitlines()[0])

    def test_fold_and_zoom(self):
        b = Board(sel="alpha")
        self.assertIn("Write code", b.cv.text())               # focus: current stage open
        self.assertNotIn("Write spec", b.cv.text())
        b.key("z")
        self.assertEqual(b.ui["fold"], "overview")
        self.assertNotIn("Write code", b.cv.text())
        b.key("z")
        self.assertIn("Write spec", b.cv.text())
        b.goto("Ship", stage=True)
        b.key(" ")
        self.assertNotIn("Release notes", b.cv.text())

    def test_stage_jumps(self):
        b = Board(sel="alpha", fold="zoom")
        b.goto("Write code")
        b.key(board.STAGE_DOWN)
        self.assertEqual(b.cur()[:2], ("s", "Ship"))
        b.key("s", UP)
        self.assertEqual(b.cur()[:2], ("s", "Build"))
        b.key("s", "b")
        self.assertEqual(b.cur()[3], "Release notes")

    def test_solo_only_with_s_o(self):
        b = Board(sel="alpha")
        b.key("s", ENTER)
        self.assertFalse(b.ui["solo"])
        b.key("s", "o")
        self.assertTrue(b.ui["solo"])
        self.assertIn("1/2", b.cv.text().splitlines()[0])
        b.key(ESC)
        self.assertFalse(b.ui["solo"])

    def test_reload_keeps_view_but_not_columns(self):
        b = Board(sel="beta")
        b.key("s", "3", "z")
        self.assertEqual(b.key("s", "r"), "reload")
        self.assertEqual(board.reload_args(b.ui), ["--fold", "overview", "--sel", "beta"])

    def test_v_fits_height_to_longest_column(self):
        b = Board(sel="alpha")                          # alpha: 3 header rows + 7 rows, beta: 2 rows
        b.key("v")
        self.assertEqual(b.ui["fit"][:2], (13, 160))     # title + header + rule + 7 rows + footer
        self.assertIn("13 rows", b.msg())
        b.H = 13
        b.frame()                                       # the terminal took it: no warning
        self.assertIsNone(b.ui["fit"])
        self.assertIn("Ship", b.cv.text())
        b.key("v")
        b.ui["fit"] = (13, 160, 0)
        b.H = 30
        b.frame()                                       # still 30 rows later: say so
        self.assertIn("didn't resize", b.msg())
        b.key("e", "x")                                 # x is only delete (edit mode)
        self.assertEqual(b.ui["del"], "alpha")

    def test_h_fits_width_for_the_set_columns(self):
        reg = core.load_projects()
        reg["projects"].append(self.entry("gamma", "Gamma"))
        self.write_flow("gamma", "- [>] S\n  - [ ] A step that is quite a lot longer than the others are\n")
        core.write_json(core.PROJECTS, reg)
        b = Board(sel="alpha", W=60)                    # too narrow: only 2 of the 4 columns show now
        b.key("s", "3", "h")
        h, w = b.ui["fit"][:2]
        self.assertEqual(w, 3 * board.FIT_COL_MAX)      # 3 columns; gamma's long step hits the cap
        b.H, b.W = h, w
        text = b.frame()
        for name in ("Alpha", "Beta", "Gamma"):
            self.assertIn(name, text)
        self.assertIn("A step that", text)
        self.assertIn("others are", text)               # the long step wraps but is all on screen
        self.assertIsNone(b.ui["fit"])

    def test_h_short_project_keeps_min_width(self):
        b = Board(sel="beta")
        b.key("s", "o", "h")                            # one project, short lines
        self.assertEqual(b.ui["fit"][1], board.MIN_COL)

    def test_m_minimizes_to_project_status(self):
        b = Board(sel="alpha")
        b.key("m")
        text = b.cv.text()
        self.assertIn("minimized", text.splitlines()[0])
        self.assertIn("Alpha", text)
        self.assertIn("■", text)                       # progress bar is still there
        for hidden in ("Build", "Write code", "Only stage"):
            self.assertNotIn(hidden, text)
        before = self.flow()
        b.key(DOWN, ENTER, ENTER, " ")                  # row keys do nothing while minimized
        self.assertEqual(self.flow(), before)
        self.assertIn("m shows them", b.msg())
        b.key(RIGHT)
        self.assertNotEqual(b.ui["sel_id"], "alpha")
        b.key("v")
        self.assertEqual(b.ui["fit"][0], board.MIN_H)   # title + 3 header rows + rule + footer
        b.H = board.MIN_H
        self.assertIn("Beta", b.frame())
        b.key("s", "r")
        self.assertIn("--min", board.reload_args(b.ui))
        b.H = 30
        b.key(LEFT, "m")
        self.assertIn("Write code", b.frame())
        b.key("m", "z")                                 # z also brings the stages back
        self.assertFalse(b.ui["min"])

    def test_m_then_h_fits_width_to_headers(self):
        b = Board(sel="alpha")
        b.key("m", "h")
        self.assertEqual(b.ui["fit"][1], 2 * board.MIN_COL)   # short names: narrowest columns

    def test_help_screen(self):
        b = Board()
        b.key("?")
        self.assertIn("BOARD KEYS", b.cv.text())
        b.key(DOWN)
        self.assertTrue(b.ui["help"])
        b.key("x")
        self.assertFalse(b.ui["help"])
        self.assertNotIn("BOARD KEYS", b.cv.text())


class ChangeTests(Case):
    def test_enter_enter_ticks_and_other_key_cancels(self):
        b = Board(sel="alpha")
        b.goto("Write code")
        b.key(ENTER)
        self.assertIn("⏎ again", b.cv.text())
        b.key(DOWN)
        self.assertEqual(self.marks()["Build/Write code"], " ")
        b.goto("Write code")
        b.key(ENTER, ENTER)
        self.assertEqual(self.marks()["Build/Write code"], "x")
        b.key(ENTER, ENTER)
        self.assertEqual(self.marks()["Build/Write code"], " ")

    def test_enter_enter_ticks_an_idea(self):
        b = Board(sel="alpha")
        b.goto("Try Rust")
        b.key(ENTER, ENTER)
        self.assertEqual(self.marks()["Build/Try Rust"], "x")

    def test_edit_mode_statuses(self):
        b = Board(sel="alpha", fold="zoom")
        b.key("e")
        self.assertIn("EDIT", b.cv.text().splitlines()[0])
        b.goto("Write code")
        b.key("p")
        self.assertEqual(self.marks()["Build/Write code"], ">")
        b.key("i")                                      # i is not a key any more
        self.assertEqual(self.marks()["Build/Write code"], ">")
        b.key("?")                                      # in edit mode ? marks an idea (not help)
        self.assertEqual(self.marks()["Build/Write code"], "?")
        self.assertFalse(b.ui["help"])
        b.key("o")
        self.assertEqual(self.marks()["Build/Write code"], " ")
        b.key("x", ESC)                                 # x asks; anything but ⏎ cancels
        self.assertIn("alpha", self.ids())
        b.goto("Ship", stage=True)
        b.key("p")
        self.assertEqual((self.marks()["Build"], self.marks()["Ship"]), (" ", ">"))
        b.key(ESC)
        self.assertFalse(b.ui["edit"])
        b.key("p")                                      # outside edit mode: nothing
        self.assertEqual(self.marks()["Ship"], ">")

    def test_edit_cursor_is_yellow(self):
        for edit, want in ((False, "b r"), (True, board.EDIT_CURSOR)):
            b = Board(sel="alpha")
            b.ui["edit"] = edit
            cv = StyleSpy(30, 160)
            board.draw(cv, b.d, b.ui, 0, 30, 160)
            cursor = [s for s, t in cv.styles if t.startswith("Build")]
            self.assertEqual(cursor, [want])

    def test_undo(self):
        b = Board(sel="alpha")
        before = self.flow()
        b.key("u")
        self.assertIn("nothing to undo", b.msg())
        b.goto("Write code")
        b.key(ENTER, ENTER, "e", "?")
        b.key("u", "u")
        self.assertEqual(self.flow(), before)
        b.key(ENTER, ENTER)
        self.proj("step", "add", "alpha", "ship", "Outside change")
        b.key("u")
        self.assertIn("can't undo", b.msg())
        self.assertIn("Outside change", self.flow())

    def test_delete_project_and_undo(self):
        b = Board(sel="beta")
        b.key("x")
        self.assertIsNone(b.ui["del"])                  # only in edit mode
        b.key("e", "x")
        self.assertIn("delete project", b.cv.text())
        b.key(ENTER)
        self.assertNotIn("beta", self.ids())
        self.assertNotIn("Beta", "\n".join(b.cv.text().splitlines()[:-1]))   # footer names what was deleted
        b.key("u")
        self.assertEqual(self.ids(), ["alpha", "beta"])
        self.assertTrue(os.path.exists(core.flow_path("beta")))

    def test_delete_is_final_after_quit_or_reload(self):
        b = Board(sel="beta")
        b.key("e", "x", ENTER)
        trash = b.ui["undo"][-1]["trash"]
        self.assertTrue(os.path.exists(trash))
        b.key(ESC, "s", "r")                            # s r → run() purges before restarting
        board.purge_deletes(b.ui)
        self.assertFalse(os.path.exists(trash))
        b.key("u")
        self.assertIn("nothing to undo", b.msg())
        self.assertNotIn("beta", self.ids())

    def test_purge_skips_restored_project(self):
        b = Board(sel="beta")
        b.key("e", "x", ENTER)
        u = b.ui["undo"][-1]
        b.key("u")
        board.purge_deletes(b.ui)
        self.assertIn("beta", self.ids())
        self.assertTrue(os.path.exists(core.flow_path("beta")))
        self.assertFalse(core.purge_deleted(u))

    def test_duplicate_rows_are_separate(self):
        self.write_flow("beta", "- [>] A\n  - [ ] Same\n  - [ ] Same\n")
        b = Board(sel="beta")
        b.goto("Same")
        b.key(DOWN, ENTER, ENTER)
        self.assertEqual(self.flow("beta").splitlines()[1:], ["  - [ ] Same", "  - [x] Same"])


class RobustnessTests(Case):
    def test_broken_registry_keeps_last_view(self):
        b = Board(sel="alpha")
        with open(core.PROJECTS, "w") as f:
            f.write("{bad")
        b.d.refresh(force=True)
        text = b.frame()
        self.assertIn("not valid JSON", text)
        self.assertIn("Alpha", text)
        b.goto("Write code")
        b.key(ENTER, ENTER)
        self.assertIn("not saved", b.msg())

    def test_broken_registry_at_start(self):
        with open(core.PROJECTS, "w") as f:
            f.write("{bad")
        self.assertIn("not valid JSON", Board().cv.text())

    def test_unreadable_flow_shows_in_its_column(self):
        with open(core.flow_path("beta"), "wb") as f:
            f.write(b"- [ ] caf\xe9\n")
        text = Board().cv.text()
        self.assertIn("not UTF-8", text)
        self.assertIn("Alpha", text)

    def test_tiny_terminal(self):
        b = Board(W=20, H=5)
        self.assertIn("terminal too", b.cv.text())

    def test_mouse_and_unknown_combos_are_ignored(self):
        class Scr:
            def __init__(self, keys):
                self.keys = list(keys)

            def getch(self):
                return self.keys.pop(0) if self.keys else -1

            def nodelay(self, on):
                pass

            def timeout(self, ms):
                pass

        self.assertEqual(board.read_key(Scr([curses.KEY_MOUSE])), -1)
        self.assertEqual(board.read_key(Scr([27])), 27)                            # bare Esc
        self.assertEqual(board.read_key(Scr([27] + [ord(c) for c in "[1;3A"])), -1)  # ⌥↑

    def test_dump_cli(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(CODE, "board.py"), "--dump", "--size", "100x12"],
                           capture_output=True, text=True, env=self.env())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PROJECT BOARD", r.stdout)
