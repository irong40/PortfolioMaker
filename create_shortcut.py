"""Create a Windows desktop shortcut for Portfolio Maker."""

import os
import sys
from pathlib import Path

try:
    import winshell
    from win32com.client import Dispatch
except ImportError:
    sys.exit("pip install winshell pywin32")

SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable.replace("python.exe", "pythonw.exe")
TARGET_SCRIPT = SCRIPT_DIR / "portfolio_maker.pyw"
ICON_FILE = SCRIPT_DIR / "portfolio_maker.ico"


def main():
    desktop = winshell.desktop()
    shortcut_path = os.path.join(desktop, "Portfolio Maker.lnk")

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(shortcut_path)
    shortcut.Targetpath = PYTHON_EXE
    shortcut.Arguments = f'"{TARGET_SCRIPT}"'
    shortcut.WorkingDirectory = str(SCRIPT_DIR)
    shortcut.Description = "Sentinel Portfolio Maker — Sort drone photos"
    if ICON_FILE.exists():
        shortcut.IconLocation = str(ICON_FILE)
    shortcut.save()

    print(f"Shortcut created: {shortcut_path}")


if __name__ == "__main__":
    main()
