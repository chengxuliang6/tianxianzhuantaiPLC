"""Safe console entry point for the development-stage application."""


def main() -> int:
    """Report that this development build performs no PLC communication."""
    print("软件仍在开发，不会连接或写入 PLC。")
    return 0
