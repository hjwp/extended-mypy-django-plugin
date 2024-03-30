import os
import pathlib

from pytest_mypy_plugins.collect import File
from pytest_mypy_plugins.item import YamlTestItem

here = pathlib.Path(__file__).parent


def django_plugin_hook(test_item: YamlTestItem) -> None:
    custom_settings = test_item.parsed_test_data.get("custom_settings", "")
    installed_apps = test_item.parsed_test_data.get("installed_apps", ["myapp", "myapp2"])
    monkeypatch = test_item.parsed_test_data.get("monkeypatch", False)

    if installed_apps and custom_settings:
        raise ValueError(
            '"installed_apps" and "custom_settings" are not compatible, please use one or the other'
        )

    if installed_apps is not None:
        # custom_settings is empty, add INSTALLED_APPS
        if "django.contrib.contenttypes" not in installed_apps:
            installed_apps += ["django.contrib.contenttypes"]
        installed_apps_as_str = "(" + ",".join([repr(app) for app in installed_apps]) + ",)"
        custom_settings += "INSTALLED_APPS = " + installed_apps_as_str

    if "SECRET_KEY" not in custom_settings:
        custom_settings = 'SECRET_KEY = "1"\n' + custom_settings

    if monkeypatch:
        custom_settings = (
            "import django_stubs_ext\ndjango_stubs_ext.monkeypatch()\n" + custom_settings
        )

    django_settings_section = (
        "\n[mypy.plugins.django-stubs]\n" "django_settings_module = mysettings"
    )
    if not test_item.additional_mypy_config:
        test_item.additional_mypy_config = django_settings_section
    else:
        if "[mypy.plugins.django-stubs]" not in test_item.additional_mypy_config:
            test_item.additional_mypy_config += django_settings_section

    mysettings_file = File(path="mysettings.py", content=custom_settings)

    test_item.files.append(mysettings_file)

    if "myapp" in installed_apps:
        for root, _, files in os.walk(here / "myapp"):
            for name in files:
                location = pathlib.Path(root, name)
                test_item.files.append(
                    File(path=str(location.relative_to(here)), content=location.read_text())
                )
    if "myapp2" in installed_apps:
        for root, _, files in os.walk(here / "myapp2"):
            for name in files:
                location = pathlib.Path(root, name)
                test_item.files.append(
                    File(path=str(location.relative_to(here)), content=location.read_text())
                )
