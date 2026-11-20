"""Compose the Windows host scripts with the shared guest credential reader."""

import os
from pathlib import Path


def windows_powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def read_host_script(*paths: Path) -> str:
    helper = Path(__file__).with_name("harness_guest_credential.ps1")
    return "\n".join(path.read_text(encoding="utf-8") for path in (helper, *paths))
