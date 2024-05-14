"""
The config class is copied from ``django-stubs`` except it also looks for a
``python_identifier`` option and ensures it's a valid python identifier.
"""

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

MISSING_SCRATCH_PATH = "missing required 'scratch_path' config"
INVALID_BOOL_SETTING = "invalid {key!r}: the setting must be a boolean"


class Config(DjangoPluginConfig):
    __slots__ = (
        "django_settings_module",
        "strict_settings",
        "scratch_path",
        "installed_apps_script",
    )
    scratch_path: Path
    installed_apps_script: Path | None

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

        if "scratch_path" not in config:
            toml_exit(MISSING_SCRATCH_PATH)

        self.django_settings_module = config["django_settings_module"]
        if not isinstance(self.django_settings_module, str):
            toml_exit("invalid 'django_settings_module': the setting must be a string")

        if not isinstance(config["scratch_path"], str):
            toml_exit("invalid 'scratch_path': the setting must be a string")
        self.scratch_path = filepath.parent / config["scratch_path"]
        self.scratch_path.mkdir(parents=True, exist_ok=True)

        if "installed_apps_path" in config:
            if not isinstance(config["installed_apps_script"], str):
                toml_exit("invalid 'installed_apps_script': the setting must be a string")
            self.installed_apps_script = filepath.parent / config["installed_apps_script"]
        else:
            self.installed_apps_script = None

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

        if not parser.has_option(section, "scratch_path"):
            exit_with_error(MISSING_SCRATCH_PATH)

        self.django_settings_module = parser.get(section, "django_settings_module").strip("'\"")

        self.scratch_path = filepath.parent / parser.get(section, "scratch_path").strip("'\"")
        self.scratch_path.mkdir(parents=True, exist_ok=True)

        if parser.has_option(section, "installed_apps_script"):
            self.installed_apps_script = filepath.parent / parser.get(
                section, "installed_apps_script"
            ).strip("'\"")
        else:
            self.installed_apps_script = None

        try:
            self.strict_settings = parser.getboolean(section, "strict_settings", fallback=True)
        except ValueError:
            exit_with_error(INVALID_BOOL_SETTING.format(key="strict_settings"))

    def to_json(self) -> dict[str, Any]:
        """We use this method to reset mypy cache via `report_config_data` hook."""
        return {
            "django_settings_module": self.django_settings_module,
            "scratch_path": str(self.scratch_path),
            "installed_apps_script": (
                None if self.installed_apps_script is None else str(self.installed_apps_script)
            ),
            "strict_settings": self.strict_settings,
        }
