#!/usr/bin/env python3
"""Install (or with --uninstall, remove) claude-project-board for the current user.

  python3 install.py              board + proj on your PATH, Claude Code hooks, the /board skill
  python3 install.py --uninstall  takes all of that out again; your data folder is kept

Safe to run again (e.g. after `git pull`). Backs up ~/.claude/settings.json before changing it and
never touches other settings, other hooks, or files it didn't create.
"""
import json
import os
import shlex
import shutil
import sys
import time

REPO = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, REPO)
import core  # noqa: E402

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local", "bin")
LINKS = {"board": "board.py", "proj": "proj.py"}
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
SKILL_SRC = os.path.join(REPO, "skill", "board", "SKILL.md")
SKILL_DIR = os.path.join(HOME, ".claude", "skills", "board")
HOOK_CMD = "%s -S %s  # %s" % (shlex.quote(sys.executable), shlex.quote(os.path.join(REPO, "hook.py")),
                                core.HOOK_MARK)


def say(mark, text):
    print(" %s %s" % (mark, text))


def stamp():
    return time.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------- commands on PATH

def ours_link(path):
    """A symlink we made: it points at a board.py / proj.py (this copy or an older one)."""
    return os.path.islink(path) and os.path.basename(os.readlink(path)) in LINKS.values()


def install_links():
    os.makedirs(BIN, exist_ok=True)
    for name, target in LINKS.items():
        dst, src = os.path.join(BIN, name), os.path.join(REPO, target)
        if os.path.lexists(dst) and not ours_link(dst):
            say("✗", "%s exists and isn't ours — left alone (remove it, then run this again)" % core.short(dst))
            continue
        if os.path.lexists(dst):
            os.unlink(dst)
        os.symlink(src, dst)
        os.chmod(src, 0o755)
        say("✓", "%s → %s" % (core.short(dst), core.short(src)))
    if BIN not in os.environ.get("PATH", "").split(os.pathsep):
        say("!", "%s isn't on your PATH yet. Add this line to ~/.zshrc, then open a new terminal:" % core.short(BIN))
        print('     export PATH="$HOME/.local/bin:$PATH"')


def remove_links():
    for name in LINKS:
        dst = os.path.join(BIN, name)
        if ours_link(dst):
            os.unlink(dst)
            say("✓", "removed " + core.short(dst))


# ---------------------------------------------------------------- Claude Code hooks

def load_settings():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS, encoding="utf-8") as f:
        data = json.load(f)                    # invalid JSON: stop rather than overwrite it
    backup = "%s.bak-%s" % (SETTINGS, stamp())
    shutil.copy2(SETTINGS, backup)
    say("·", "backup of your settings: " + core.short(backup))
    return data


def write_settings(data):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    core.write_json(SETTINGS, data)


def set_hooks(add):
    data = load_settings()
    hooks = data.setdefault("hooks", {})
    for ev, matcher in core.HOOK_EVENTS.items():
        groups = [g for g in hooks.get(ev, []) if not any(core.is_our_hook(h) for h in g.get("hooks", []))]
        if add:
            g = {"hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 5}]}
            if matcher:
                g = dict(matcher=matcher, **g)
            groups.append(g)
        if groups:
            hooks[ev] = groups
        else:
            hooks.pop(ev, None)
    if not hooks:
        data.pop("hooks")
    write_settings(data)
    say("✓", "%s Claude Code hooks in %s" % ("registered" if add else "removed", core.short(SETTINGS)))


# ---------------------------------------------------------------- /board skill

def ours_skill(path):
    try:
        with open(path, encoding="utf-8") as f:
            return "name: board" in f.read(400)
    except OSError:
        return False


def install_skill():
    dst = os.path.join(SKILL_DIR, "SKILL.md")
    os.makedirs(SKILL_DIR, exist_ok=True)
    if os.path.exists(dst) and not ours_skill(dst):
        backup = "%s.bak-%s" % (dst, stamp())
        os.replace(dst, backup)
        say("·", "another skill was there; moved to " + core.short(backup))
    shutil.copyfile(SKILL_SRC, dst)
    say("✓", "skill /board → " + core.short(dst))


def remove_skill():
    dst = os.path.join(SKILL_DIR, "SKILL.md")
    if ours_skill(dst):
        os.unlink(dst)
        if not os.listdir(SKILL_DIR):
            os.rmdir(SKILL_DIR)
        say("✓", "removed skill " + core.short(dst))


# ---------------------------------------------------------------- main

def main():
    if sys.version_info < (3, 9):
        sys.exit("claude-project-board needs Python 3.9 or newer (this is %s)" % sys.version.split()[0])
    if "--uninstall" in sys.argv[1:]:
        print("Removing claude-project-board %s" % core.VERSION)
        remove_links()
        set_hooks(add=False)
        remove_skill()
        say("·", "your data is kept in %s — delete that folder yourself if you want it gone" % core.short(core.HOME))
        return
    print("Installing claude-project-board %s from %s" % (core.VERSION, core.short(REPO)))
    install_links()
    set_hooks(add=True)
    install_skill()
    os.makedirs(core.FLOWS, exist_ok=True)
    say("✓", "data folder: " + core.short(core.HOME))
    print("\nNext: cd into a project folder and run `proj new`, then `board` in its own terminal window.")
    print("Check the setup any time with `proj doctor`. New Claude Code sessions pick up the hooks.")


if __name__ == "__main__":
    main()
