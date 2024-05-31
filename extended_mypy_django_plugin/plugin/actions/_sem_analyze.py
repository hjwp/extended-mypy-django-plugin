from mypy.nodes import (
    GDEF,
    StrExpr,
    SymbolTableNode,
    TypeInfo,
    TypeVarExpr,
)
from mypy.plugin import AnalyzeTypeContext, DynamicClassDefContext
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    TypeOfAny,
    TypeVarType,
)
from mypy.types import (
    Type as MypyType,
)

from .. import _known_annotations, _store
from . import _annotation_resolver


class TypeAnalyzer:
    def __init__(self, store: _store.Store, api: TypeAnalyser, sem_api: SemanticAnalyzer) -> None:
        self.api = api
        self.store = store
        self.sem_api = sem_api

    def _lookup_info(self, fullname: str) -> TypeInfo | None:
        instance = self.sem_api.named_type_or_none(fullname)
        if instance:
            return instance.type

        return self.store.plugin_lookup_info(fullname)

    def analyze(
        self, ctx: AnalyzeTypeContext, annotation: _known_annotations.KnownAnnotations
    ) -> MypyType:
        def defer() -> bool:
            if self.sem_api.final_iteration:
                return True
            else:
                self.sem_api.defer()
                return False

        resolver = _annotation_resolver.AnnotationResolver(
            self.store,
            defer=defer,
            fail=lambda msg: self.api.fail(msg, ctx.context),
            lookup_info=self._lookup_info,
            named_type_or_none=self.sem_api.named_type_or_none,
        )

        type_arg = resolver.find_type_arg(ctx.type, self.api.analyze_type)
        if type_arg is None:
            return ctx.type

        result = resolver.resolve(annotation, type_arg)
        if result is None:
            return ctx.type
        else:
            return result


class SemAnalyzing:
    def __init__(self, store: _store.Store, *, api: SemanticAnalyzer) -> None:
        self.api = api
        self.store = store

    def _lookup_info(self, fullname: str) -> TypeInfo | None:
        instance = self.api.named_type_or_none(fullname)
        if instance:
            return instance.type

        return self.store.plugin_lookup_info(fullname)

    def transform_type_var_classmethod(self, ctx: DynamicClassDefContext) -> None:
        if not isinstance(ctx.call.args[0], StrExpr):
            self.api.fail(
                "First argument to Concrete.type_var must be a string of the name of the variable",
                ctx.call,
            )
            return

        name = ctx.call.args[0].value
        if name != ctx.name:
            self.api.fail(
                f"First argument {name} was not the name of the variable {ctx.name}",
                ctx.call,
            )
            return

        module = self.api.modules[self.api.cur_mod_id]
        if isinstance(module.names.get(name), TypeVarType):
            return

        parent: SymbolTableNode | None = None
        try:
            parent = self.api.lookup_type_node(ctx.call.args[1])
        except AssertionError:
            parent = None

        if parent is None:
            self.api.fail(
                "Second argument to Concrete.type_var must be the abstract model class to find concrete instances of",
                ctx.call,
            )
            return

        if not isinstance(parent.node, TypeInfo):
            self.api.fail(
                "Second argument to Concrete.type_var was not pointing at a class", ctx.call
            )
            return

        object_type = self.api.named_type("builtins.object")
        values = self.store.retrieve_concrete_children_types(
            parent.node, self._lookup_info, self.api.named_type_or_none
        )
        if not values:
            self.api.fail(f"No concrete children found for {parent.node.fullname}", ctx.call)

        type_var_expr = TypeVarExpr(
            name=name,
            fullname=f"{self.api.cur_mod_id}.{name}",
            values=list(values),
            upper_bound=object_type,
            default=AnyType(TypeOfAny.from_omitted_generics),
        )

        module.names[name] = SymbolTableNode(GDEF, type_var_expr, plugin_generated=True)
        return None
