.. _changelog:

Changelog
---------

.. _release-0.5.1:

0.5.1 - 21 May 2024
    * Providing a return code of 2 from the installed_apps script will make dmypy not
      change version to cause a restart.
    * Changed the ``get_installed_apps`` setting to be ``determine_django_state``
    * Changed the name in pyproject.toml to use dashes instead of underscores

.. _release-0.5.0:

0.5.0 - 19 May 2024
    * ``Concrete``, ``ConcreteQuerySet``, ``DefaultQuerySet`` and ``Concrete.type_var``
    * Better support for running the plugin in the ``mypy`` daemon.
