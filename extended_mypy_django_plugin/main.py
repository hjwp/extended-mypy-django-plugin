from typing import cast

from mypy.options import Options

from .plugin import ExtendedMypyStubs


def plugin(version: str) -> type[ExtendedMypyStubs]:
    major, minor, _ = version.split(".", 2)

    class Plugin(ExtendedMypyStubs):
        def __new__(self, options: Options) -> "Plugin":
            instance = ExtendedMypyStubs(options, mypy_version_tuple=(int(major), int(minor)))
            return cast(Plugin, instance)

    return Plugin
