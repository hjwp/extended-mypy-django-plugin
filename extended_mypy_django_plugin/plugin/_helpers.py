from collections.abc import Callable

from mypy.nodes import TypeInfo
from mypy_django_plugin.lib import helpers

MODEL_METACLASS_FULLNAME = "django.db.models.base.ModelBase"


def is_model_type(info: TypeInfo) -> bool:
    return info.metaclass_type is not None and info.metaclass_type.type.has_base(
        MODEL_METACLASS_FULLNAME
    )


def get_is_abstract_model() -> Callable[[TypeInfo], bool] | None:
    return getattr(helpers, "is_abstract_model", None)


def is_abstract_model(model: TypeInfo) -> bool:
    if (is_abstract_model := get_is_abstract_model()) is None:
        raise NotImplementedError("Only available in newer django-stubs")
    assert callable(is_abstract_model)
    return is_abstract_model(model)
