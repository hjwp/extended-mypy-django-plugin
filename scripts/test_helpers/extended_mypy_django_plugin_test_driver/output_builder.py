from collections.abc import Iterator

from pytest_mypy_plugins import OutputMatcher
from pytest_mypy_plugins.utils import FileOutputMatcher
from typing_extensions import Self


class _Build:
    def __init__(self) -> None:
        self.result: list[OutputMatcher] = []

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
    ) -> None:
        if build is None:
            build = _Build()
        self._build = build

        self.target_file = target_file

    def clear(self) -> Self:
        self._build.result.clear()
        return self

    def on(self, path: str) -> Self:
        return self.__class__(build=self._build, target_file=path)

    def add_revealed_type(self, lnum: int, revealed_type: str) -> Self:
        assert self.target_file is not None
        self._build.add(
            self.target_file, lnum, None, "note", f'Revealed type is "{revealed_type}"'
        )
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
        yield from self._build.result
