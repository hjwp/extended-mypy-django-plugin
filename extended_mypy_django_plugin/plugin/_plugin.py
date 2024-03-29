import enum
from typing import Generic

from mypy.checker import TypeChecker
from mypy.nodes import CallExpr, FuncDef
from mypy.options import Options
from mypy.plugin import (
    AnalyzeTypeContext,
    AttributeContext,
    ClassDefContext,
    DynamicClassDefContext,
    FunctionContext,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.transformers.managers import resolve_manager_method
from typing_extensions import assert_never

from . import _hook, _store, actions


class Hook(
    Generic[_hook.T_Ctx, _hook.T_Ret],
    _hook.Hook["ExtendedMypyStubs", _hook.T_Ctx, _hook.T_Ret],
):
    store: _store.Store

    def extra_init(self) -> None:
        self.store = self.plugin.store


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    class Annotations(enum.Enum):
        CONCRETE = "extended_mypy_django_plugin.annotations.Concrete"
        CONCRETE_QUERYSET = "extended_mypy_django_plugin.annotations.ConcreteQuerySet"
        DEFAULT_QUERYSET = "extended_mypy_django_plugin.annotations.DefaultQuerySet"

    def __init__(self, options: Options, mypy_version_tuple: tuple[int, int]) -> None:
        super().__init__(options)
        self.mypy_version_tuple = mypy_version_tuple
        self.store = _store.Store(self.lookup_fully_qualified, django_context=self.django_context)

    @_hook.hook
    class get_type_analyze_hook(Hook[AnalyzeTypeContext, MypyType]):
        def choose(self) -> bool:
            return any(
                member.value == self.fullname
                for member in ExtendedMypyStubs.Annotations.__members__.values()
            )

        def run(self, ctx: AnalyzeTypeContext) -> MypyType:
            Known = ExtendedMypyStubs.Annotations
            name = Known(self.fullname)

            assert isinstance(ctx.api, TypeAnalyser)
            assert isinstance(ctx.api.api, SemanticAnalyzer)

            type_analyzer = actions.TypeAnalyzing(self.store)

            if name is Known.CONCRETE:
                method = type_analyzer.find_concrete_models

            elif name is Known.CONCRETE_QUERYSET:
                method = type_analyzer.find_concrete_querysets

            elif name is Known.DEFAULT_QUERYSET:
                method = type_analyzer.find_default_queryset
            else:
                assert_never(name)

            return method(unbound_type=ctx.type, api=ctx.api, sem_api=ctx.api.api)

    @_hook.hook
    class get_function_hook(Hook[FunctionContext, MypyType]):
        def choose(self) -> bool:
            sym = self.plugin.lookup_fully_qualified(self.fullname)
            return bool(
                sym
                and isinstance(sym.node, FuncDef)
                and self.store.registered_for_function_hook(sym.node)
            )

        def run(self, ctx: FunctionContext) -> MypyType:
            assert isinstance(ctx.api, TypeChecker)
            assert isinstance(ctx.context, CallExpr)

            type_checking = actions.TypeChecking(self.store)

            return type_checking.modify_default_queryset_return_type(
                ctx,
                context=ctx.context,
                api=ctx.api,
                super_hook=self.super_hook,
                desired_annotation_fullname=ExtendedMypyStubs.Annotations.DEFAULT_QUERYSET.value,
            )

    @_hook.hook
    class get_customize_class_mro_hook(Hook[ClassDefContext, None]):
        def choose(self) -> bool:
            self.store.fill_out_concrete_children(self.fullname)
            return False

        def run(self, ctx: ClassDefContext) -> None:
            # Never called
            return None

    @_hook.hook
    class get_dynamic_class_hook(Hook[DynamicClassDefContext, None]):
        def choose(self) -> bool:
            class_name, _, method_name = self.fullname.rpartition(".")
            if method_name == "type_var":
                info = self.plugin._get_typeinfo_or_none(class_name)
                if info and info.has_base(ExtendedMypyStubs.Annotations.CONCRETE.value):
                    return True

            return False

        def run(self, ctx: DynamicClassDefContext) -> None:
            assert isinstance(ctx.api, SemanticAnalyzer)

            sem_analyzing = actions.SemAnalyzing(self.store, ctx.api)
            return sem_analyzing.transform_type_var_classmethod(
                ctx, mypy_version_tuple=self.plugin.mypy_version_tuple
            )

    @_hook.hook
    class get_attribute_hook(Hook[AttributeContext, MypyType]):
        def choose(self) -> bool:
            return self.super_hook is resolve_manager_method

        def run(self, ctx: AttributeContext) -> MypyType:
            type_checking = actions.TypeChecking(self.store)
            return type_checking.extended_get_attribute_resolve_manager_method(ctx)
