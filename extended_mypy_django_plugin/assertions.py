from typing import Generic, TypeVar

from django.db import models

T_Parent = TypeVar("T_Parent", bound=models.Model)


class ConcreteAssertions(Generic[T_Parent]):
    @classmethod
    def assert_is_concrete(cls, obj: T_Parent | type[T_Parent]) -> None:
        """
        The return type is modified by the mypy plugin at the point it is called

        Usage must be inside a method for an abstract django model::

            from typing import Self

            from extended_mypy_django_plugin import Concrete, ConcreteAssertions

            class MyModel(Model):
                class Meta:
                    abstract = True

                @classmethod
                def new(cls) -> Concrete[Self]:
                    ConcreteAssertions[MyModel].assert_is_concrete(cls)
                    ...

                def get_self(self) -> Concrete[Self]:
                    ConcreteAssertions[MyModel].assert_is_concrete(self)
                    ...
        """
        if isinstance(obj, type):
            if (Meta := getattr(obj, "Meta", None)) and getattr(Meta, "abstract", False):
                raise RuntimeError("Expected a concrete subclass")

        elif obj._meta.abstract:
            raise RuntimeError("Expected a concrete instance")
