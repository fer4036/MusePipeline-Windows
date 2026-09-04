"""Tests for Athena Python runtime discovery."""

from muse_hrc.python_runtime import interpreter_candidates


def test_configured_interpreter_takes_priority():
    candidates = interpreter_candidates(
        '/custom/python',
        '/workspace',
        {'VIRTUAL_ENV': '/active/env'},
    )

    assert candidates[:3] == [
        '/custom/python',
        '/active/env/bin/python',
        '/workspace/muse_env/bin/python',
    ]


def test_auto_uses_workspace_environment_before_system_python():
    candidates = interpreter_candidates(
        'auto',
        '/workspace',
        {},
    )

    assert candidates[0] == '/workspace/muse_env/bin/python'
    assert len(candidates) == len(set(candidates))
