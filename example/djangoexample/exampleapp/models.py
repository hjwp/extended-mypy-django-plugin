from typing import TYPE_CHECKING

from django.db import models
from typing_extensions import Self

from extended_mypy_django_plugin import Concrete


class Parent(models.Model):
    one = models.CharField(max_length=50)

    class Meta:
        abstract = True

    @classmethod
    def new(cls) -> Concrete[Self]:
        return cls  # type: ignore[return-value]


class Child1(Parent):
    two = models.CharField(max_length=60)


class Child2QuerySet(models.QuerySet["Child2"]):
    pass


Child2Manager = models.Manager.from_queryset(Child2QuerySet)


class Child2(Parent):
    two = models.CharField(max_length=60)
    four = models.CharField(max_length=1)

    three = models.CharField(max_length=70)

    objects = Child2Manager()


class Parent2(Parent):
    three = models.CharField(max_length=50)

    class Meta:
        abstract = True


class Child3(Parent2):
    two = models.CharField(max_length=60)

    three = models.CharField(max_length=70)


class Child4QuerySet(models.QuerySet["Child2"]):
    pass


Child4Manager = models.Manager.from_queryset(Child4QuerySet)


class Child4(Parent2):
    two = models.CharField(max_length=60)

    three = models.CharField(max_length=70)

    objects = Child4Manager()


class Child5(Parent):
    two = models.CharField(max_length=60)

    three = models.CharField(max_length=70)


if TYPE_CHECKING:
    reveal_type(Child5.new())
