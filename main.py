"""
main.py
───────
Entry point for the YouTube ↔ GitHub AI Orchestrator.

Run with:
    py main.py

Flow:
  1. Load environment variables from .env
  2. Validate that required API keys are present
  3. Initialize the Orchestrator (which initializes Agents + Memory)
  4. Start the interactive input loop
  5. Pass each user input to orchestrator.handle()
  6. Display the response
  7. Repeat until user types 'exit'
"""

import sys
import os
from dotenv import load_dotenv
from rich.console import Console

# Load .env file FIRST before any other imports that need env vars
load_dotenv()

from orchestrator.orchestrator import Orchestrator
from utils.logger import print_banner

console = Console()


def validate_environment():
    """
    Checks that all required environment variables are set before starting.
    Fails fast with a clear message rather than crashing mid-task.
    """
    required = {
        "ANTHROPIC_API_KEY": "Get from https://console.anthropic.com → API Keys",
        "GITHUB_TOKEN":      "Get from GitHub → Settings → Developer Settings → PAT (classic)",
        "GITHUB_USERNAME":   "Your GitHub username (the @handle)",
    }

    missing = []
    for key, instructions in required.items():
        value = os.getenv(key)
        if not value or value.startswith("your_"):
            missing.append(f"  • {key}\n    → {instructions}")

    if missing:
        console.print("\n[bold red]Missing required environment variables:[/bold red]")
        for m in missing:
            console.print(m)
        console.print(
            "\n[yellow]Edit the [bold].env[/bold] file in the project folder and fill in your keys.[/yellow]\n"
        )
        sys.exit(1)


def main():
    """Main interactive loop."""

    # Step 1 — Validate environment
    validate_environment()

    # Step 2 — Print banner
    print_banner()

    # Step 3 — Initialize Orchestrator
    try:
        orchestrator = Orchestrator()
    except Exception as e:
        console.print(f"[bold red]Failed to start Orchestrator: {e}[/bold red]")
        sys.exit(1)

    console.print("\n[dim]Type 'help' to see available commands. Type 'exit' to quit.[/dim]\n")

    # Step 4 — Interactive loop
    while True:
        try:
            # Get user input
            user_input = console.input("[bold cyan]You → [/bold cyan]").strip()

            # Skip empty input
            if not user_input:
                continue

            # Exit commands
            if user_input.lower() in ("exit", "quit", "q", "bye"):
                console.print("[dim]Shutting down...[/dim]")
                orchestrator.shutdown()
                console.print("[bold green]Goodbye![/bold green]")
                break

            # Pass input to Orchestrator and display response
            response = orchestrator.handle(user_input)
            console.print(f"\n[bold green]Assistant →[/bold green] {response}\n")

        except KeyboardInterrupt:
            # Handle Ctrl+C gracefully
            console.print("\n[dim]Interrupted. Type 'exit' to quit.[/dim]")

        except Exception as e:
            console.print(f"\n[bold red]Unexpected error: {e}[/bold red]")
            console.print("[dim]The system is still running. Try again.[/dim]\n")


if __name__ == "__main__":
    main()
