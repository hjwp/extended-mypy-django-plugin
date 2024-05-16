import pathlib
from collections.abc import Iterator
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django.apps.registry import Apps


def record_known_models(output_location: pathlib.Path, apps_registry: "Apps") -> None:
    found: set[str] = set()
    for model in find_known_models(apps_registry):
        found.add(f"{model.__module__}.{model.__qualname__}")

    output_location.write_text("\n".join(sorted(found)))


def find_known_models(apps_registry: "Apps") -> Iterator[type[models.Model]]:
    for concrete_model_cls in apps_registry.get_models():
        yield concrete_model_cls

        # And find abstract classes
        for model_cls in concrete_model_cls.mro()[1:]:
            if (
                issubclass(model_cls, models.Model)
                and hasattr(model_cls, "_meta")
                and model_cls._meta.abstract
            ):
                yield model_cls
