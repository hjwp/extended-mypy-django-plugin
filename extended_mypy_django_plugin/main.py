from typing import cast

from mypy.options import Options
from mypy.plugin import Plugin as MypyPlugin

from .plugin import ExtendedMypyStubs

# This lets us work out if we're in dmypy
# dmypy will recall "plugin" below which will
# make a new plugin, but without necessarily replacing
# existing plugin hooks, which is confusing
created: bool = False


def plugin(version: str) -> type[MypyPlugin]:
    global created
    if created:
        # Inside dmypy, don't create a new plugin
        return MypyPlugin

    created = True
    major, minor, _ = version.split(".", 2)

    class Plugin(ExtendedMypyStubs):
        """
        Mypy will complain if the plugin isn't a type, but I want to return an instance of my plugin
        rather than the class itself, so I can pass in mypy_version_tuple.

        So I abuse the `__new__` method to do so.
        """

        def __new__(self, options: Options) -> "Plugin":
            instance = ExtendedMypyStubs(options, mypy_version_tuple=(int(major), int(minor)))
            return cast(Plugin, instance)

    return Plugin
