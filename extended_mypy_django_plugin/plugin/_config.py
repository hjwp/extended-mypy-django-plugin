import configparser
import pathlib
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, NoReturn

from mypy_django_plugin.config import (
    COULD_NOT_LOAD_FILE,
    MISSING_DJANGO_SETTINGS,
    MISSING_SECTION,
    DjangoPluginConfig,
    exit_with_error,
)

if sys.version_info[:2] >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

MISSING_PROJECT_IDENTIFIER = "missing required 'project_identifier' config"
INVALID_BOOL_SETTING = "invalid {key!r}: the setting must be a boolean"


class Config(DjangoPluginConfig):
    __slots__ = ("django_settings_module", "strict_settings", "project_identifier")
    project_identifier: str

    def parse_toml_file(self, filepath: pathlib.Path) -> None:
        toml_exit: Callable[[str], NoReturn] = partial(exit_with_error, is_toml=True)
        try:
            with filepath.open(mode="rb") as f:
                data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError):
            toml_exit(COULD_NOT_LOAD_FILE)

        try:
            config: dict[str, Any] = data["tool"]["django-stubs"]
        except KeyError:
            toml_exit(MISSING_SECTION.format(section="tool.django-stubs"))

        if "django_settings_module" not in config:
            toml_exit(MISSING_DJANGO_SETTINGS)

        if "project_identifier" not in config:
            toml_exit(MISSING_PROJECT_IDENTIFIER)

        self.django_settings_module = config["django_settings_module"]
        if not isinstance(self.django_settings_module, str):
            toml_exit("invalid 'django_settings_module': the setting must be a string")

        self.project_identifier = config["django_settings_module"]
        if not isinstance(self.project_identifier, str):
            toml_exit("invalid 'project_identifier': the setting must be a string")

            if not self.project_identifier.isidentifier():
                toml_exit(
                    "invalid 'project_identifier': the setting must be a valid python identifier"
                )

        self.strict_settings = config.get("strict_settings", True)
        if not isinstance(self.strict_settings, bool):
            toml_exit(INVALID_BOOL_SETTING.format(key="strict_settings"))

    def parse_ini_file(self, filepath: Path) -> None:
        parser = configparser.ConfigParser()
        try:
            with filepath.open(encoding="utf-8") as f:
                parser.read_file(f, source=str(filepath))
        except OSError:
            exit_with_error(COULD_NOT_LOAD_FILE)

        section = "mypy.plugins.django-stubs"
        if not parser.has_section(section):
            exit_with_error(MISSING_SECTION.format(section=section))

        if not parser.has_option(section, "django_settings_module"):
            exit_with_error(MISSING_DJANGO_SETTINGS)

        if not parser.has_option(section, "project_identifier"):
            exit_with_error(MISSING_PROJECT_IDENTIFIER)

        self.django_settings_module = parser.get(section, "django_settings_module").strip("'\"")
        self.project_identifier = parser.get(section, "project_identifier").strip("'\"")

        try:
            self.strict_settings = parser.getboolean(section, "strict_settings", fallback=True)
        except ValueError:
            exit_with_error(INVALID_BOOL_SETTING.format(key="strict_settings"))

    def to_json(self) -> dict[str, Any]:
        """We use this method to reset mypy cache via `report_config_data` hook."""
        return {
            "django_settings_module": self.django_settings_module,
            "project_identifier": self.project_identifier,
            "strict_settings": self.strict_settings,
        }
