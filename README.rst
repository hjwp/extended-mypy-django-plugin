Django mypy test
================

This is a playground repo for figuring out how to make mypy in django4.2
with django-stubs understand `objects` on abstract model classes.

Development
-----------

This project uses venvstarter.readthedocs.io to manage a virtualenv.

All the commands will only install things locally to this repository.

To run mypy against this plugin::

  > ./types

To Run tests::

  > ./test.sh

To activate the virtualenv in your current shell::

  > source run.sh activate
