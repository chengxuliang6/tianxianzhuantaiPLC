def test_package_version() -> None:
    import turntable_control

    assert turntable_control.__version__ == "0.1.0"


def test_console_entry_reports_safe_development_status(capsys) -> None:
    from turntable_control.main import main

    assert main() == 0
    assert "不会连接或写入 PLC" in capsys.readouterr().out
