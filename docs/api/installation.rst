Installation
============

.. note:: This plugin hasn't been published on pypi at this point

Enabling this plugin in a project is adding either to ``mypy.ini``::

    [mypy]
    plugins =
        extended_mypy_django_plugin.main
    mypy_path = $MYPY_CONFIG_FILE_DIR/./path/relative/to/config/where/information/is/cached

    [mypy.plugins.django-stubs]
    scratch_path = ./path/relative/to/config/where/information/is/cached
    django_settings_module = some_valid_import_path_to_django_settings

Or to ``pyproject.toml``::

    [tool.mypy]
    plugins = ["extended_mypy_django_plugin.main"]
    mypy_path = "$MYPY_CONFIG_FILE_DIR/./path/relative/to/config/where/information/is/cached"

    [tool.django-stubs]
    scratch_path = "./path/relative/to/config/where/information/is/cached"
    django_settings_module = "some_valid_import_path_to_django_settings"

.. note:: This project adds a mandatory setting ``scratch_path`` that
   will be a path relative to the config file where the mypy plugin will write
   files to for the purpose of understanding when files need to be re-analyzed.
