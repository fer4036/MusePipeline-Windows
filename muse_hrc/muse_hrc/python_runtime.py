"""Select a Python runtime containing the Athena and ROS2 dependencies."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


REQUIRED_IMPORTS = 'import rclpy; from muselsl.athena import Athena'


def interpreter_candidates(configured='auto', workspace_root=None, environ=None):
    """Return ordered, unique Python interpreter candidates."""
    environment = environ if environ is not None else os.environ
    candidates = []

    if configured and configured != 'auto':
        candidates.append(configured)

    virtual_env = environment.get('VIRTUAL_ENV')
    if virtual_env:
        candidates.append(str(Path(virtual_env) / 'bin' / 'python'))

    if workspace_root:
        candidates.append(str(Path(workspace_root) / 'muse_env' / 'bin' / 'python'))

    candidates.append(sys.executable)
    path_python = shutil.which('python3')
    if path_python:
        candidates.append(path_python)

    unique = []
    for candidate in candidates:
        expanded = str(Path(candidate).expanduser())
        if expanded not in unique:
            unique.append(expanded)
    return unique


def select_muse_python(configured='auto', workspace_root=None, environ=None):
    """Return the first interpreter able to import ROS2 and Athena."""
    failures = []
    for candidate in interpreter_candidates(configured, workspace_root, environ):
        if not Path(candidate).is_file():
            failures.append(f'{candidate}: no existe')
            continue
        try:
            result = subprocess.run(
                [candidate, '-c', REQUIRED_IMPORTS],
                capture_output=True,
                text=True,
                timeout=10,
                env=environ,
            )
        except (OSError, subprocess.SubprocessError) as error:
            failures.append(f'{candidate}: {error}')
            continue
        if result.returncode == 0:
            return candidate
        detail = (result.stderr or result.stdout).strip().splitlines()
        failures.append(
            f"{candidate}: {detail[-1] if detail else 'imports fallaron'}"
        )

    joined_failures = '; '.join(failures)
    raise RuntimeError(
        'No se encontró un Python con rclpy y muselsl.athena. '
        f'Intérpretes revisados: {joined_failures}'
    )
