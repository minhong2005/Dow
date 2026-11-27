"""Mac terminal views of the same sessions listed by the Dow desktop app."""
import curses
from pathlib import Path
import runpy
import secrets
import subprocess
import tempfile

import project_runtime as rt


class Terminal:
    browser = [*rt.DOCKER, "exec", rt.CONTROLLER, "browser-open", "--show"]

    def __init__(self, project):
        self.project = project
        self.in_menu = False

    def jobs(self, project):
        return sorted((r["id"], project, r["tool"], r["title"], str(int(r["exited"])))
                      for _, r in rt.catalog().values() if r["project"] == project and not r["archived"])

    def create(self, project, tool, title):
        return rt.new_session(project, tool, title)["id"]

    def preset(self, project):
        return rt.session_preset(project)["ids"]

    def action(self, action, ident):
        container, row = rt.catalog()[ident]
        rt.sessions(container, action, ident)
        if action == "end" and row["scope"] == "isolated":
            rt.docker("rm", "-f", container)

    def show(self, ids):
        # The screen configuration contains only validated Docker and tmux identifiers.
        rows = rt.catalog()
        commands = [[*rt.DOCKER, "exec", "-it", rows[ident][0], "tmux", "-u",
                     "attach-session", "-t", "=" + ident] for ident in ids]
        lines = ["startup_message off", "escape ^Aa", "caption always 'Dow | Ctrl-A Tab: next | Ctrl-A D: back'"]
        for i, cmd in enumerate(commands):
            if i:
                lines.extend(["split", "focus down"])
            lines.append("screen -t Dow-" + str(i + 1) + " " + " ".join(cmd))
        lines.extend(["resize =", "focus top"])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".screenrc") as config:
            config.write("\n".join(lines) + "\n")
            config.flush()
            session = "dow-view-" + secrets.token_hex(8)
            if self.in_menu:
                curses.def_prog_mode()
                curses.endwin()
            try:
                subprocess.run(["/usr/bin/screen", "-S", session, "-c", config.name], check=True)
            finally:
                # Discard only the viewer. Agent sessions remain in their containers.
                subprocess.run(["/usr/bin/screen", "-S", session, "-X", "quit"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if self.in_menu:
                    curses.reset_prog_mode()


def open_terminal(mode, project):
    rt.project_id(project)
    terminal = Terminal(project)
    if mode == "preset":
        terminal.show(terminal.preset(project))
        return
    script = Path(__file__).with_name("aiwork-sessions")
    if not script.is_file():
        script = Path(__file__).resolve().parents[1] / "workspace/aiwork-sessions"
    terminal.in_menu = True
    curses.wrapper(runpy.run_path(str(script))["menu"], terminal)
