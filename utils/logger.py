"""
utils/logger.py
───────────────
Centralized logger using Rich for beautiful terminal output.

WHY RICH?
  - Color-coded by component (Orchestrator=blue, YouTube=red, GitHub=green)
  - Timestamps on every log line
  - Tables, panels, progress bars all built-in
  - Industry standard for Python CLI tools
"""

from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel
from rich.table import Table
from rich import box
from datetime import datetime


# Define a color theme for each component
_theme = Theme({
    "orchestrator": "bold cyan",
    "youtube":      "bold red",
    "github":       "bold green",
    "memory":       "bold magenta",
    "success":      "bold green",
    "error":        "bold red",
    "warning":      "bold yellow",
    "info":         "dim white",
})

console = Console(theme=_theme)


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Logger:
    """
    Component-aware logger. Each component gets its own color and prefix.

    Usage:
        log = Logger("YouTube Agent")
        log.info("Fetching transcript...")
        log.success("Transcript fetched — 4200 words")
        log.error("Video has no captions")
    """

    def __init__(self, component: str):
        self.component = component
        # Map component names to theme colors
        self._color_map = {
            "Orchestrator":  "orchestrator",
            "YouTube Agent": "youtube",
            "GitHub Agent":  "github",
            "Memory":        "memory",
        }
        self._color = self._color_map.get(component, "info")

    def _prefix(self) -> str:
        return f"[{self._color}][{_timestamp()}] [{self.component}][/{self._color}]"

    def info(self, message: str):
        console.print(f"{self._prefix()} {message}")

    def success(self, message: str):
        console.print(f"{self._prefix()} [success]✓ {message}[/success]")

    def error(self, message: str):
        console.print(f"{self._prefix()} [error]✗ {message}[/error]")

    def warning(self, message: str):
        console.print(f"{self._prefix()} [warning]⚠ {message}[/warning]")

    def thinking(self, message: str):
        """Used when the LLM is processing — visually distinct."""
        console.print(f"{self._prefix()} [dim italic]🤔 {message}[/dim italic]")


def print_banner():
    """Prints the startup banner when the app launches."""
    console.print(Panel.fit(
        "[bold cyan]YouTube ↔ GitHub AI Orchestrator[/bold cyan]\n"
        "[dim]Powered by Claude Sonnet 4.6[/dim]\n"
        "[dim]Type a YouTube URL to summarize, or 'help' for commands[/dim]",
        box=box.DOUBLE_EDGE,
        border_style="cyan"
    ))


def print_summary_panel(summary: dict):
    """Prints a formatted summary in a Rich panel."""
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    table.add_column("Field",  style="bold cyan", width=22)
    table.add_column("Value",  style="white")

    table.add_row("Title",           summary.get("title", "—"))
    table.add_row("Difficulty",      summary.get("difficulty_level", "—"))
    table.add_row("Target Audience", summary.get("target_audience", "—"))
    table.add_row("Transcript Words",str(summary.get("transcript_length", 0)))

    console.print(Panel(table, title="[bold green]Technical Summary[/bold green]", border_style="green"))

    # Key points
    if summary.get("key_points"):
        console.print("\n[bold cyan]Key Points:[/bold cyan]")
        for point in summary["key_points"]:
            console.print(f"  [green]•[/green] {point}")

    # Technical concepts
    if summary.get("technical_concepts"):
        console.print("\n[bold cyan]Technical Concepts:[/bold cyan]")
        console.print("  " + ", ".join(summary["technical_concepts"]))

    # Tools mentioned
    if summary.get("tools_mentioned"):
        console.print("\n[bold cyan]Tools & Libraries:[/bold cyan]")
        console.print("  " + ", ".join(summary["tools_mentioned"]))


def print_help():
    """Prints available commands."""
    table = Table(title="Available Commands", box=box.ROUNDED, border_style="cyan")
    table.add_column("Command",     style="bold yellow", width=35)
    table.add_column("Description", style="white")

    table.add_row("<YouTube URL>",              "Fetch transcript and generate technical summary")
    table.add_row("push <filename>",            "Push the last summary to GitHub")
    table.add_row("fetch <filename>",           "Fetch a file from your GitHub repo")
    table.add_row("list",                       "List all summaries saved in memory")
    table.add_row("history",                    "Show task history from memory")
    table.add_row("help",                       "Show this help message")
    table.add_row("exit / quit",                "Exit the application")

    console.print(table)
