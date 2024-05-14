import importlib
import pathlib
import shlex
import stat
import subprocess
import sys
import tempfile
import zlib


class Report:
    def __init__(
        self, installed_apps_script: pathlib.Path | None, django_settings_module: str
    ) -> None:
        self._django_settings_module = django_settings_module
        self._installed_apps_script = installed_apps_script

        if installed_apps_script is not None:
            if not installed_apps_script.exists():
                raise ValueError("The provided script for finding installed apps does not exist")

            if not installed_apps_script.stat().st_mode & stat.S_IXUSR:
                raise ValueError(
                    "The provided script for finding installed apps is not executable!"
                )

    def determine_version_hash(self) -> str:
        with tempfile.NamedTemporaryFile() as result_file:
            if self._installed_apps_script is not None:
                script = self._installed_apps_script
            else:
                script = pathlib.Path(
                    str(
                        importlib.resources.files("extended_mypy_django_plugin")
                        / "scripts"
                        / "get_installed_apps.py"
                    )
                )

            cmd: list[str] = []

            if script.suffix == ".py":
                cmd.append(sys.executable)
            else:
                with open(script) as fle:
                    line = fle.readline()
                    if line.startswith("#!"):
                        cmd.extend(shlex.split(line[2:]))

            cmd.extend(
                [
                    str(script),
                    "--django-settings-module",
                    self._django_settings_module,
                    "--apps-file",
                    result_file.name,
                ]
            )

            subprocess.run(cmd, capture_output=True, check=True)
            return str(zlib.adler32(pathlib.Path(result_file.name).read_bytes()))
