"""Matrix digital rain effect in the terminal.

Inspired by the movie: The Matrix
Original code adapted from juancarlospaco (2013)
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import struct
import sys
import termios
import time
from dataclasses import dataclass, field
from random import randint, choice, random


# Katakana as in the movie "The Matrix" + digits.
# Uses HALF-WIDTH katakana symbols (U+FF61-U+FF9F): each occupies exactly
# one terminal column, so the rain renders as straight vertical columns.
# Full-width ideographs (U+30A0-U+30FF) occupy two columns and break the grid.
_SYMBOLS = [chr(i) for i in range(0xFF61, 0xFF9F + 1)] + [str(i) for i in range(10)]

# Character modes (switchable with keys 1/2/3 during runtime)
_MODES = {
    "1": [str(i) for i in range(10)],
    "2": [chr(i) for i in range(0xFF61, 0xFF9F + 1)],
    "3": list(_SYMBOLS),
    "4": ["0", "1"],
}

# ANSI colors
_COLORS = [32, 37, 36, 31, 33]  # green, white, cyan, red, yellow

# Quotes from the movie "The Matrix"
_QUOTES = [
    "WAKE UP, NEO...",
    "THE MATRIX HAS YOU",
    "FOLLOW THE WHITE RABBIT",
    "KNOCK, KNOCK, NEO",
    "YOU'VE BEEN THERE BEFORE",
    "THERE IS NO SPOON",
    "IGNORANCE IS BLISS",
    "FREE YOUR MIND",
    "THE ORACLE TOLD ME I'D FALL IN LOVE",
    "REALITY IS JUST A CONSTRUCT",
]

# Hacked system phrases (always red color, more glitches)
_HACK_QUOTES = [
    "SYSTEM OVERRIDE",
    "ACCESS GRANTED",
    "NEO IS ONLINE",
    "MAINFRAME BREACHED",
    "DOWNLOADING CODEX",
    "ENTITY DETECTED",
    "REALITY PATCHING",
    "ZERO ONE ZERO ONE",
    "SIMULATION COLLAPSE",
    "THE ONE HAS ARISEN",
    "ROOT ACCESS OBTAINED",
    "FIREWALL DISABLED",
]

# Chance of hacked phrase appearing (1 in N)
_HACK_CHANCE = 5

# Global flag for terminal size changes
_resize_pending = False

# Delay between character appearance (typing like a human)
_TYPE_SPEED = 0.08


@dataclass(frozen=False)
class Column:
    """A single column of falling characters."""

    width: int
    height: int
    speed: int
    symbols: list[str]
    length: int = field(init=False)
    text: list[str] = field(default_factory=list, init=False)
    y: int = field(init=False)
    x: int = field(init=False)
    skip: int = field(init=False)
    state: str = field(init=False)
    bright: set[int] = field(init=False)

    def __post_init__(self) -> None:
        self._respawn()
        self.y = -self.length
        self.x = randint(0, self.width - 1)
        self.skip = 0
        self.state = "falling"
        self.bright = set()

    def _respawn(self) -> None:
        """Generate a fresh random character set."""
        self.length = randint(10, 25)
        self.text = [choice(self.symbols) for _ in range(self.length)]
        self.bright = set()

    def move(self) -> None:
        if self.state == "falling":
            if self.speed > self.skip:
                self.skip += 1
            else:
                self.skip = 0
                self.y += 1
            if self.y >= self.height - 1:
                self.state = "fading"
        else:
            self.length -= 1
            if self.length <= 0:
                self._respawn()
                self.y = -self.length
                self.x = randint(0, self.width - 1)
                self.skip = 0
                self.state = "falling"

    def flicker(self) -> None:
        """Randomly change ~10% of characters and make some flash bright."""
        self.bright = set()
        for i in range(self.length):
            if random() < 0.1:
                self.text[i] = choice(self.symbols)
            if random() < 0.15:
                self.bright.add(i)

    def visible_text(self) -> list[str]:
        """Return the currently visible portion of the column."""
        return self.text[: self.length]

    def visible_bright(self) -> set[int]:
        """Return bright indices relative to the visible part."""
        return {i for i in self.bright if i < self.length}


@dataclass
class Terminal:
    """Thin wrapper around terminal size detection."""

    _instance: Terminal | None = None

    def __new__(cls) -> Terminal:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self.height: int = 24
        self.width: int = 80
        self._is_tty: bool = sys.stdout.isatty()

        if not self._is_tty:
            return

        try:
            self.height, self.width = self._get_winsize()
        except OSError:
            pass

    @staticmethod
    def _get_winsize() -> tuple[int, int]:
        """Get terminal dimensions via TIOCGWINSZ ioctl."""
        fd = sys.stdout.fileno()
        data = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        rows, cols, _, _ = struct.unpack("HHHH", data)
        return rows, cols


@dataclass
class Canvas:
    """Screen buffer — uses list of characters for UTF-8 support."""

    width: int
    height: int
    size: int = field(init=False)
    grid: list[str] = field(init=False)
    attr: list[int] = field(init=False)  # 0=normal, 1=bold, 2=dim

    def __post_init__(self) -> None:
        self.size = self.height * self.width
        self.grid = [" "] * self.size
        self.attr = [0] * self.size

    def clear(self) -> None:
        """Clear buffer to spaces."""
        self.grid[:] = [" "] * self.size
        self.attr[:] = [0] * self.size

    def set_vertical(
        self, x: int, y: int, string: list[str], intensities: list[int]
    ) -> None:
        """Draw a list of characters vertically at column x with per-char intensities."""
        if x < 0:
            x = self.width + x
        if x >= self.width or x < 0:
            return

        slen = len(string)
        if y < 0:
            string = string[-y:]
            intensities = intensities[-y:]
            y = 0
            slen = len(string)

        if y >= self.height:
            return

        if y + slen > self.height:
            string = string[: self.height - y]
            intensities = intensities[: self.height - y]
            slen = len(string)

        base = y * self.width + x
        for i in range(slen):
            self.grid[base + i * self.width] = string[i]
            self.attr[base + i * self.width] = intensities[i]


def create_columns(
    count: int, width: int, height: int, symbols: list[str]
) -> list[Column]:
    """Create random falling columns."""
    columns: list[Column] = []
    for _ in range(count):
        columns.append(
            Column(
                speed=randint(1, 3),
                width=width,
                height=height,
                symbols=symbols,
            )
        )
    return columns


def _handle_resize(sig: int, frame: object) -> None:
    global _resize_pending
    _resize_pending = True


def _get_keys(stdin_fd: int) -> str:
    """Read all available keypresses without blocking, stripping escape sequences.

    Mouse clicks, arrow keys and function keys arrive as escape sequences
    (starting with ESC). They are fully removed so they never linger in the
    input buffer and never interfere with single-character commands.
    """
    chars: list[str] = []
    while select.select([stdin_fd], [], [], 0)[0]:
        try:
            data = os.read(stdin_fd, 1024)
        except OSError:
            break
        if not data:
            break
        text = data.decode("utf-8", errors="replace")
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x1b":
                # Skip the whole escape sequence.
                i += 1
                if i < len(text) and text[i] == "[":
                    i += 1
                    while i < len(text) and not ("\x40" <= text[i] <= "\x7e"):
                        i += 1
                    if i < len(text):
                        i += 1  # skip the final byte
                elif i < len(text):
                    i += 1  # ESC + single char (or just ESC)
            else:
                chars.append(ch)
                i += 1
    return "".join(chars)


def render_frame(canvas: Canvas, color_code: int) -> bytes:
    """Render canvas to bytes with ANSI attribute + color changes.

    Every cell is emitted (including spaces) so the terminal wraps lines
    exactly as before; only non-printing ANSI sequences are added.
    Color is applied per attribute group so switching colors works reliably.
    """
    parts: list[bytes] = []
    current: tuple[int, int] | None = None
    for i in range(canvas.size):
        ch = canvas.grid[i]
        key = (canvas.attr[i], color_code)
        if key != current:
            current = key
            attr, col = key
            parts.append(("\033[%d;%dm" % (attr, col)).encode())
        parts.append(ch.encode("utf-8"))
    parts.append(b"\033[0m")
    return b"".join(parts)


def glitch_char() -> str:
    """Return a random character for the glitch effect."""
    return choice(_SYMBOLS)


def show_quote(
    term: Terminal, canvas: Canvas, color_code: int, quote: str, is_hack: bool = False
) -> None:
    """Show a phrase with typewriter effect, glitch, and countdown dots."""
    q = quote[: max(0, term.width - 5)]  # space for 3 dots at the end
    y = term.height // 2
    x = max(0, (term.width - len(q) - 3) // 2)

    # Clear the screen first
    canvas.clear()
    sys.stdout.buffer.write(b"\033[2J\033[H")
    sys.stdout.buffer.write(render_frame(canvas, color_code))
    sys.stdout.buffer.write(b"\033[0m")
    sys.stdout.buffer.flush()
    time.sleep(0.2)

    # If hack — random glitches across the screen before start
    if is_hack:
        for _ in range(term.width * term.height // 4):
            rx = randint(0, term.width - 1)
            ry = randint(0, term.height - 1)
            canvas.grid[ry * term.width + rx] = glitch_char()
            canvas.attr[ry * term.width + rx] = 1
        sys.stdout.buffer.write(b"\033[2J\033[H")
        sys.stdout.buffer.write(render_frame(canvas, color_code))
        sys.stdout.buffer.write(b"\033[0m")
        sys.stdout.buffer.flush()
        time.sleep(0.3)

    # Character-by-character printing with random glitch
    displayed: list[str] = []
    for ch in q:
        displayed.append(ch)

        # Collect current line state (with random glitches on already printed)
        line_parts = []
        for c in displayed:
            line_parts.append(c if random() > 0.15 else glitch_char())

        canvas.clear()
        base = y * term.width + x
        for j, c in enumerate(line_parts):
            canvas.grid[base + j] = c
            canvas.attr[base + j] = 1  # bold

        # Blinking cursor (block) at the end of printed text
        cursor_x = x + len(displayed)
        if cursor_x < term.width:
            canvas.grid[y * term.width + cursor_x] = "\u2588"  # full block
            canvas.attr[y * term.width + cursor_x] = 1

        sys.stdout.buffer.write(b"\033[H")
        sys.stdout.buffer.write(render_frame(canvas, color_code))
        sys.stdout.buffer.write(b"\033[0m")
        sys.stdout.buffer.flush()
        time.sleep(_TYPE_SPEED)

    # 3 dots appear one by one, each second (3 second countdown)
    dots = ""
    for _ in range(3):
        dots += "."
        canvas.clear()
        base = y * term.width + x
        for j, ch in enumerate(q):
            canvas.grid[base + j] = ch
            canvas.attr[base + j] = 1
        for j, dot in enumerate(dots):
            canvas.grid[base + len(q) + j] = dot
            canvas.attr[base + len(q) + j] = 1

        sys.stdout.buffer.write(b"\033[H")
        sys.stdout.buffer.write(render_frame(canvas, color_code))
        sys.stdout.buffer.write(b"\033[0m")
        sys.stdout.buffer.flush()
        time.sleep(1.0)

    # Another 1 second full text with three dots hangs before disappearing
    time.sleep(1.0)

    # If hack — final random glitches across the screen
    if is_hack:
        for _ in range(term.width * term.height // 3):
            rx = randint(0, term.width - 1)
            ry = randint(0, term.height - 1)
            canvas.grid[ry * term.width + rx] = glitch_char()
            canvas.attr[ry * term.width + rx] = 1
        sys.stdout.buffer.write(b"\033[2J\033[H")
        sys.stdout.buffer.write(render_frame(canvas, color_code))
        sys.stdout.buffer.write(b"\033[0m")
        sys.stdout.buffer.flush()
        time.sleep(0.5)


def matrix() -> None:
    """Infinite matrix rain — runs until KeyboardInterrupt."""
    global _resize_pending
    term = Terminal()
    if not term._is_tty:
        print("Matrix effect requires a TTY terminal.", file=sys.stderr)
        return

    # Prepare stdin for non-blocking single-key reads without echo.
    stdin_fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    old_attrs = None
    if stdin_fd is not None:
        old_attrs = termios.tcgetattr(stdin_fd)
        new_attrs = termios.tcgetattr(stdin_fd)
        new_attrs[3] &= ~(termios.ECHO | termios.ICANON)  # lflags
        new_attrs[6][termios.VMIN] = 0
        new_attrs[6][termios.VTIME] = 0
        try:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, new_attrs)
        except termios.error:
            old_attrs = None

    signal.signal(signal.SIGWINCH, _handle_resize)

    canvas = Canvas(width=term.width, height=term.height)
    columns = create_columns(term.width // 4, term.width, term.height, _SYMBOLS)

    # Clear screen, hide cursor, switch to alternate buffer
    # so accidental trackpad scroll does not move the main terminal screen.
    sys.stdout.buffer.write(b"\033[2J\033[?25l\033[?1049h\033[H")
    # Enable mouse reporting so iTerm2 does not start native text selection
    # on left-click (which freezes the screen and scrolls down).
    sys.stdout.buffer.write(
        b"\033[?1000h\033[?1002h\033[?1003h\033[?1006h\033[?1015h\033[?1016h"
    )
    sys.stdout.buffer.flush()

    # Flush any stale input that arrived before we switched modes.
    if stdin_fd is not None:
        try:
            termios.tcflush(stdin_fd, termios.TCIFLUSH)
        except termios.error:
            pass

    color_index = 0
    paused = False

    # Show a random movie quote with glitch effect.
    if random() < 1.0 / _HACK_CHANCE:
        show_quote(term, canvas, _COLORS[3], choice(_HACK_QUOTES), is_hack=True)
    else:
        show_quote(term, canvas, _COLORS[color_index], choice(_QUOTES))

    try:
        while True:
            # Handle terminal resize
            if _resize_pending:
                _resize_pending = False
                term = Terminal()
                canvas = Canvas(width=term.width, height=term.height)
                columns = create_columns(
                    term.width // 4, term.width, term.height, _SYMBOLS
                )
                sys.stdout.buffer.write(b"\033[2J\033[H")
                sys.stdout.buffer.flush()
                continue

            # Handle keypresses
            keys = _get_keys(stdin_fd) if stdin_fd is not None else ""
            for key in keys:
                if key in ("\x03", "\x1b", "q", "й", "Q", "Й"):
                    raise KeyboardInterrupt
                elif key == "0":
                    columns = create_columns(
                        term.width // 4, term.width, term.height, _SYMBOLS
                    )
                    canvas.clear()
                    if random() < 1.0 / _HACK_CHANCE:
                        show_quote(
                            term, canvas, _COLORS[3], choice(_HACK_QUOTES), is_hack=True
                        )
                    else:
                        show_quote(term, canvas, _COLORS[color_index], choice(_QUOTES))
                    continue
                elif key in _MODES:
                    columns = create_columns(
                        term.width // 4, term.width, term.height, _MODES[key]
                    )
                    canvas.clear()
                elif key in ("-", "_"):
                    color_index = (color_index + 1) % len(_COLORS)
                elif key == " ":
                    paused = not paused

            if not paused:
                # Clear screen + reset cursor every frame
                sys.stdout.buffer.write(b"\033[2J\033[H")
                canvas.clear()

                for col in columns:
                    col.flicker()
                    chars = col.visible_text()
                    bright = col.visible_bright()
                    # Build intensities: head bright, tail dim, random flashes
                    intensities = [0] * len(chars)
                    if chars:
                        intensities[0] = 1  # head bright
                        if len(chars) > 1:
                            intensities[-1] = 2  # tail dim
                        for i in bright:
                            intensities[i] = 1
                    canvas.set_vertical(col.x, col.y, chars, intensities)
                    col.move()

                sys.stdout.buffer.write(render_frame(canvas, _COLORS[color_index]))
                sys.stdout.buffer.write(b"\033[0m")
                sys.stdout.buffer.flush()

            time.sleep(0.05)
    finally:
        # Disable mouse reporting.
        sys.stdout.buffer.write(
            b"\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1015l\033[?1016l"
        )
        # Return to the main screen buffer and show the cursor.
        sys.stdout.buffer.write(b"\033[?1049l\033[?25h")
        sys.stdout.buffer.flush()
        # Restore terminal echo settings.
        if old_attrs is not None and stdin_fd is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
            except termios.error:
                pass


def main() -> None:
    """Entry point — runs matrix rain until interrupted."""

    def _handler(sig: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)

    while True:
        try:
            matrix()
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
