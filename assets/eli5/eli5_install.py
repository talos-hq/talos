"""'Explain like I'm 5' install walkthroughs for the README.

Render with ./render.sh (needs manim, ffmpeg and gifsicle). Text only, no LaTeX.
"""

from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    Arrow,
    Create,
    FadeIn,
    FadeOut,
    Indicate,
    Rectangle,
    RoundedRectangle,
    Scene,
    Square,
    Text,
    VGroup,
    Write,
    config,
)

PS_CMD = "irm https://raw.githubusercontent.com/talos-hq/talos/main/slim/install.ps1 | iex"
SH_CMD = "curl -fsSL https://raw.githubusercontent.com/talos-hq/talos/main/slim/install.sh | sh"

BG = "#0d1117"
KEY = "#30363d"
KEY_EDGE = "#8b949e"
GLOW = "#f0b72f"
FONT = "DejaVu Sans"
MONO = "DejaVu Sans Mono"

config.background_color = BG


def key(label, width=0.62, font_size=14):
    box = RoundedRectangle(
        corner_radius=0.08, width=width, height=0.55,
        fill_color=KEY, fill_opacity=1, stroke_color=KEY_EDGE, stroke_width=1.5,
    )
    group = VGroup(box)
    if label:
        group.add(Text(label, font=FONT, font_size=font_size, color="#e6edf3").move_to(box))
    return group


def windows_logo():
    panes = VGroup(*[Square(0.11, fill_color="#58a6ff", fill_opacity=1, stroke_width=0) for _ in range(4)])
    return panes.arrange_in_grid(2, 2, buff=0.03)


def keyboard(bottom_labels, bottom_widths):
    """Four rows; returns (keyboard, named keys)."""
    names = {}
    rows = []
    for letters in ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"):
        row = VGroup(*[key(c) for c in letters]).arrange(RIGHT, buff=0.08)
        for c, k in zip(letters, row):
            names[c] = k
        rows.append(row)
    bottom = VGroup(*[key(lbl, w, 12) for lbl, w in zip(bottom_labels, bottom_widths)]).arrange(RIGHT, buff=0.08)
    for lbl, k in zip(bottom_labels, bottom):
        names.setdefault(lbl, k)
    rows.append(bottom)
    board = VGroup(*rows).arrange(DOWN, buff=0.08)
    for row in rows[:3]:
        row.align_to(board, LEFT).shift(RIGHT * 0.15 * rows.index(row))
    frame = RoundedRectangle(corner_radius=0.2, width=board.width + 0.5, height=board.height + 0.5,
                             stroke_color=KEY_EDGE, stroke_width=2).move_to(board)
    return VGroup(frame, board), names, bottom


class Eli5Base(Scene):
    def caption(self, text, old=None):
        new = Text(text, font=FONT, font_size=30, color="#e6edf3").to_edge(UP, buff=0.45)
        if old is None:
            self.play(FadeIn(new, shift=DOWN * 0.2), run_time=0.5)
        else:
            self.play(FadeOut(old, shift=UP * 0.2), FadeIn(new, shift=DOWN * 0.2), run_time=0.5)
        return new

    def press(self, k):
        self.play(k[0].animate.set_fill(GLOW).scale(0.9), run_time=0.3)

    def window(self, title, color, width=13.4, height=2.4):
        body = Rectangle(width=width, height=height, fill_color=color, fill_opacity=1, stroke_color=KEY_EDGE)
        bar = Rectangle(width=width, height=0.4, fill_color="#21262d", fill_opacity=1, stroke_width=0)
        bar.next_to(body, UP, buff=0)
        label = Text(title, font=FONT, font_size=16, color="#e6edf3").move_to(bar).align_to(bar, LEFT).shift(RIGHT * 0.2)
        return VGroup(body, bar, label).shift(DOWN * 0.4)

    def type_into(self, win, prompt, cmd, font_size=15):
        line = Text(prompt + cmd, font=MONO, font_size=font_size, color="#e6edf3")
        if line.width > win[0].width - 0.5:
            line.scale_to_fit_width(win[0].width - 0.5)
        line.move_to(win[0]).align_to(win[0], LEFT).shift(RIGHT * 0.25)
        self.play(Write(line), run_time=max(1.0, len(cmd) / 40))
        return line

    def look_steps(self, names, target, cap):
        board = self.board
        down = Arrow(board.get_top() + UP * 0.1, board[1][-1].get_top(), color=GLOW, buff=0.05)
        cap = self.caption("Look down.", cap)
        self.play(Create(down), run_time=0.6)
        left = Arrow(board[1][-1].get_center() + UP * 0.6, target.get_top() + UP * 0.05, color=GLOW, buff=0.05)
        cap = self.caption("Now look left.", cap)
        self.play(FadeOut(down), Create(left), run_time=0.7)
        self.play(FadeOut(left), run_time=0.3)
        return cap


class WindowsInstall(Eli5Base):
    def construct(self):
        cap = self.caption("Look at your keyboard.")
        self.board, names, bottom = keyboard(
            ["Ctrl", "", "Alt", "", "Alt", "Ctrl"], [0.8, 0.62, 0.7, 3.6, 0.7, 0.8]
        )
        win = bottom[1]
        win.add(windows_logo().move_to(win[0]))
        self.board.scale(1.1).shift(DOWN * 0.3)
        self.play(FadeIn(self.board), run_time=0.8)
        cap = self.look_steps(names, win, cap)
        cap = self.caption("See the Windows key? Hold it down...", cap)
        self.play(Indicate(win, color=GLOW, scale_factor=1.3), run_time=0.8)
        self.press(win)
        cap = self.caption("...and tap R.", cap)
        self.play(Indicate(names["R"], color=GLOW, scale_factor=1.4), run_time=0.7)
        self.wait(0.3)

        cap = self.caption("A box pops up. Type: powershell", cap)
        run = self.window("Run", "#161b22", width=7, height=1.6)
        self.play(FadeOut(self.board), FadeIn(run), run_time=0.6)
        typed = self.type_into(run, "Open:  ", "powershell", font_size=22)
        cap = self.caption("Press Enter.", cap)
        self.wait(0.6)

        cap = self.caption("Copy this, paste it in, press Enter:", cap)
        ps = self.window("Windows PowerShell", "#012456")
        self.play(FadeOut(run), FadeOut(typed), FadeIn(ps), run_time=0.6)
        self.type_into(ps, "PS> ", PS_CMD, font_size=24)
        cap = self.caption("That's it! talos installs itself.", cap)
        self.wait(2)


class MacLinuxInstall(Eli5Base):
    def construct(self):
        cap = self.caption("Look at your keyboard.")
        self.board, names, bottom = keyboard(
            ["fn", "control", "option", "command", "", "command", "option"],
            [0.55, 0.75, 0.75, 0.95, 3.0, 0.95, 0.75],
        )
        self.board.scale(1.1).shift(DOWN * 0.3)
        cmd, space = bottom[3], bottom[4]
        self.play(FadeIn(self.board), run_time=0.8)
        cap = self.look_steps(names, cmd, cap)
        cap = self.caption("See the command key? Hold it down...", cap)
        self.play(Indicate(cmd, color=GLOW, scale_factor=1.3), run_time=0.8)
        self.press(cmd)
        cap = self.caption("...and tap the space bar.", cap)
        self.play(Indicate(space, color=GLOW, scale_factor=1.15), run_time=0.7)
        self.wait(0.3)

        cap = self.caption("A search bar pops up. Type: Terminal", cap)
        bar = self.window("Spotlight Search", "#161b22", width=7, height=1.2)
        self.play(FadeOut(self.board), FadeIn(bar), run_time=0.6)
        typed = self.type_into(bar, "", "Terminal", font_size=22)
        cap = self.caption("Press return.", cap)
        self.wait(0.6)

        cap = self.caption("Copy this, paste it in, press return:", cap)
        term = self.window("Terminal", "#000000")
        self.play(FadeOut(bar), FadeOut(typed), FadeIn(term), run_time=0.6)
        self.type_into(term, "$ ", SH_CMD, font_size=24)
        cap = self.caption("That's it! talos installs itself.", cap)
        self.wait(2)
