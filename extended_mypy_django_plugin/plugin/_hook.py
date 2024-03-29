from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Generic, TypeAlias, TypeVar, overload

from mypy.plugin import Plugin

T_Ctx = TypeVar("T_Ctx")
T_Ret = TypeVar("T_Ret")
T_Plugin = TypeVar("T_Plugin", bound=Plugin)


_HookChooser: TypeAlias = Callable[[str], Callable[[T_Ctx], T_Ret] | None]


class Hook(Generic[T_Plugin, T_Ctx, T_Ret], abc.ABC):
    def __init__(
        self,
        plugin: T_Plugin,
        fullname: str,
        super_hook: Callable[[T_Ctx], T_Ret] | None,
    ) -> None:
        self.plugin = plugin
        self.fullname = fullname
        self.super_hook = super_hook
        self.extra_init()

    def extra_init(self) -> None: ...

    @abc.abstractmethod
    def run(self, ctx: T_Ctx) -> T_Ret: ...

    @abc.abstractmethod
    def choose(self) -> bool: ...

    def hook(self) -> Callable[[T_Ctx], T_Ret] | None:
        if self.choose():
            return self.run
        else:
            return self.super_hook


class hook(Generic[T_Plugin, T_Ctx, T_Ret]):
    def __init__(self, hook: type[Hook[T_Plugin, T_Ctx, T_Ret]]) -> None:
        self.hook = hook

    name: str

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: None) -> hook[T_Plugin, T_Ctx, T_Ret]: ...

    @overload
    def __get__(self, instance: T_Plugin, owner: type[T_Plugin]) -> _HookChooser[T_Ctx, T_Ret]: ...

    def __get__(
        self, instance: T_Plugin | None, owner: type[T_Plugin] | None = None
    ) -> hook[T_Plugin, T_Ctx, T_Ret] | _HookChooser[T_Ctx, T_Ret]:
        if instance is None:
            return self

        super_hook = getattr(super(owner, instance), self.name)

        def hook(fullname: str) -> Callable[[T_Ctx], T_Ret] | None:
            return self.hook(
                plugin=instance,
                fullname=fullname,
                super_hook=super_hook(fullname),
            ).hook()

        return hook
