import pytest
from pytest_mypy_plugins import MypyPluginsConfig, MypyPluginsScenario

from extended_mypy_django_plugin.plugin._debug import debug

__builtins__["debug"] = debug

from .scenario import Scenario


@pytest.fixture
def scenario(
    mypy_plugins_config: MypyPluginsConfig, mypy_plugins_scenario: MypyPluginsScenario
) -> Scenario:
    """
    Polish the sharp edges of the pytest mypy plugin
    """
    mypy_plugins_scenario.additional_mypy_config = (
        "\n[mypy.plugins.django-stubs]\n" "django_settings_module = mysettings"
    )
    return Scenario(mypy_plugins_config, mypy_plugins_scenario)
