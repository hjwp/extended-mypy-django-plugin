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

Each dependency is represented by it's priority, import path and line number where
the line number may be ``-1`` if no specific line is relevant.

This plugin needs to be able to re-analyze specific files when otherwise unrelated
files are changed, including changes to the value of the Django ``INSTALLED_APPS``
settings.

To make that easy, the plugin will create a report that is written to where
the plugin is installed where specific line numbers map to specific modules. This
is why the plugin adds a ``project_identifier`` setting, so that the name of this
report is consistent between normal and daemon runs of ``mypy`` and don't conflict
with any other projects in the same python environment.

This specifics of what's in this report is still under construction, but the
important part is that the plugin uses the fact that specific lines in a specific
file may trigger re-analyzing a file.
