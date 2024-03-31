Installation
============

.. note:: This plugin hasn't been published on pypi at this point

Enabling this plugin in a project is adding either to ``mypy.ini``::

    [mypy]
    plugins =
        extended_mypy_django_plugin.main

    [mypy.plugins.django-stubs]
    project_identifier = some_valid_python_identifier
    django_settings_module = some_valid_import_path_to_django_settings

Or to ``pyproject.toml``::

    [tool.mypy]
    plugins = ["extended_mypy_django_plugin.main"]

    [tool.django-stubs]
    project_identifier = "some_valid_python_identifier"
    django_settings_module = "some_valid_import_path_to_django_settings"

.. note:: This project adds a mandatory setting ``project_identifier`` that
   needs to be a valid python identifier, and unique to your project within
   the python environment this plugin is installed in.
