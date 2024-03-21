from typing import TYPE_CHECKING, cast

from django.db import models

from djangomypytest.mypy_plugin.annotations import Concrete


class Question(models.Model):
    question_text = models.CharField(max_length=200)
    pub_date = models.DateTimeField("date published")


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    choice_text = models.CharField(max_length=200)
    votes = models.IntegerField(default=0)


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


class Child2(Parent):
    two = models.CharField(max_length=60)
    four = models.CharField(max_length=1)

    three = models.CharField(max_length=70)


class Parent2(Parent):
    three = models.CharField(max_length=50)

    class Meta:
        abstract = True


class Child3(Parent2):
    two = models.CharField(max_length=60)

    three = models.CharField(max_length=70)
