"""Status screen for the running characters: who's online, what each one is doing, how full her context is,
whose turn it is in each channel, and the log at the bottom. Drawn with Rich, which works in the Windows
console (Python's curses module doesn't exist on Windows)."""
import asyncio
import logging
import math
import sys
import time
from collections import deque
from datetime import datetime

from rich import box
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

REFRESH_SECONDS = 0.5
COUNT_RECHECK_SECONDS = 5   # how often the message counts are read from the databases
LOG_LINES = 2000            # kept in memory, and printed again after quitting
TURNS_SHOWN = 4             # channels in the turns panel
TURNS_MAX_AGE = 15 * 60     # seconds before a quiet channel drops out of it
COLORS = ("gold1", "medium_purple1", "dark_orange", "deep_sky_blue1", "spring_green2", "hot_pink")
LEVEL_STYLES = {logging.DEBUG: "grey50", logging.INFO: "", logging.WARNING: "yellow", logging.ERROR: "red",
                logging.CRITICAL: "bold red"}


def enable_vt_mode() -> None:
    """Let the classic Windows console understand the escape codes Rich draws with (Windows Terminal
    always does)."""
    if sys.platform != "win32":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # stdout
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def short_number(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def ago(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


class LogBuffer(logging.Handler):
    """Keeps the latest log lines for the screen."""

    def __init__(self):
        super().__init__()
        self.lines: deque[tuple[str, int, str, str, bool]] = deque(maxlen=LOG_LINES)
        self.plain = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + self.plain.formatException(record.exc_info)
        except Exception:
            self.handleError(record)
            return
        self.add(record.name, record.levelno, text, record.created)

    def add(self, name: str, level: int, text: str, created: float | None = None) -> None:
        stamp = datetime.fromtimestamp(created or time.time()).strftime("%H:%M:%S")
        with self.lock:
            for i, line in enumerate(text.splitlines() or [""]):
                self.lines.append((stamp, level, name, line, i > 0))

    def tail(self, count: int) -> list[tuple[str, int, str, str, bool]]:
        with self.lock:
            return list(self.lines)[-count:] if count > 0 else []

    @staticmethod
    def styled(entry: tuple[str, int, str, str, bool]) -> Text:
        stamp, level, name, line, continued = entry
        style = LEVEL_STYLES.get(level, "")
        if continued:  # second and later lines of a traceback or multi-line message
            return Text("         " + line, style=style or "grey62")
        word = logging.getLevelName(level)[:4]
        return Text.assemble((stamp, "grey50"), " ", (f"{word:<4}", style or "green"), " ",
                             (name, "cyan"), ": ", (line, style))


class _ToLog:
    """Stands in for stdout/stderr while the screen is up, so stray prints end up in the log instead of
    scribbling over the screen."""
    encoding = "utf-8"

    def __init__(self, buffer: LogBuffer, level: int):
        self.buffer, self.level, self.partial = buffer, level, ""

    def write(self, text: str) -> int:
        self.partial += text
        *lines, self.partial = self.partial.split("\n")
        for line in lines:
            if line.strip():
                self.buffer.add("console", self.level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class LogView:
    """The newest log lines that fit in the space it gets."""

    def __init__(self, buffer: LogBuffer):
        self.buffer = buffer

    def __rich_console__(self, console, options):
        height = options.height or 10
        texts = [LogBuffer.styled(e) for e in self.buffer.tail(height)]
        if not texts:
            return
        lines = console.render_lines(Group(*texts), options.update(height=None), pad=False)
        for line in lines[-height:]:  # long lines wrap, so keep only the bottom ones
            yield from line
            yield Segment.line()


class FitWidth:
    """Builds its content when it's drawn, for the width it actually gets."""

    def __init__(self, build):
        self.build = build

    def __rich_console__(self, console, options):
        yield self.build(options.max_width)


class StatusScreen:
    """Full-window status screen. Use as a context manager around the bots' event loop, and run refresh()
    as a task inside it."""

    def __init__(self, bots: list, skipped: dict[str, str], director=None):
        self.bots = bots
        self.skipped = skipped  # character name -> why she isn't running
        self.director = director
        self.log = LogBuffer()
        self.started = time.time()
        self.counts: dict[int, tuple[float, tuple[int, int]]] = {}
        self.render_failed = False
        enable_vt_mode()
        self.console = Console(file=sys.stdout)  # bound now: sys.stdout is swapped out while the screen is up
        self.live = Live(console=self.console, screen=True, auto_refresh=False, redirect_stdout=False,
                         redirect_stderr=False)

    def __enter__(self):
        root = logging.getLogger()
        self.old_handlers = root.handlers[:]
        root.handlers = [self.log]
        root.setLevel(logging.INFO)
        self.streams = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _ToLog(self.log, logging.INFO), _ToLog(self.log, logging.WARNING)
        self.live.start()
        self.draw()
        return self

    def __exit__(self, *exc):
        self.live.stop()
        sys.stdout, sys.stderr = self.streams
        for entry in self.log.tail(LOG_LINES):  # the screen is gone: leave the log behind in the window
            self.console.print(LogBuffer.styled(entry), soft_wrap=True, highlight=False)
        root = logging.getLogger()
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-4.4s %(name)s: %(message)s", "%H:%M:%S"))
        root.handlers = [handler]
        return False

    async def refresh(self) -> None:
        while True:
            self.draw()
            await asyncio.sleep(REFRESH_SECONDS)

    def draw(self) -> None:
        try:
            self.live.update(self.render(), refresh=True)
        except Exception:
            if not self.render_failed:  # once, not twice a second
                self.render_failed = True
                logging.getLogger("tui").exception("Couldn't draw the status screen")

    # --- layout ----------------------------------------------------------------------------------

    def render(self) -> Layout:
        rows = len(self.bots) + len(self.skipped)
        parts = [Layout(self.header(), size=1),
                 Layout(Panel(FitWidth(self.characters), title="Characters", title_align="left",
                              border_style="grey50"), size=rows + 4)]
        if self.director:
            turns = self.turns()
            parts.append(Layout(Panel(turns, title="Turns", title_align="left", border_style="grey50"),
                                size=max(1, turns.row_count) + 2))
        parts.append(Layout(Panel(LogView(self.log), title="Log", title_align="left", border_style="grey50"),
                            ratio=1, minimum_size=4))
        parts.append(Layout(self.footer(), size=1))
        layout = Layout()
        layout.split_column(*parts)
        return layout

    def header(self) -> Table:
        llms = [bot.character.llm for bot in self.bots]
        text = Text.assemble((" VonikoDiscord ", "bold black on gold1"), " ")
        if llms:
            llm = llms[0]
            text.append(f"LM Studio {llm.root.split('://')[-1]}", "bold")
            if problem := next((c.problem for c in llms if c.problem), ""):
                text.append(f" · {problem}", "bold red")
            if llm.info:
                limit = llm.configured_context or llm.info.context
                text.append(f" · {llm.info.id}")
                text.append(" · vision" if llm.info.vision else " · text only", "grey62")
                text.append(f" · context {limit:,}" if limit else " · context size unknown", "grey62")
            effort = llm.params.get("reasoning_effort")
            text.append(" · thinking off" if effort == "none" else f" · thinking {effort or 'on'}", "grey62")
            latest = max((bot.character.stats for bot in self.bots), key=lambda st: st.last_reply_at)
            if latest.tokens_per_second:  # they all share the one model
                text.append(f" · {latest.tokens_per_second:.0f} tok/s", "grey62")
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(text, Text(datetime.now().strftime("%H:%M:%S "), "grey62"))
        return grid

    def footer(self) -> Table:
        uptime = int(time.time() - self.started)
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(Text.assemble((" Q ", "bold black on grey70"), " quit   ",
                                   ("the log is printed again after quitting", "grey50")),
                     Text(f"up {uptime // 3600}:{uptime // 60 % 60:02}:{uptime % 60:02} ", "grey50"))
        return grid

    def characters(self, width: int) -> Table:
        # Every column has a maximum width, and narrow windows drop the least important ones: Rich would
        # otherwise squeeze all columns evenly, and the short ones (like the names) would vanish.
        show_memory, show_replies = width >= 112, width >= 140
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, expand=True, pad_edge=False, header_style="bold grey70")
        table.add_column("Character", no_wrap=True, max_width=12)
        table.add_column("Discord", no_wrap=True, overflow="ellipsis", max_width=16)
        table.add_column("Doing", ratio=1, min_width=10, no_wrap=True, overflow="ellipsis")
        compact_at = self.bots[0].character.compact_at if self.bots else 0.75
        table.add_column(f"Context (compacts at {compact_at:.0%})", no_wrap=True, overflow="ellipsis", max_width=36)
        if show_memory:
            table.add_column("Memory", no_wrap=True, overflow="ellipsis", max_width=36 if show_replies else 24)
        if show_replies:
            table.add_column("Replies", no_wrap=True, overflow="ellipsis", max_width=28)
        for i, bot in enumerate(self.bots):
            character = bot.character
            stats = character.stats
            row = [Text(character.name, f"bold {COLORS[i % len(COLORS)]}"), self.discord(bot),
                   Text(" + ".join(character.doing)) if character.doing else Text("idle", "grey50"),
                   self.context(character)]
            if show_memory:
                messages, notes = self.db_counts(bot.db)
                memory = Text(f"compacted {stats.compactions}x · ", "cyan") if stats.compactions else Text()
                memory.append(f"{messages:,} msgs · {notes} note{'s' * (notes != 1)}")
                row.append(memory)
            if show_replies:
                replies = Text(str(stats.replies))
                if stats.last_reply_at:
                    replies.append(f" · {ago(stats.last_reply_at)}", "grey50")
                if stats.thinking_tokens:
                    replies.append(f" · thought {stats.thinking_tokens}", "grey50")
                row.append(replies)
            table.add_row(*row)
        for name, why in self.skipped.items():
            table.add_row(Text(name, "grey50"), Text("× not running", "red"), Text(why, "red"),
                          *[""] * (len(table.columns) - 3))
        return table

    @staticmethod
    def discord(bot) -> Text:
        if bot.problem:
            return Text(f"× {bot.problem}", "red")
        if bot.is_closed():
            return Text("● offline", "grey50")
        if bot.is_ready():
            ping = f" {bot.latency * 1000:.0f} ms" if math.isfinite(bot.latency) else ""
            return Text(f"● online{ping}", "green")
        return Text("● connecting", "yellow")

    @staticmethod
    def context(character) -> Text:
        """How full her latest prompt was: a bar against the model's context, colored by how close it is
        to compacting."""
        stats, llm = character.stats, character.llm
        limit = llm.configured_context or (llm.info.context if llm.info else None)
        if not stats.context_tokens:
            return Text("not measured yet" if not stats.compactions else "compacted; measured on next reply",
                        "grey50")
        if not limit:
            return Text(f"{short_number(stats.context_tokens)} tokens · {stats.context_place}")
        share = stats.context_tokens / limit
        style = "red" if share >= character.compact_at else "yellow" if share >= character.compact_at - 0.15 \
            else "green"
        filled = min(10, round(share * 10))
        text = Text.assemble(("█" * filled, style), ("░" * (10 - filled), "grey35"), f" {share:4.0%} ",
                             f"{short_number(stats.context_tokens)}/{short_number(limit)}")
        text.append(f" {stats.context_place}", "grey50")
        return text

    def turns(self) -> Table:
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(no_wrap=True, style="bold")
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True, style="grey50")
        now = time.monotonic()
        recent = sorted((s for s in self.director.state.values() if now - s[2] < TURNS_MAX_AGE),
                        key=lambda s: s[2], reverse=True)[:TURNS_SHOWN]
        for place, what, since in recent:
            grid.add_row(place, what, ago(time.time() - (now - since)))
        if not recent:
            grid.add_row("", Text("quiet everywhere", "grey50"), "")
        return grid

    def db_counts(self, db) -> tuple[int, int]:
        checked, counts = self.counts.get(id(db), (0.0, (0, 0)))
        if time.monotonic() - checked > COUNT_RECHECK_SECONDS:
            try:
                counts = db.counts()
            except Exception:  # closed while quitting
                pass
            self.counts[id(db)] = (time.monotonic(), counts)
        return counts
