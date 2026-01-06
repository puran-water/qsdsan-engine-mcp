"""
Path normalization utilities for Windows/WSL interoperability.

When running in WSL but using Windows Python (e.g., from a Windows venv),
paths need to be converted from Windows format (C:/Users/...) to WSL format
(/mnt/c/Users/...) for subprocess execution.
"""

import sys
import os
from pathlib import Path
from typing import Union


def is_wsl() -> bool:
    """Check if running in Windows Subsystem for Linux."""
    if sys.platform != 'linux':
        return False

    # Check for WSL-specific indicators
    try:
        with open('/proc/version', 'r') as f:
            version = f.read().lower()
            return 'microsoft' in version or 'wsl' in version
    except (IOError, OSError):
        return False


def normalize_path_for_wsl(path: Union[str, Path, None]) -> str:
    r"""
    Convert Windows paths to WSL paths if running in WSL.

    Examples:
        C:\Users\hvksh\... -> /mnt/c/Users/hvksh/...
        D:\Projects\... -> /mnt/d/Projects/...
        jobs\a9eaada2\file.json -> jobs/a9eaada2/file.json

    Args:
        path: A file path (string or Path object)

    Returns:
        Normalized path string suitable for WSL execution
    """
    if path is None:
        return ""

    path_str = str(path)

    if not path_str:
        return path_str

    # Only normalize if we're in WSL
    if not is_wsl():
        return path_str

    # Check if this is a Windows absolute path (e.g., C:\Users\...)
    if len(path_str) >= 2 and path_str[1] == ':':
        drive = path_str[0].lower()
        rest = path_str[2:].replace('\\', '/')
        return f'/mnt/{drive}{rest}'

    # Convert backslashes to forward slashes for relative paths
    return path_str.replace('\\', '/')


def get_python_executable() -> str:
    """
    Get Python executable path, normalized for WSL if needed.

    Returns:
        Path to Python interpreter, converted to WSL format if running in WSL
    """
    return normalize_path_for_wsl(sys.executable)


def normalize_command(cmd: list) -> list:
    """
    Normalize all path-like arguments in a command list.

    Args:
        cmd: List of command arguments

    Returns:
        List with all Windows paths converted to WSL format
    """
    return [normalize_path_for_wsl(arg) for arg in cmd]
