import functools
from collections.abc import Callable

from mypy.nodes import FuncDef
from mypy.plugin import (
    AnalyzeTypeContext,
    AttributeContext,
    ClassDefContext,
    DynamicClassDefContext,
    FunctionContext,
)
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.transformers.managers import resolve_manager_method

from . import impl

MYPY_VERSION_TUPLE: tuple[int, int]


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    def __init__(self, options: main.Options) -> None:
        super().__init__(options)
        self.metadata = impl.Metadata(
            self.lookup_fully_qualified,
            django_context=self.django_context,
            mypy_version=MYPY_VERSION_TUPLE,
        )

    def get_type_analyze_hook(
        self, fullname: str
    ) -> Callable[[AnalyzeTypeContext], MypyType] | None:
        if fullname == "extended_mypy_django_plugin.annotations.Concrete":
            return self.metadata.find_concrete_models
        elif fullname == "extended_mypy_django_plugin.annotations.ConcreteQuerySet":
            return self.metadata.find_concrete_querysets
        elif fullname == "extended_mypy_django_plugin.annotations.DefaultQuerySet":
            return self.metadata.find_default_queryset
        else:
            return super().get_type_analyze_hook(fullname)

    def get_function_hook(self, fullname: str) -> Callable[[FunctionContext], MypyType] | None:
        sym = self.lookup_fully_qualified(fullname)
        if (
            sym
            and isinstance(sym.node, FuncDef)
            and self.metadata.registered_for_function_hook(sym.node)
        ):
            return functools.partial(
                self.metadata.modify_default_queryset_return_type,
                super_hook=super().get_function_hook(fullname),
            )
        else:
            return super().get_function_hook(fullname)

    def get_customize_class_mro_hook(
        self, fullname: str
    ) -> Callable[[ClassDefContext], None] | None:
        self.metadata.fill_out_concrete_children(fullname)
        return super().get_customize_class_mro_hook(fullname)

    def get_dynamic_class_hook(
        self, fullname: str
    ) -> Callable[[DynamicClassDefContext], None] | None:
        class_name, _, method_name = fullname.rpartition(".")
        if method_name == "type_var":
            info = self._get_typeinfo_or_none(class_name)
            if info and info.has_base("extended_mypy_django_plugin.annotations.Concrete"):
                return self.metadata.transform_type_var_classmethod
        return super().get_dynamic_class_hook(fullname)

    def get_attribute_hook(self, fullname: str) -> Callable[[AttributeContext], MypyType] | None:
        super_hook = super().get_attribute_hook(fullname)
        if super_hook is resolve_manager_method:
            return self.metadata.extended_get_attribute_resolve_manager_method
        else:
            return super_hook


def plugin(version: str) -> type[ExtendedMypyStubs]:
    global MYPY_VERSION_TUPLE
    major, minor, _ = version.split(".", 2)
    MYPY_VERSION_TUPLE = (int(major), int(minor))
    return ExtendedMypyStubs
