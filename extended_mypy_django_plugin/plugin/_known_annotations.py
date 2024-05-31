import enum


class KnownClasses(enum.Enum):
    CONCRETE = "extended_mypy_django_plugin.annotations.Concrete"


class KnownAnnotations(enum.Enum):
    CONCRETE = "extended_mypy_django_plugin.annotations.Concrete"
    CONCRETE_QUERYSET = "extended_mypy_django_plugin.annotations.ConcreteQuerySet"
    DEFAULT_QUERYSET = "extended_mypy_django_plugin.annotations.DefaultQuerySet"
