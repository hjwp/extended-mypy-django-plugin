import inspect
import os
import platform
import shlex
import sys
import typing as tp
from collections.abc import Callable
from pathlib import Path

here = Path(__file__).parent

if platform.system() == "Windows":
    import mslex  # type:ignore[import-not-found]

    shlex = mslex


class Command:
    __is_command__: bool

    def __call__(self, bin_dir: Path, args: list[str]) -> None: ...


def command(func: Callable[..., None]) -> Callable[..., None]:
    tp.cast(Command, func).__is_command__ = True
    return func


def run(*args: str | Path) -> None:
    cmd = " ".join(shlex.quote(str(part)) for part in args)
    print(f"Running '{cmd}'\n")  # noqa: T201
    ret = os.system(cmd)
    if ret != 0:
        sys.exit(1)


class App:
    commands: dict[str, Command]

    def __init__(self) -> None:
        self.commands = {}

        compare = inspect.signature(type("C", (Command,), {})().__call__)

        for name in dir(self):
            val = getattr(self, name)
            if getattr(val, "__is_command__", False):
                assert (
                    inspect.signature(val) == compare
                ), f"Expected '{name}' to have correct signature, have {inspect.signature(val)} instead of {compare}"
                self.commands[name] = val

    def __call__(self, args: list[str]) -> None:
        bin_dir = Path(sys.executable).parent

        if args and args[0] in self.commands:
            os.chdir(here.parent)
            self.commands[args[0]](bin_dir, args[1:])
            return

        sys.exit(f"Unknown command:\nAvailable: {sorted(self.commands)}\nWanted: {args}")

    @command
    def lint(self, bin_dir: Path, args: list[str]) -> None:
        run(bin_dir / "ruff", "check", *args)

    @command
    def format(self, bin_dir: Path, args: list[str]) -> None:
        if not args:
            args = [".", *args]
        run(bin_dir / "ruff", "format", *args)
        run(bin_dir / "ruff", "check", "--fix", "--select", "I,UP", *args)

    @command
    def types(self, bin_dir: Path, args: list[str]) -> None:
        if not any(not a.startswith("-") for a in args):
            args.append(str((here / "..").resolve()))

        run(bin_dir / "mypy", *args)


app = App()

if __name__ == "__main__":
    app(sys.argv[1:])
