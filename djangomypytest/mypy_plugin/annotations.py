from __future__ import annotations

from collections.abc import Sequence
from typing import Generic, TypeVar

from django.db import models

T_Parent = TypeVar("T_Parent", bound=models.Model)


class Concrete(Generic[T_Parent]):
    @classmethod
    def find_children(cls, parent: type[models.Model]) -> Sequence[type[models.Model]]:
        found: list[type[models.Model]] = []

        from django.contrib.contenttypes.models import ContentType

        content_types = ContentType.objects.filter(app_label=parent._meta.app_label)
        for ct in content_types:
            model = ct.model_class()
            if model is None:
                continue
            if not issubclass(model, parent):
                continue
            if hasattr(model, "Meta") and getattr(model.Meta, "is_abstract"):
                continue
            found.append(model)

        return found

    @classmethod
    def type_var(cls, name: str, parent: type[models.Model]) -> TypeVar:
        return TypeVar(name, *cls.find_children(parent))
