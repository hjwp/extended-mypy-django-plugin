from typing import TYPE_CHECKING, cast

from django.db import models

from extended_mypy_django_plugin import Concrete


class Parent(models.Model):
    one = models.CharField(max_length=50)

    class Meta:
        abstract = True


if TYPE_CHECKING:
    _Parent_concrete = cast(Concrete[Parent], None)
    # This next line failing means you need to restart dmypy
    _Parent_concrete.objects


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
