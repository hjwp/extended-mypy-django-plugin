from mypy.nodes import (
    GDEF,
    CastExpr,
    StrExpr,
    SymbolTableNode,
    TypeInfo,
    TypeVarExpr,
    Var,
)
from mypy.plugin import DynamicClassDefContext
from mypy.semanal import SemanticAnalyzer
from mypy.types import AnyType, TypeOfAny, TypeType, TypeVarType, UnionType, get_proper_type

from .. import _known_annotations, _store


class SemAnalyzing:
    def __init__(self, store: _store.Store, *, api: SemanticAnalyzer) -> None:
        self.api = api
        self.store = store

    def transform_assert_is_concrete(self, ctx: DynamicClassDefContext) -> None:
        if not self.api.is_func_scope():
            return

        if len(ctx.call.args) != 1:
            return

        arg_name = self.api.lookup_type_node(ctx.call.args[0]).node.name
        arg_node = self.api.lookup_current_scope(arg_name)

        if arg_node is None or arg_node.type is None or arg_node.node is None:
            return None

        if not isinstance(arg_node.node, Var):
            return None

        arg_node_typ = get_proper_type(arg_node.type)

        concrete = self.api.lookup_fully_qualified(
            _known_annotations.KnownAnnotations.CONCRETE.value
        )
        assert isinstance(concrete.node, TypeInfo)

        is_self_type: bool = bool(
            self.api.is_func_scope()
            and self.api.type
            and isinstance(arg_node_typ, TypeType)
            and isinstance(arg_node_typ.item, TypeVarType)
            and arg_node_typ.item.name == "Self"
        )

        if not is_self_type:
            self.api.fail(
                "Should only use Concrete.assert_is_concrete in django abstract model class methods"
            )
            return None

        concrete = tuple(
            self.store.retrieve_concrete_children_types(
                self.api.type, self.lookup_info, self.api.named_type_or_none
            )
        )

        into = TypeType(UnionType(concrete))

        self.api.scope.function.type.arg_types[0] = TypeType(
            self.api.named_type(self.api.type.fullname)
        )

        ctx.call.analyzed = CastExpr(ctx.call.args[0], into)
        ctx.call.analyzed.line = ctx.call.line
        ctx.call.analyzed.column = ctx.call.column
        ctx.call.analyzed.accept(self.api)

        return None

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
            parent.node, self.lookup_info, self.api.named_type_or_none
        )
        if not values:
            if not self.api.final_iteration:
                self.api.defer()
                return None
            self.api.fail(f"No concrete children found for {parent.node.fullname}", ctx.call)
            return None

        type_var_expr = TypeVarExpr(
            name=name,
            fullname=f"{self.api.cur_mod_id}.{name}",
            values=list(values),
            upper_bound=object_type,
            default=AnyType(TypeOfAny.from_omitted_generics),
        )

        module.names[name] = SymbolTableNode(GDEF, type_var_expr, plugin_generated=True)
        return None

    def lookup_info(self, fullname: str) -> TypeInfo | None:
        instance = self.api.named_type_or_none(fullname)
        if instance:
            return instance.type

        return self.store._plugin_lookup_info(fullname)
