import enum
from typing import Generic

from mypy.checker import TypeChecker
from mypy.nodes import TypeInfo
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
from mypy.types import CallableType, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)
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
        self.store = _store.Store(
            get_model_class_by_fullname=self.django_context.get_model_class_by_fullname,
            lookup_info=self._lookup_info,
        )

    def _lookup_info(self, fullname: str) -> TypeInfo | None:
        sym = self.lookup_fully_qualified(fullname)
        if sym and isinstance(sym.node, TypeInfo):
            return sym.node
        else:
            return None

    @_hook.hook
    class get_customize_class_mro_hook(Hook[ClassDefContext, None]):
        def choose(self) -> bool:
            sym = self.plugin._lookup_info(self.fullname)
            return self.fullname != _store.MODEL_CLASS_FULLNAME and bool(
                sym and _store.MODEL_CLASS_FULLNAME in [m.fullname for m in sym.mro]
            )

        def run(self, ctx: ClassDefContext) -> None:
            return self.store.associate_model_heirarchy(self.fullname, self.plugin._lookup_info)

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

            sem_analyzing = actions.SemAnalyzing(self.store, api=ctx.api)
            return sem_analyzing.transform_type_var_classmethod(
                ctx, mypy_version_tuple=self.plugin.mypy_version_tuple
            )

    @_hook.hook
    class get_type_analyze_hook(Hook[AnalyzeTypeContext, MypyType]):
        def choose(self) -> bool:
            return any(
                member.value == self.fullname
                for member in ExtendedMypyStubs.Annotations.__members__.values()
            )

        def run(self, ctx: AnalyzeTypeContext) -> MypyType:
            assert isinstance(ctx.api, TypeAnalyser)
            assert isinstance(ctx.api.api, SemanticAnalyzer)

            Known = ExtendedMypyStubs.Annotations
            name = Known(self.fullname)
            type_analyzer = actions.TypeAnalyzing(self.store, api=ctx.api, sem_api=ctx.api.api)

            if name is Known.CONCRETE:
                method = type_analyzer.find_concrete_models

            elif name is Known.CONCRETE_QUERYSET:
                method = type_analyzer.find_concrete_querysets

            elif name is Known.DEFAULT_QUERYSET:
                method = type_analyzer.find_default_queryset
            else:
                assert_never(name)

            return get_proper_type(method(unbound_type=ctx.type))

    @_hook.hook
    class get_function_hook(Hook[FunctionContext, MypyType]):
        def choose(self) -> bool:
            sym = self.plugin.lookup_fully_qualified(self.fullname)
            if not sym or not sym.node:
                return False

            call = getattr(sym.node, "type", None)
            if not isinstance(call, CallableType):
                return False

            return call.is_generic()

        def run(self, ctx: FunctionContext) -> MypyType:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)

            return get_proper_type(
                type_checking.modify_default_queryset_return_type(
                    ctx,
                    super_hook=self.super_hook,
                    desired_annotation_fullname=ExtendedMypyStubs.Annotations.DEFAULT_QUERYSET.value,
                )
            )

    @_hook.hook
    class get_attribute_hook(Hook[AttributeContext, MypyType]):
        def choose(self) -> bool:
            return self.super_hook is resolve_manager_method

        def run(self, ctx: AttributeContext) -> MypyType:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)
            return get_proper_type(
                type_checking.extended_get_attribute_resolve_manager_method(
                    ctx, resolve_manager_method_from_instance=resolve_manager_method_from_instance
                )
            )
