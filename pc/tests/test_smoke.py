def test_package_version() -> None:
    import turntable_control

    assert turntable_control.__version__ == "0.1.0"


def test_console_entry_is_import_safe() -> None:
    from turntable_control.main import main

    assert callable(main)


def test_pyinstaller_entry_is_import_safe() -> None:
    import turntable_control_entry

    assert callable(turntable_control_entry.main)
