#!/usr/bin/env python

import argparse
import os
import pathlib
import sys


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--django-settings-module", help="The setting value to use for DJANGO_SETTINGS_MODULE"
    )
    parser.add_argument(
        "--apps-file",
        help="The file to print the installed apps to, one app per line",
        type=pathlib.Path,
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = make_parser()
    args = parser.parse_args(argv)

    os.environ["DJANGO_SETTINGS_MODULE"] = args.django_settings_module

    # add current directory to sys.path
    sys.path.append(str(pathlib.Path.cwd()))

    from django.apps import apps
    from django.conf import settings

    if not settings.configured:
        settings._setup()  # type: ignore[misc]
    apps.populate(settings.INSTALLED_APPS)

    assert apps.apps_ready, "Apps are not ready"
    assert settings.configured, "Settings are not configured"

    args.apps_file.write_text("\n".join(settings.INSTALLED_APPS))


if __name__ == "__main__":
    main()
