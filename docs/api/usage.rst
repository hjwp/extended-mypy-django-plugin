Usage
=====

To make use of this plugin in code means using the annotation classes that are
provided.

The following examples assume there is an abstract model ``AbstractModel``
with the concrete models ``Concrete1``, ``Concrete2`` and ``Concrete3`` and
``Concrete2`` has a custom queryset class called ``Concrete2QS``.

Concrete
--------

To create a union of the concrete models, use the ``Concrete`` annotation:

.. code-block:: python

    from extended_mypy_django_plugin import Concrete


    instance: Concrete[AbstractModel]
    
    # --------------
    # Equivalent to
    # --------------

    instance: Concrete1 | Concrete2 | Concrete3

This also works for types:

.. code-block:: python

    from extended_mypy_django_plugin import Concrete


    cls: Concrete[type[AbstractModel]]

    # --------------
    # Equivalent to
    # --------------

    cls: type[Concrete1 | Concrete2 | Concrete3]


Concrete.type_var
-----------------

To create a type var representing any one of the concrete models of an abstract
model, create a ``TypeVar`` object using ``Concrete.type_var``:

.. code-block:: python

    from extended_mypy_django_plugin import Concrete


    T_Concrete = Concrete.type_var("T_Concrete", AbstractModel)


    def create_row(cls: type[T_Concrete]) -> T_Concrete:
        return cls.objects.create()

    # --------------
    # Equivalent to
    # --------------

    from typing import TypeVar

    T_Concrete = TypeVar("T_Concrete", Concrete1, Concrete2, Concrete3)


    def create_row(cls: type[T_Concrete]) -> T_Concrete:
        return cls.objects.create()


ConcreteQuerySet
----------------

To create a union of the default querysets for the concrete models of an
abstract class, use the ``ConcreteQuerySet`` annotation:

.. code-block:: python

    from extended_mypy_django_plugin import ConcreteQuerySet
    from django.db import models


    qs: ConcreteQuerySet[AbstractModel]

    # --------------
    # Equivalent to
    # --------------

    qs: models.QuerySet[Concrete1] | Concrete2QuerySet | models.QuerySet[Concrete3]

DefaultQuerySet
---------------

This is similar to ``ConcreteQuerySet`` but works on the concrete models themselves:

.. code-block:: python

    from extended_mypy_django_plugin import DefaultQuerySet


    qs1: DefaultQuerySet[Concrete1]
    qs2: DefaultQuerySet[Concrete2]

    # --------------
    # Equivalent to
    # --------------

    from django.db import models

    qs1: models.QuerySet[Concrete1]
    qs2: Concrete2QuerySet

It also works on the ``TypeVar`` objects returned by ``Concrete.type_var``:

.. code-block:: python

    from extended_mypy_django_plugin import Concrete, DefaultQuerySet


    T_Concrete = Concrete.type_var("T_Concrete", AbstractModel)


    def get_qs(cls: type[T_Concrete]) -> DefaultQuerySet[T_Concrete]:
        return cls.objects.all()

    # --------------
    # Essentially equivalent to
    # --------------

    from typing import TypeVar, overload

    T_Concrete = TypeVar("T_Concrete", Concrete1, Concrete2, Concrete3)


    @overload
    def create_row(cls: Concrete1) -> models.QuerySet[Concrete1]: ...


    @overload
    def create_row(cls: Concrete2) -> Concrete2QuerySet: ...


    @overload
    def create_row(cls: Concrete3) -> models.QuerySet[Concrete3]: ...


    def create_row(
        cls: type[Concrete1 | Concrete2 | Concrete3],
    ) -> models.QuerySet[Concrete1] | Concrete2QuerySet | models.QuerySet[Concrete3]:
        return cls.objects.create()
