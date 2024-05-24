import textwrap
from collections.abc import Mapping
from typing import Protocol, TypedDict, overload

from pytest_mypy_plugins import File, FollowupFile, MypyPluginsConfig, MypyPluginsScenario
from pytest_mypy_plugins.scenario import Strategy
from typing_extensions import NotRequired, Unpack

from .output_builder import OutputBuilder


class RunArgs(TypedDict):
    start: NotRequired[list[str]]
    expect_fail: NotRequired[bool]
    additional_properties: NotRequired[Mapping[str, object]]
    copied_apps: NotRequired[list[str]]
    installed_apps: NotRequired[list[str]]
    debug: NotRequired[bool]


class Action(Protocol):
    def __call__(self, expected: OutputBuilder) -> None: ...


class _Runner:
    def __init__(
        self, scenario: "Scenario", output_builder: OutputBuilder, **kwargs: Unpack[RunArgs]
    ) -> None:
        self.scenario = scenario
        self.output_builder = output_builder
        self.run_kwargs = kwargs

    def __call__(self, action: Action, /) -> None:
        action(self.output_builder)
        return self.scenario.run_and_check_mypy(self.output_builder, **self.run_kwargs)


class Scenario:
    def __init__(self, config: MypyPluginsConfig, scenario: MypyPluginsScenario) -> None:
        self.config = config
        self.scenario = scenario
        self.expected = OutputBuilder(for_daemon=self.config.strategy is Strategy.DAEMON)

    def make_file(self, path: str, content: str) -> File:
        file = File(path=path, content=textwrap.dedent(content).lstrip())
        self.scenario.make_file(file)
        return file

    def update_file(self, path: str, content: str | None) -> FollowupFile:
        if isinstance(content, str):
            content = textwrap.dedent(content).lstrip()
        file = FollowupFile(path=path, content=content)
        self.scenario.handle_followup_file(file)
        return file

    def run_and_check_mypy(
        self, expected_output: OutputBuilder, **kwargs: Unpack[RunArgs]
    ) -> None:
        start = kwargs.get("start", ["."])

        extra_properties: dict[str, object] = {}

        if "installed_apps" in kwargs:
            extra_properties["installed_apps"] = kwargs["installed_apps"]

        if "debug" in kwargs:
            extra_properties["debug"] = kwargs["debug"]

        if "copied_apps" in kwargs:
            extra_properties["copied_apps"] = kwargs["copied_apps"]

        return self.scenario.run_and_check_mypy(
            start,
            expect_fail=kwargs.get("expect_fail", False),
            expected_output=list(expected_output),
            additional_properties={**kwargs.get("additional_properties", {}), **extra_properties},
        )

    @overload
    def run_and_check_mypy_after(
        self, action: None = None, /, **kwargs: Unpack[RunArgs]
    ) -> _Runner: ...

    @overload
    def run_and_check_mypy_after(self, action: Action, /, **kwargs: Unpack[RunArgs]) -> None: ...

    def run_and_check_mypy_after(
        self, action: Action | None = None, /, **kwargs: Unpack[RunArgs]
    ) -> _Runner | None:
        runner = _Runner(self, self.expected, **kwargs)
        if action is None:
            return runner
        else:
            runner(action)
            return None
