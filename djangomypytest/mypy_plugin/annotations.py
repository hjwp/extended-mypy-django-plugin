from typing import Generic, TypeVar

from django.db import models

T_Parent = TypeVar("T_Parent", bound=models.Model)


class Concrete(Generic[T_Parent]): ...
