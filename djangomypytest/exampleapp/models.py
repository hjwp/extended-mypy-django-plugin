from django.db import models


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


class Child1(Parent):
    two = models.CharField(max_length=60)


class Child2(Parent):
    two = models.CharField(max_length=60)

    three = models.CharField(max_length=70)
