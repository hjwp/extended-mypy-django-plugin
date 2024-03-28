from collections.abc import Callable

from mypy.checker import TypeChecker
from mypy.nodes import (
    GDEF,
    CallExpr,
    MemberExpr,
    StrExpr,
    SymbolNode,
    SymbolTableNode,
    TypeInfo,
    TypeVarExpr,
)
from mypy.plugin import (
    AttributeContext,
    DynamicClassDefContext,
    FunctionContext,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    CallableType,
    Instance,
    ProperType,
    TypeOfAny,
    TypeType,
    TypeVarType,
    UnboundType,
    UnionType,
    get_proper_type,
)
from mypy.types import Type as MypyType
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)

from . import concrete_children, fullnames, helpers


class Actions:
    def __init__(
        self,
        lookup_fully_qualified: Callable[[str], SymbolTableNode | None],
        django_context: DjangoContext,
        mypy_version: tuple[int, int],
    ) -> None:
        self._mypy_version = mypy_version
        self._django_context = django_context
        self._metadata: dict[str, dict[str, object]] = {}
        self._lookup_fully_qualified = lookup_fully_qualified

        self._registered_for_function_hook: set[str] = set()

    def sync_metadata(self, info: TypeInfo) -> None:
        on_info = info.metadata.get("django_extended")
        in_metadata = self._metadata.get(info.fullname)

        if on_info is None:
            if in_metadata is None:
                # Have neither
                info.metadata["django_extended"] = self._metadata[info.fullname] = {}
            else:
                # Only have in metadata
                info.metadata["django_extended"] = in_metadata
        else:
            if in_metadata is None:
                # only have on info
                self._metadata[info.fullname] = on_info
            else:
                # Have both on_info and in_metadata
                if in_metadata:
                    info.metadata["django_extended"] = in_metadata
                else:
                    in_metadata[info.fullname] = on_info

    def concrete_for(self, info: TypeInfo) -> concrete_children.ConcreteChildren:
        self.sync_metadata(info)
        metadata = self._metadata[info.fullname]
        if "concrete_children" not in metadata:
            metadata["concrete_children"] = []

        children = metadata["concrete_children"]
        assert isinstance(children, list)
        return concrete_children.ConcreteChildren(
            children=children,
            _lookup_fully_qualified=self._lookup_fully_qualified,
            _django_context=self._django_context,
            _fail_function=lambda s: None,
        )

    def register_for_function_hook(self, node: SymbolNode) -> None:
        assert node.fullname is not None
        self._registered_for_function_hook.add(node.fullname)

    def registered_for_function_hook(self, node: SymbolNode) -> bool:
        return node.fullname in self._registered_for_function_hook

    def fill_out_concrete_children(self, fullname: str) -> None:
        if not fullname:
            return None

        sym = self._lookup_fully_qualified(fullname)
        if sym is not None and isinstance(sym.node, TypeInfo) and len(sym.node.mro) > 2:
            if any(
                m.fullname == fullnames.MODEL_CLASS_FULLNAME for m in sym.node.mro
            ) and not helpers.is_abstract_model(sym.node):
                for typ in sym.node.mro[1:-2]:
                    if typ.fullname != sym.node.fullname and helpers.is_abstract_model(typ):
                        self.concrete_for(typ).add_child(sym.node.fullname)

        return None

    def find_concrete_models(
        self, unbound_type: UnboundType, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> ProperType:
        args = unbound_type.args
        type_arg = api.analyze_type(args[0])

        if not isinstance(type_arg, Instance):
            return get_proper_type(UnionType(()))

        if helpers.is_annotated_model_fullname(type_arg.type.fullname):
            # If it's already a generated class, we want to use the original model as a base
            type_arg = type_arg.type.bases[0]

        concrete = self.concrete_for(type_arg.type).instances(sem_api)
        return get_proper_type(UnionType(tuple(concrete)))

    def find_concrete_querysets(
        self, unbound_type: UnboundType, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> ProperType:
        args = unbound_type.args
        type_arg = api.analyze_type(args[0])

        if not isinstance(type_arg, Instance | TypeVarType):
            return get_proper_type(UnionType(()))

        if hasattr(type_arg, "type"):
            if helpers.is_annotated_model_fullname(type_arg.type.fullname):
                # If it's already a generated class, we want to use the original model as a base
                type_arg = type_arg.type.bases[0]

        concrete = self.concrete_for(type_arg.type).querysets(sem_api)
        return get_proper_type(UnionType(tuple(concrete)))

    def find_default_queryset(
        self, unbound_type: UnboundType, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> ProperType:
        args = unbound_type.args
        type_arg = api.analyze_type(args[0])

        if isinstance(type_arg, TypeVarType):
            func = self._lookup_fully_qualified(sem_api.scope.current_target())
            assert func is not None
            assert func.node is not None
            self.register_for_function_hook(func.node)
            return get_proper_type(unbound_type)
        else:
            if isinstance(type_arg, AnyType):
                api.fail("Can't get default query set for Any", unbound_type)
                return unbound_type

            if isinstance(type_arg, UnionType):
                concrete = concrete_children.ConcreteChildren(
                    children=[
                        item.type.fullname for item in type_arg.items if isinstance(item, Instance)
                    ],
                    _lookup_fully_qualified=self._lookup_fully_qualified,
                    _django_context=self._django_context,
                    _fail_function=lambda reason: api.fail(reason, unbound_type),
                ).querysets(sem_api)
                return get_proper_type(UnionType(tuple(concrete)))

            assert isinstance(type_arg, Instance)
            return get_proper_type(
                concrete_children.ConcreteChildren(
                    children=[],
                    _lookup_fully_qualified=self._lookup_fully_qualified,
                    _django_context=self._django_context,
                    _fail_function=lambda reason: api.fail(reason, unbound_type),
                ).make_one_queryset(sem_api, type_arg.type)
            )

    def modify_default_queryset_return_type(
        self, ctx: FunctionContext, *, super_hook: Callable[[FunctionContext], MypyType] | None
    ) -> ProperType:
        assert isinstance(ctx.api, TypeChecker)
        assert isinstance(ctx.context, CallExpr)
        func = ctx.api.lookup_type(ctx.context.callee)
        assert isinstance(func, CallableType)

        func_def = ctx.api.expr_checker.analyze_ref_expr(ctx.context.callee)  # type: ignore[arg-type]
        assert isinstance(func_def, CallableType)

        assert isinstance(func.ret_type, UnboundType)
        type_var = func.ret_type.args[0]
        assert isinstance(type_var, UnboundType)

        arg_value: MypyType | None = None
        for arg in func_def.formal_arguments():
            arg_type = arg.typ
            if isinstance(arg_type, TypeType):
                arg_type = arg_type.item

            if isinstance(arg_type, TypeVarType) and arg_type.name == type_var.name:
                found = func.argument_by_name(arg.name)
                assert found is not None
                arg_value = found.typ
                break

        if arg_value is None:
            ctx.api.fail("Can't work out what value to bind the return type to", ctx.context)
            return AnyType(TypeOfAny.from_error)

        if isinstance(arg_value, UnionType):
            concrete = concrete_children.ConcreteChildren(
                children=[
                    item.type.fullname for item in arg_value.items if isinstance(item, Instance)
                ],
                _lookup_fully_qualified=self._lookup_fully_qualified,
                _django_context=self._django_context,
                _fail_function=lambda reason: ctx.api.fail(reason, ctx.context),
            ).querysets(ctx.api)
            return get_proper_type(UnionType(tuple(concrete)))

        if isinstance(arg_value, TypeType):
            arg_value = arg_value.item

        assert isinstance(arg_value, Instance)

        return get_proper_type(
            concrete_children.ConcreteChildren(
                children=[],
                _lookup_fully_qualified=self._lookup_fully_qualified,
                _django_context=self._django_context,
                _fail_function=lambda reason: ctx.api.fail(reason, ctx.context),
            ).make_one_queryset(ctx.api, arg_value.type)
        )

    def transform_type_var_classmethod(self, ctx: DynamicClassDefContext) -> None:
        assert isinstance(ctx.call, CallExpr)
        if not isinstance(ctx.call.args[0], StrExpr):
            ctx.api.fail(
                "First argument to Concrete.type_var must be a string of the name of the variable",
                ctx.call,
            )
            return

        name = ctx.call.args[0].value
        if name != ctx.name:
            ctx.api.fail(
                f"First argument {name} was not the name of the variable {ctx.name}",
                ctx.call,
            )
            return

        module = ctx.api.modules[ctx.api.cur_mod_id]
        if isinstance(module.names.get(name), TypeVarType):
            return

        parent: SymbolTableNode | None = None
        assert isinstance(ctx.api, SemanticAnalyzer)
        try:
            parent = ctx.api.lookup_type_node(ctx.call.args[1])
        except AssertionError:
            parent = None

        if parent is None:
            ctx.api.fail(
                "Second argument to Concrete.type_var must be the abstract model class to find concrete instances of",
                ctx.call,
            )
            return

        assert isinstance(parent.node, TypeInfo)
        assert isinstance(ctx.api, SemanticAnalyzer)

        object_type = ctx.api.named_type("builtins.object")
        values: list[MypyType] = []
        for instance in self.concrete_for(parent.node).instances(ctx.api):
            assert isinstance(instance, MypyType)
            values.append(instance)

        if self._mypy_version >= (1, 4):
            type_var_expr = TypeVarExpr(
                name=name,
                fullname=f"{ctx.api.cur_mod_id}.{name}",
                values=values,
                upper_bound=object_type,
                default=AnyType(TypeOfAny.from_omitted_generics),
            )
        else:
            type_var_expr = TypeVarExpr(  # type: ignore[call-arg]
                name=name,
                fullname=f"{ctx.api.cur_mod_id}.{name}",
                values=values,
                upper_bound=object_type,
            )

        module.names[name] = SymbolTableNode(GDEF, type_var_expr, plugin_generated=True)
        return None

    def extended_get_attribute_resolve_manager_method(self, ctx: AttributeContext) -> MypyType:
        # Copy from original resolve_manager_method

        if not isinstance(ctx.default_attr_type, AnyType):
            return ctx.default_attr_type
        elif ctx.default_attr_type.type_of_any != TypeOfAny.implementation_artifact:
            return ctx.default_attr_type

        if not isinstance(ctx.type, UnionType):
            return resolve_manager_method(ctx)

        method_name: str | None = None
        if isinstance(ctx.context, MemberExpr):
            method_name = ctx.context.name
        elif isinstance(ctx.context, CallExpr) and isinstance(ctx.context.callee, MemberExpr):
            method_name = ctx.context.callee.name

        if method_name is None or not all(
            isinstance(instance, Instance) for instance in ctx.type.items
        ):
            ctx.api.fail(
                f'Unable to resolve return type of queryset/manager method "{method_name}"',
                ctx.context,
            )
            return AnyType(TypeOfAny.from_error)

        resolved = tuple(
            resolve_manager_method_from_instance(
                instance=instance, method_name=method_name, ctx=ctx
            )
            for instance in ctx.type.items
            if isinstance(instance, Instance)
        )
        return get_proper_type(UnionType(resolved))
