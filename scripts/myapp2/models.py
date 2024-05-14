from django.db import models
from myapp.models import Parent


class ChildOther(Parent):
    concrete_from_myapp2 = models.CharField(max_length=50)
    two = models.CharField(max_length=60)
