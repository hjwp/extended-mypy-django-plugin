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
    FormalArgument,
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

from . import _concrete_children, _fullnames, _helpers


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

    def concrete_for(self, info: TypeInfo) -> _concrete_children.ConcreteChildren:
        self.sync_metadata(info)
        metadata = self._metadata[info.fullname]
        if "concrete_children" not in metadata:
            metadata["concrete_children"] = []

        children = metadata["concrete_children"]
        assert isinstance(children, list)
        return _concrete_children.ConcreteChildren(
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
                m.fullname == _fullnames.MODEL_CLASS_FULLNAME for m in sym.node.mro
            ) and not _helpers.is_abstract_model(sym.node):
                for typ in sym.node.mro[1:-2]:
                    if typ.fullname != sym.node.fullname and _helpers.is_abstract_model(typ):
                        self.concrete_for(typ).add_child(sym.node.fullname)

        return None

    def find_concrete_models(
        self, unbound_type: UnboundType, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> ProperType:
        args = unbound_type.args
        type_arg = api.analyze_type(args[0])

        if not isinstance(type_arg, Instance):
            return get_proper_type(UnionType(()))

        if _helpers.is_annotated_model_fullname(type_arg.type.fullname):
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
            if _helpers.is_annotated_model_fullname(type_arg.type.fullname):
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
                concrete = _concrete_children.ConcreteChildren(
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
                _concrete_children.ConcreteChildren(
                    children=[],
                    _lookup_fully_qualified=self._lookup_fully_qualified,
                    _django_context=self._django_context,
                    _fail_function=lambda reason: api.fail(reason, unbound_type),
                ).make_one_queryset(sem_api, type_arg.type)
            )

    def modify_default_queryset_return_type(
        self,
        ctx: FunctionContext,
        *,
        context: CallExpr,
        api: TypeChecker,
        super_hook: Callable[[FunctionContext], MypyType] | None,
        desired_annotation_fullname: str,
    ) -> ProperType:
        if not isinstance(ctx.default_return_type, UnboundType):
            return get_proper_type(ctx.default_return_type)

        func = api.get_expression_type(context.callee)
        assert isinstance(func, CallableType)

        if not isinstance(func.ret_type, UnboundType):
            return get_proper_type(ctx.default_return_type)

        if len(func.ret_type.args) != 1:
            api.fail("DefaultQuerySet takes only one argument", context)
            return AnyType(TypeOfAny.from_error)

        as_generic_type = api.named_generic_type(func.ret_type.name, [func.ret_type.args[0]])
        if as_generic_type.type.fullname != desired_annotation_fullname:
            return ctx.default_return_type

        found_type: MypyType | None = None

        type_var = func.ret_type.args[0]

        if isinstance(type_var, UnboundType):
            match: FormalArgument | None = None
            for arg in func.formal_arguments():
                arg_name: str | None = None
                if isinstance(arg.typ, TypeType) and isinstance(arg.typ.item, TypeVarType):
                    arg_name = arg.typ.item.name

                elif isinstance(arg, TypeVarType):
                    arg_name = arg.typ.name

                if arg_name and arg_name == type_var.name:
                    match = arg
                    break

            if match is not None:
                for arg_name, arg_type in zip(ctx.callee_arg_names, ctx.arg_types):
                    if arg_name == match.name:
                        found_type = arg_type[0]

            if found_type is None:
                api.fail("Failed to find an argument that matched the type var", context)
                return AnyType(TypeOfAny.from_error)

            if isinstance(found_type, CallableType):
                type_var = found_type.ret_type
            else:
                type_var = found_type

        if not isinstance(type_var, Instance | UnionType):
            api.fail("Don't know what to do with what DefaultQuerySet was given", context)
            return AnyType(TypeOfAny.from_error)

        if isinstance(type_var, UnionType):
            if not all(isinstance(item, Instance) for item in type_var.items):
                api.fail("DefaultQuerySet needs to be given Type or an instance of Types", context)
                return AnyType(TypeOfAny.from_error)

            concrete = _concrete_children.ConcreteChildren(
                children=[
                    item.type.fullname for item in type_var.items if isinstance(item, Instance)
                ],
                _lookup_fully_qualified=self._lookup_fully_qualified,
                _django_context=self._django_context,
                _fail_function=lambda reason: api.fail(reason, context),
            ).querysets(api)
            return get_proper_type(UnionType(tuple(concrete)))

        return get_proper_type(
            _concrete_children.ConcreteChildren(
                children=[],
                _lookup_fully_qualified=self._lookup_fully_qualified,
                _django_context=self._django_context,
                _fail_function=lambda reason: api.fail(reason, context),
            ).make_one_queryset(api, type_var.type)
        )

    def transform_type_var_classmethod(
        self, ctx: DynamicClassDefContext, api: SemanticAnalyzer
    ) -> None:
        if not isinstance(ctx.call.args[0], StrExpr):
            api.fail(
                "First argument to Concrete.type_var must be a string of the name of the variable",
                ctx.call,
            )
            return

        name = ctx.call.args[0].value
        if name != ctx.name:
            api.fail(
                f"First argument {name} was not the name of the variable {ctx.name}",
                ctx.call,
            )
            return

        module = api.modules[api.cur_mod_id]
        if isinstance(module.names.get(name), TypeVarType):
            return

        parent: SymbolTableNode | None = None
        try:
            parent = api.lookup_type_node(ctx.call.args[1])
        except AssertionError:
            parent = None

        if parent is None:
            api.fail(
                "Second argument to Concrete.type_var must be the abstract model class to find concrete instances of",
                ctx.call,
            )
            return

        if not isinstance(parent.node, TypeInfo):
            api.fail("Second argument to Concrete.type_var was not pointing at a class", ctx.call)
            return

        object_type = api.named_type("builtins.object")
        values: list[MypyType] = []
        for instance in self.concrete_for(parent.node).instances(api):
            values.append(instance)

        if self._mypy_version >= (1, 4):
            type_var_expr = TypeVarExpr(
                name=name,
                fullname=f"{api.cur_mod_id}.{name}",
                values=values,
                upper_bound=object_type,
                default=AnyType(TypeOfAny.from_omitted_generics),
            )
        else:
            type_var_expr = TypeVarExpr(  # type: ignore[call-arg]
                name=name,
                fullname=f"{api.cur_mod_id}.{name}",
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
