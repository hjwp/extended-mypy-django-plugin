Tracking changes to models
==========================

The difficulty with making a ``mypy`` plugin is making sure the plugin still
produces correct and useful results after the code has changed. This is made
especially difficult for the ``mypy`` plugin, which depends on using Django itself
to understand the relationship between the different models in the project.

The API for a ``mypy`` plugin exposes a hook called ``get_additional_deps``
that is called when a whole file needs to be analyzed. This hook takes is called
after the file has been parsed, but before it's been analyzed. The hook must
return a list of other files this file depends on.

This plugin needs to be able to re-analyze specific files when otherwise unrelated
files are changed, including changes to the value of the Django ``INSTALLED_APPS``
settings.

To make that easy, the plugin will create reports that are written to a folder
that is specified by the ``scratch_path`` setting and use those paths to ensure that
there is a dependency that is changed when new dependencies are discovered.

There is also a script that can be provided to determine information about the project
used to work out if dmypy should restart itself. This is a necessary action in cases
where Django needs to be restarted. Doing that restart requires a fresh process that
hasn't been "poisoned" by having an already imported Django environment.

The plugin comes with a script that already does the correct thing for usual Django
projects.

If there is a file in the scratch path named ``__assume_django_state_unchanged__`` in
the ``scratch_path`` then dmypy will not restart itself regardless of whether there are
changes that require this to happen. This is for projects where starting Django is a
costly process and a developer may want to have more fidelity over when that startup cost
is incurred.
