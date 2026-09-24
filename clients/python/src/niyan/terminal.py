from contextlib import nullcontext
import sys

import questionary
from prompt_toolkit.output import create_output
from prompt_toolkit.styles import Style
from rich.console import Console


NIYAN_PROMPT_STYLE = Style.from_dict({
    'qmark': 'fg:#6366f1 bold',
    'question': 'bold',
    'pointer': 'fg:#6366f1 bold',
    'highlighted': 'fg:#6366f1 bold',
    'selected': 'fg:#6366f1',
    'instruction': 'fg:#71717a',
})


def is_interactive(*, stdin=None, stderr=None):
    """Return whether both input and diagnostics are attached to terminals.

    Parameters
    ----------
    stdin : file-like object, optional
        Input stream to inspect.
    stderr : file-like object, optional
        Diagnostic stream to inspect.
    """

    input_stream = stdin or sys.stdin
    error_stream = stderr or sys.stderr
    return bool(getattr(input_stream, 'isatty', lambda: False)() and getattr(error_stream, 'isatty', lambda: False)())


def select_lfs_extensions(extension_counts, *, stderr=None):
    """Ask which discovered file extensions Git LFS should track.

    Parameters
    ----------
    extension_counts : dict[str, int]
        Normalized extension names and their observed file counts.
    stderr : file-like object, optional
        Terminal stream receiving the interactive selector.
    """

    choices = [questionary.Choice(title=f'{extension}  ({count:,} {"file" if count == 1 else "files"})', value=extension) for extension, count in sorted(extension_counts.items())]
    selected = questionary.checkbox(
        'Select file formats for S3-backed Git LFS',
        choices=choices,
        instruction='(space toggle, enter confirm)',
        style=NIYAN_PROMPT_STYLE,
        output=create_output(stdout=stderr or sys.stderr),
    ).ask()
    if selected is None:
        raise KeyboardInterrupt
    return selected


def status(message, *, stderr=None, enabled=True):
    """Show a transient terminal spinner when interactive diagnostics are available.

    Parameters
    ----------
    message : str
        Human-readable operation description.
    stderr : file-like object, optional
        Diagnostic stream receiving the status.
    enabled : bool, optional
        Disable dynamic output for verbose commands.
    """

    output = stderr or sys.stderr
    if not enabled or not getattr(output, 'isatty', lambda: False)():
        return nullcontext()
    console = Console(file=output)
    return console.status(message, spinner='dots', spinner_style='bold #6366f1')


def success(message, *, stderr=None):
    """Write a concise completion message to an interactive terminal."""

    output = stderr or sys.stderr
    if getattr(output, 'isatty', lambda: False)():
        Console(file=output).print(f'[bold green]✓[/bold green] {message}')
