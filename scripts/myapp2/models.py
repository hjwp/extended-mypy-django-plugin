from django.db import models
from myapp.models import Parent


class ChildOther(Parent):
    two = models.CharField(max_length=60)
