from typing import TYPE_CHECKING

from django.db import models

from extended_mypy_django_plugin import Concrete


class Leader(models.Model):
    class Meta:
        abstract = True


if TYPE_CHECKING:
    ms: Concrete[Leader]
    reveal_type(ms)  # noqa: F821
