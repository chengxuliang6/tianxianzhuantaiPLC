"""PyInstaller-safe top-level entry for the turntable desktop application."""

from turntable_control.main import main


if __name__ == "__main__":
    raise SystemExit(main())
