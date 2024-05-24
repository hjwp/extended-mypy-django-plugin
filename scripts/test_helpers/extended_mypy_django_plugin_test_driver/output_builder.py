from collections.abc import Iterator

from pytest_mypy_plugins import OutputMatcher
from pytest_mypy_plugins.utils import DaemonOutputMatcher, FileOutputMatcher
from typing_extensions import Self


class _Build:
    def __init__(self, for_daemon: bool) -> None:
        self.for_daemon = for_daemon
        self.result: list[OutputMatcher] = []
        self.daemon_should_restart: bool = False

    def add(
        self,
        path: str,
        lnum: int,
        col: int | None,
        severity: str,
        message: str,
        regex: bool = False,
    ) -> None:
        fname = path.removesuffix(".py")
        self.result.append(
            FileOutputMatcher(
                fname, lnum, severity, message, regex=regex, col=None if col is None else str(col)
            )
        )


class OutputBuilder:
    def __init__(
        self,
        build: _Build | None = None,
        target_file: str | None = None,
        for_daemon: bool | None = False,
    ) -> None:
        if build is None:
            assert for_daemon is not None
            build = _Build(for_daemon=for_daemon)
        self._build = build

        self.target_file = target_file

    def clear(self) -> Self:
        self._build.result.clear()
        return self

    def daemon_should_restart(self) -> Self:
        self._build.daemon_should_restart = True
        return self

    def daemon_should_not_restart(self) -> Self:
        self._build.daemon_should_restart = False
        return self

    def on(self, path: str) -> Self:
        return self.__class__(build=self._build, target_file=path)

    def add_revealed_type(self, lnum: int, revealed_type: str) -> Self:
        assert self.target_file is not None
        self._build.add(
            self.target_file, lnum, None, "note", f'Revealed type is "{revealed_type}"'
        )
        return self

    def change_revealed_type(self, lnum: int, message: str) -> Self:
        assert self.target_file is not None

        found: list[FileOutputMatcher] = []
        for matcher in self._build.result:
            if (
                isinstance(matcher, FileOutputMatcher)
                and matcher.fname == self.target_file.removesuffix(".py")
                and matcher.lnum == lnum
                and matcher.severity == "note"
                and matcher.message.startswith("Revealed type is")
            ):
                found.append(matcher)

        assert len(found) == 1
        found[0].message = f'Revealed type is "{message}"'
        return self

    def remove_errors(self, lnum: int) -> Self:
        assert self.target_file is not None

        i: int = -1
        while i < len(self._build.result):
            if i >= len(self._build.result):
                break

            nxt = self._build.result[i]
            if (
                isinstance(nxt, FileOutputMatcher)
                and nxt.fname == self.target_file.removesuffix(".py")
                and nxt.lnum == lnum
                and nxt.severity == "error"
            ):
                self._build.result.pop(i)
            else:
                i += 1

        return self

    def add_error(self, lnum: int, error_type: str, message: str) -> Self:
        assert self.target_file is not None
        self._build.add(self.target_file, lnum, None, "error", f"{message}  [{error_type}]")
        return self

    def remove_from_revealed_type(self, lnum: int, remove: str) -> Self:
        assert self.target_file is not None

        found: list[FileOutputMatcher] = []
        for matcher in self._build.result:
            if (
                isinstance(matcher, FileOutputMatcher)
                and matcher.fname == self.target_file.removesuffix(".py")
                and matcher.lnum == lnum
                and matcher.severity == "note"
                and matcher.message.startswith("Revealed type is")
            ):
                found.append(matcher)

        assert len(found) == 1
        found[0].message = found[0].message.replace(remove, "")
        return self

    def __iter__(self) -> Iterator[OutputMatcher]:
        if self._build.daemon_should_restart and self._build.for_daemon:
            yield DaemonOutputMatcher(line="Restarting: plugins changed", regex=False)
            yield DaemonOutputMatcher(line="Daemon stopped", regex=False)
        yield from self._build.result
