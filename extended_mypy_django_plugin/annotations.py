from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar

from django.db import models

T = TypeVar("T")
T_Parent = TypeVar("T_Parent")


class Concrete(Generic[T_Parent]):
    """
    The ``Concrete`` annotation exists as a class with functionality for both
    runtime and static type checking time.

    At runtime it can be used to create special ``TypeVar`` objects that may
    represent any one of the concrete children of some abstract class and
    it can be used to find those concrete children.

    At static type checking time (specifically with ``mypy``) it is used to create
    a type that represents the Union of all the concrete children of some
    abstract model.

    .. automethod:: find_children
    .. automethod:: type_var
    .. automethod:: assert_is_concrete
    """

    @staticmethod
    def assert_is_concrete(obj: object) -> None:
        """
        Assert that an object is not abstract.

        Note a cast can't be done automatically::

            from typing import Self, cast

            from extended_mypy_django_plugin import Concrete

            class MyModel(Model):
                class Meta:
                    abstract = True

                @classmethod
                def new(cls) -> Concrete[Self]:
                    Concrete.assert_not_abstract(cls)
                    cls = cast(Concrete[Self], cls)
                    ...

                def get_self(self) -> Concrete[Self]:
                    ...
        """
        if isinstance(obj, type):
            if not issubclass(obj, models.Model) or (
                (Meta := getattr(obj, "Meta", None)) and getattr(Meta, "abstract", False)
            ):
                raise RuntimeError("Expected a concrete subclass")

        elif not isinstance(obj, models.Model) or obj._meta.abstract:
            raise RuntimeError("Expected a concrete instance")

    @classmethod
    def find_children(cls, parent: type[models.Model]) -> Sequence[type[models.Model]]:
        """
        At runtime this will find all the concrete children of some model.

        That is all models that inherit from this model and aren't abstract
        themselves.
        """
        found: list[type[models.Model]] = []

        from django.contrib.contenttypes.models import ContentType

        content_types = ContentType.objects.filter(app_label=parent._meta.app_label)
        for ct in content_types:
            model = ct.model_class()
            if model is None:
                continue
            if not issubclass(model, parent):
                continue
            if hasattr(model, "Meta") and getattr(model.Meta, "is_abstract", False):
                continue
            found.append(model)

        return found

    @classmethod
    def type_var(cls, name: str, parent: type[models.Model]) -> TypeVar:
        """
        This returns an empty ``TypeVar`` at runtime, but the ``mypy`` plugin will
        recognise that this ``TypeVar`` represents a choice of all the concrete
        children of the specified model.
        """
        return TypeVar(name)


class ConcreteQuerySet(Generic[T_Parent]):
    """
    This is used to annotate a model such that the mypy plugin may turn this into
    a union of all the default querysets for all the concrete children of the
    specified abstract model class.
    """


class DefaultQuerySet(Generic[T_Parent]):
    """
    This is used to annotate a model such that the mypy plugin may turn this into
    the queryset type used by the default manager on the model.
    """
