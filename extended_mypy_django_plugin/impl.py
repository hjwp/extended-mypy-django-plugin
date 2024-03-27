import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from django.db.models import Manager
from mypy.checker import TypeChecker
from mypy.nodes import (
    GDEF,
    AssignmentStmt,
    CallExpr,
    MemberExpr,
    NameExpr,
    StrExpr,
    SymbolNode,
    SymbolTableNode,
    TypeInfo,
    TypeVarExpr,
    Var,
)
from mypy.plugin import (
    AnalyzeTypeContext,
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
    LiteralType,
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
from mypy_django_plugin.lib import fullnames, helpers
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)


class FailFunction(Protocol):
    def __call__(self, reason: str) -> None: ...


class Metadata:
    @dataclasses.dataclass(frozen=True)
    class ConcreteChildren:
        children: list[str]
        _lookup_fully_qualified: Callable[[str], SymbolTableNode | None]
        _django_context: DjangoContext
        _fail_function: FailFunction

        def add_child(self, name: str) -> None:
            if name not in self.children:
                self.children.append(name)

            reviewed: list[str] = []
            for child in self.children:
                child_sym = self._lookup_fully_qualified(child)
                if child_sym and isinstance(child_sym.node, TypeInfo):
                    if not is_abstract_model(child_sym.node):
                        reviewed.append(child_sym.node.fullname)

            if reviewed != self.children:
                self.children.clear()
                self.children.extend(reviewed)

        def get_dynamic_manager(
            self, api: TypeChecker | SemanticAnalyzer, fullname: str, manager: "Manager[Any]"
        ) -> TypeInfo | None:
            base_manager_fullname = helpers.get_class_fullname(manager.__class__.__bases__[0])
            base_manager_info = helpers.lookup_fully_qualified_typeinfo(api, base_manager_fullname)

            generated_managers: dict[str, str]
            if (
                base_manager_info is None
                or "from_queryset_managers" not in base_manager_info.metadata
            ):
                generated_managers = {}
            else:
                generated_managers = base_manager_info.metadata["from_queryset_managers"]

            generated_manager_name: str | None = generated_managers.get(fullname)
            if generated_manager_name is None:
                return None

            return helpers.lookup_fully_qualified_typeinfo(api, generated_manager_name)

        def make_one_queryset(
            self, api: SemanticAnalyzer | TypeAnalyser | TypeChecker, info: TypeInfo
        ) -> Instance:
            model_cls = self._django_context.get_model_class_by_fullname(info.fullname)
            assert model_cls is not None
            manager = model_cls._default_manager
            if manager is None:
                self._fail_function("Cannot make a queryset for an abstract model")
                return AnyType(TypeOfAny.from_error)

            manager_info: TypeInfo | None

            if isinstance(manager, Manager):
                manager_fullname = helpers.get_class_fullname(manager.__class__)
                sem_api: SemanticAnalyzer | TypeChecker
                if isinstance(api, TypeAnalyser):
                    assert isinstance(api.api, SemanticAnalyzer)
                    sem_api = api.api
                else:
                    sem_api = api

                manager_info = self.get_dynamic_manager(sem_api, manager_fullname, manager)

            if manager_info is None:
                found = self._lookup_fully_qualified(fullnames.QUERYSET_CLASS_FULLNAME)
                assert found is not None
                assert isinstance(found.node, TypeInfo)

                if "_default_manager" not in info.names:
                    concrete: ProperType
                    try:
                        concrete = api.named_type(info.fullname)
                    except AssertionError:
                        concrete = AnyType(TypeOfAny.from_error)
                        self._fail_function("dmypy likely needs to be restarted")
                    return Instance(found.node, (concrete, concrete))
                else:
                    manager_type_node = info.names["_default_manager"].node
                    assert manager_type_node is not None
                    assert isinstance(manager_type_node, Var)
                    manager_type = manager_type_node.type
                    assert isinstance(manager_type, Instance)
                    args = manager_type.args
                    if len(args) == 1:
                        args = (args[0], args[0])
                    return Instance(found.node, args)

            metadata = helpers.get_django_metadata(manager_info)
            queryset_fullname = metadata["from_queryset_manager"]
            queryset = self._lookup_fully_qualified(queryset_fullname)
            assert queryset is not None
            assert isinstance(queryset.node, TypeInfo)
            assert not queryset.node.is_generic()
            return Instance(queryset.node, [])

        def instances(self, api: TypeChecker | SemanticAnalyzer) -> Sequence[Instance]:
            concrete: list[Instance] = []
            reviewed: list[str] = []

            for name in self.children:
                try:
                    nxt = api.named_type(name)
                except AssertionError:
                    pass
                else:
                    concrete.append(nxt)
                    reviewed.append(name)

            if self.children != reviewed:
                self.children.clear()
                self.children.extend(reviewed)

            return concrete

        def querysets(self, api: TypeChecker | SemanticAnalyzer) -> Sequence[Instance]:
            querysets: list[Instance] = []
            for instance in self.instances(api):
                querysets.append(self.make_one_queryset(api, instance.type))
            return querysets

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

    def concrete_for(self, info: TypeInfo) -> ConcreteChildren:
        self.sync_metadata(info)
        metadata = self._metadata[info.fullname]
        if "concrete_children" not in metadata:
            metadata["concrete_children"] = []

        children = metadata["concrete_children"]
        assert isinstance(children, list)
        return self.ConcreteChildren(
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
            ) and not is_abstract_model(sym.node):
                for typ in sym.node.mro[1:-2]:
                    if typ.fullname != sym.node.fullname and is_abstract_model(typ):
                        self.concrete_for(typ).add_child(sym.node.fullname)

        return None

    def find_concrete_models(self, ctx: AnalyzeTypeContext) -> ProperType:
        args = ctx.type.args
        type_arg = ctx.api.analyze_type(args[0])

        if not isinstance(type_arg, Instance):
            return get_proper_type(UnionType(()))

        assert isinstance(ctx.api, TypeAnalyser)
        assert isinstance(ctx.api.api, SemanticAnalyzer)

        if helpers.is_annotated_model_fullname(type_arg.type.fullname):
            # If it's already a generated class, we want to use the original model as a base
            type_arg = type_arg.type.bases[0]

        concrete = self.concrete_for(type_arg.type).instances(ctx.api.api)
        return get_proper_type(UnionType(tuple(concrete)))

    def find_concrete_querysets(self, ctx: AnalyzeTypeContext) -> ProperType:
        args = ctx.type.args
        type_arg = ctx.api.analyze_type(args[0])

        assert isinstance(ctx.api, TypeAnalyser)
        assert isinstance(ctx.api.api, SemanticAnalyzer)

        if not isinstance(type_arg, Instance | TypeVarType):
            return get_proper_type(UnionType(()))

        if hasattr(type_arg, "type"):
            if helpers.is_annotated_model_fullname(type_arg.type.fullname):
                # If it's already a generated class, we want to use the original model as a base
                type_arg = type_arg.type.bases[0]

        concrete = self.concrete_for(type_arg.type).querysets(ctx.api.api)
        return get_proper_type(UnionType(tuple(concrete)))

    def find_default_queryset(self, ctx: AnalyzeTypeContext) -> ProperType:
        args = ctx.type.args
        type_arg = ctx.api.analyze_type(args[0])

        assert isinstance(ctx.api, TypeAnalyser)
        assert isinstance(ctx.api.api, SemanticAnalyzer)

        if isinstance(type_arg, TypeVarType):
            func = self._lookup_fully_qualified(ctx.api.api.scope.current_target())
            assert func is not None
            assert func.node is not None
            self.register_for_function_hook(func.node)
            return get_proper_type(ctx.type)
        else:
            if isinstance(type_arg, AnyType):
                ctx.api.fail("Can't get default query set for Any", ctx.context)
                return ctx.type

            if isinstance(type_arg, UnionType):
                concrete = self.ConcreteChildren(
                    children=[
                        item.type.fullname for item in type_arg.items if isinstance(item, Instance)
                    ],
                    _lookup_fully_qualified=self._lookup_fully_qualified,
                    _django_context=self._django_context,
                    _fail_function=lambda reason: ctx.api.fail(reason, ctx.context),
                ).querysets(ctx.api.api)
                return get_proper_type(UnionType(tuple(concrete)))

            assert isinstance(type_arg, Instance)
            return get_proper_type(
                self.ConcreteChildren(
                    children=[],
                    _lookup_fully_qualified=self._lookup_fully_qualified,
                    _django_context=self._django_context,
                    _fail_function=lambda reason: ctx.api.fail(reason, ctx.context),
                ).make_one_queryset(ctx.api.api, type_arg.type)
            )

    def modify_default_queryset_return_type(
        self, ctx: FunctionContext, *, super_hook: Callable[[FunctionContext], None] | None
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
            concrete = self.ConcreteChildren(
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
            self.ConcreteChildren(
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


if hasattr(helpers, "is_abstract_model"):
    is_abstract_model = helpers.is_abstract_model
else:

    def is_model_type(info: TypeInfo) -> bool:
        return info.metaclass_type is not None and info.metaclass_type.type.has_base(
            "django.db.models.base.ModelBase"
        )

    def is_abstract_model(model: TypeInfo) -> bool:
        if not is_model_type(model):
            return False

        metadata = helpers.get_django_metadata(model)
        if metadata.get("is_abstract_model") is not None:
            return metadata["is_abstract_model"]

        meta = model.names.get("Meta")
        # Check if 'abstract' is declared in this model's 'class Meta' as
        # 'abstract = True' won't be inherited from a parent model.
        if meta is not None and isinstance(meta.node, TypeInfo) and "abstract" in meta.node.names:
            for stmt in meta.node.defn.defs.body:
                if (
                    # abstract =
                    isinstance(stmt, AssignmentStmt)
                    and len(stmt.lvalues) == 1
                    and isinstance(stmt.lvalues[0], NameExpr)
                    and stmt.lvalues[0].name == "abstract"
                ):
                    # abstract = True (builtins.bool)
                    rhs_is_true = helpers.parse_bool(stmt.rvalue) is True
                    # abstract: Literal[True]
                    is_literal_true = (
                        isinstance(stmt.type, LiteralType) and stmt.type.value is True
                    )
                    metadata["is_abstract_model"] = rhs_is_true or is_literal_true
                    return metadata["is_abstract_model"]

        metadata["is_abstract_model"] = False
        return False
