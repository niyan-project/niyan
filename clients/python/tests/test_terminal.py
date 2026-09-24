import io
import unittest
from unittest.mock import patch

from niyan.terminal import is_interactive, select_lfs_extensions, status, success


class TerminalTests(unittest.TestCase):
    """Exercise interactive CLI presentation without requiring a real terminal."""

    def test_interactive_requires_input_and_diagnostic_terminals(self):
        """Avoid prompts when either side cannot support an interactive session."""

        terminal = _Stream(terminal=True)
        redirected = _Stream(terminal=False)
        self.assertTrue(is_interactive(stdin=terminal, stderr=terminal))
        self.assertFalse(is_interactive(stdin=redirected, stderr=terminal))
        self.assertFalse(is_interactive(stdin=terminal, stderr=redirected))

    def test_selector_renders_counts_and_returns_extension_values(self):
        """Keep display labels separate from the extensions consumed by staging."""

        prompt = _Prompt(['.jpg'])
        output = _Stream(terminal=True)
        prompt_output = object()
        with patch('niyan.terminal.create_output', return_value=prompt_output), patch('niyan.terminal.questionary.checkbox', return_value=prompt) as checkbox:
            selected = select_lfs_extensions({'.jpg': 18826, '.nii.gz': 1}, stderr=output)

        self.assertEqual(selected, ['.jpg'])
        choices = checkbox.call_args.kwargs['choices']
        self.assertEqual([(choice.title, choice.value) for choice in choices], [('.jpg  (18,826 files)', '.jpg'), ('.nii.gz  (1 file)', '.nii.gz')])
        self.assertIs(checkbox.call_args.kwargs['output'], prompt_output)

    def test_selector_turns_prompt_cancellation_into_keyboard_interrupt(self):
        """Give staging one consistent cancellation signal."""

        with patch('niyan.terminal.create_output'), patch('niyan.terminal.questionary.checkbox', return_value=_Prompt(None)):
            with self.assertRaises(KeyboardInterrupt):
                select_lfs_extensions({'.jpg': 1})

    def test_status_and_success_stay_silent_when_redirected(self):
        """Preserve machine-readable diagnostics outside terminals."""

        redirected = _Stream(terminal=False)
        with status('Working…', stderr=redirected):
            pass
        success('Done.', stderr=redirected)
        self.assertEqual(redirected.getvalue(), '')


class _Stream(io.StringIO):
    """String stream with a controlled terminal capability."""

    def __init__(self, *, terminal):
        super().__init__()
        self.terminal = terminal

    def isatty(self):
        """Return the configured terminal state."""

        return self.terminal


class _Prompt:
    """Small Questionary prompt double."""

    def __init__(self, answer):
        self.answer = answer

    def ask(self):
        """Return the configured answer."""

        return self.answer


if __name__ == '__main__':
    unittest.main()
