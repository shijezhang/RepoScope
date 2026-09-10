"""Diagnostic test template; executed only through the registered Docker profile."""

from click import _compat
from click.testing import CliRunner


def test_isolation_restores_ansi_hook():
    original = _compat.should_strip_ansi
    with CliRunner().isolation():
        pass
    assert _compat.should_strip_ansi is original
