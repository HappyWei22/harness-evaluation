"""Save and independently verify the dedicated Windows VM credential."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from host_powershell import read_host_script, windows_powershell


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Replace the saved credential after authenticating")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Register the VM credential on the Windows Hyper-V host.")
    script = read_host_script(Path(__file__).with_suffix(".ps1"))
    command = [str(windows_powershell()), "-NoLogo", "-NoProfile"]
    register_env = {**os.environ, "HARNESS_GUEST_CREDENTIAL_SETUP": "refresh" if args.refresh else "register"}
    result = subprocess.run([*command, "-Command", script], env=register_env, check=False)
    if result.returncode:
        return result.returncode
    verify_env = {**os.environ, "HARNESS_GUEST_CREDENTIAL_SETUP": "verify"}
    return subprocess.run([*command, "-NonInteractive", "-Command", script], env=verify_env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
