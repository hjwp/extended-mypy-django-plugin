import dataclasses
from collections.abc import Iterator, Mapping
from typing import Protocol

from mypy.checker import TypeChecker
from mypy.nodes import (
    CallExpr,
    Context,
    Decorator,
    FuncDef,
    MemberExpr,
    MypyFile,
    NameExpr,
    SymbolTableNode,
    TypeInfo,
    Var,
)
from mypy.plugin import AttributeContext, FunctionContext, MethodContext
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
from typing_extensions import Self

from .. import _known_annotations, _store


class LookupFunction(Protocol):
    def __call__(self, fullname: str) -> TypeInfo | None: ...


class ResolveManagerMethodFromInstance(Protocol):
    def __call__(
        self, instance: Instance, method_name: str, ctx: AttributeContext
    ) -> MypyType: ...


def _find_type_vars(
    item: MypyType, _chain: list[ProperType] | None = None
) -> list[TypeVarType | str]:
    if _chain is None:
        _chain = []

    result: list[TypeVarType | str] = []

    item = get_proper_type(item)

    if isinstance(item, TypeVarType):
        result.append(item)

    elif isinstance(item, UnboundType):
        if item.args:
            for arg in item.args:
                if arg not in _chain:
                    _chain.append(get_proper_type(arg))
                    result.extend(_find_type_vars(arg, _chain=_chain))
        else:
            _chain.append(item)
            result.append(item.name)

    return [r for r in result if r not in ("typing.Self", "typing_extensions.Self")]


@dataclasses.dataclass
class BasicTypeInfo:
    is_self: bool
    is_type: bool
    is_guard: bool
    api: TypeChecker
    func: CallableType
    context: CallExpr

    item: ProperType
    type_vars: list[TypeVarType | str]
    lookup_info: LookupFunction
    concrete_annotation: _known_annotations.KnownAnnotations | None

    @classmethod
    def create(
        cls,
        api: TypeChecker,
        func: CallableType,
        context: CallExpr,
        lookup_info: LookupFunction,
        item: MypyType | None = None,
    ) -> Self:
        is_type: bool = False
        is_guard: bool = False

        if item is None:
            if func.type_guard:
                is_guard = True
                item = func.type_guard
            else:
                item = func.ret_type

        item = get_proper_type(item)
        original = item
        if isinstance(item, TypeType):
            is_type = True
            item = item.item

        type_vars = _find_type_vars(item)

        is_self, concrete_annotation = cls._determine_if_concrete(
            api, context, lookup_info, original
        )

        return cls(
            api=api,
            func=func,
            item=item,
            context=context,
            is_self=is_self,
            is_type=is_type,
            is_guard=is_guard,
            type_vars=type_vars,
            lookup_info=lookup_info,
            concrete_annotation=concrete_annotation,
        )

    @classmethod
    def _determine_if_concrete(
        cls, api: TypeChecker, context: CallExpr, lookup_info: LookupFunction, item: ProperType
    ) -> tuple[bool, _known_annotations.KnownAnnotations | None]:
        def _resolve_in_module(
            names: Mapping[str, SymbolTableNode], want: str
        ) -> SymbolTableNode | None:
            if "." not in want:
                return names.get(want)
            else:
                first, rest = want.split(".", 1)
                found = names.get(first)
                if found is None or not isinstance(found.node, MypyFile):
                    return None

                return _resolve_in_module(found.node.names, rest)

        def _find_name_where_defined(cls: TypeInfo, method_name: str) -> tuple[bool, str] | None:
            method = cls.names.get(method_name)
            if method is None:
                for base in cls.direct_base_classes():
                    if ret := _find_name_where_defined(base, method_name):
                        return ret

                return None

            func = method.node
            if isinstance(func, Decorator):
                func = func.func
            elif not isinstance(func, FuncDef):
                return None

            module = api.modules.get(func.info.module_name)
            if module is None:
                return None

            if not isinstance(func.type, CallableType):
                return None

            ret_type = func.type.ret_type
            if not isinstance(ret_type, UnboundType):
                return None

            resolved = _resolve_in_module(module.names, ret_type.name)
            if resolved is None or not resolved.fullname:
                return None

            is_self: bool = False
            named_type_name = resolved.fullname

            for arg in ret_type.args:
                if isinstance(arg, UnboundType):
                    resolved = _resolve_in_module(module.names, arg.name)
                    if resolved is None or not resolved.fullname:
                        api.fail(
                            f"Failed to resolve argument for {func.info.fullname}: {arg}", context
                        )
                        return None

                    if resolved.fullname in ("typing_extensions.Self", "typing.Self"):
                        is_self = True
                        break

            return is_self, named_type_name

        def _find_where_defined() -> tuple[bool, str]:
            if not isinstance(context.callee, MemberExpr):
                return False, ""

            if not isinstance(context.callee.expr, NameExpr):
                return False, ""

            if context.callee.expr.node is None:
                return False, ""

            method_name = context.callee.name

            cls: TypeInfo | None = None
            node = context.callee.expr.node
            if isinstance(node, TypeInfo):
                cls = node
            elif isinstance(node, Var) and isinstance(
                node_type := get_proper_type(node.type), Instance | TypeType
            ):
                if isinstance(node_type, TypeType):
                    if not isinstance(node_type.item, Instance):
                        return False, ""
                    cls = node_type.item.type
                else:
                    cls = node_type.type

            if cls is None:
                return False, ""

            result = _find_name_where_defined(cls, method_name)
            if result is None:
                return False, ""

            return result

        is_self: bool = False
        named_type_name: str = ""
        if isinstance(item, UnboundType):
            try:
                found = api.lookup(item.name)
                if found is None or not isinstance(found.node, TypeInfo):
                    named_type_name = ""
                else:
                    named_type_name = found.node.fullname
            except KeyError:
                is_self, named_type_name = _find_where_defined()

        concrete_annotation: _known_annotations.KnownAnnotations | None = None
        if named_type_name:
            try:
                concrete_annotation = _known_annotations.KnownAnnotations(named_type_name)
            except ValueError:
                pass

        return is_self, concrete_annotation

    @property
    def contains_concrete_annotation(self) -> bool:
        if self.concrete_annotation is not None:
            return True

        for item in self.items():
            if item is not self and item.contains_concrete_annotation:
                return True

        return False

    def items(self) -> Iterator[Self]:
        if isinstance(self.item, UnionType):
            for item in self.item.items:
                yield self.__class__.create(
                    api=self.api,
                    func=self.func,
                    context=self.context,
                    item=item,
                    lookup_info=self.lookup_info,
                )
        else:
            yield self

    def map_type_vars(
        self,
        context: Context,
        callee_arg_names: list[str | None],
        arg_types: list[list[MypyType]],
    ) -> Mapping[TypeVarType | str, ProperType]:
        result: dict[TypeVarType | str, ProperType] = {}

        formal_by_name = {arg.name: arg.typ for arg in self.func.formal_arguments()}

        for arg_name, arg_type in zip(callee_arg_names, arg_types):
            underlying = get_proper_type(formal_by_name[arg_name])
            if isinstance(underlying, TypeType):
                underlying = underlying.item

            if isinstance(underlying, TypeVarType):
                found_type = get_proper_type(arg_type[0])
                if isinstance(found_type, CallableType):
                    found_type = get_proper_type(found_type.ret_type)

                result[underlying] = found_type
                result[underlying.name] = found_type

        if self.func.bound_args:
            bound_args = self.func.bound_args
            if len(bound_args) > 0 and bound_args[0]:
                bound_arg = get_proper_type(bound_args[0])
                if isinstance(bound_arg, TypeType):
                    bound_arg = bound_arg.item

                if "typing_extensions.Self" not in result:
                    result["typing_extensions.Self"] = bound_arg
                if "typing.Self" not in result:
                    result["typing.Self"] = bound_arg
                if "Self" not in result:
                    result["Self"] = bound_arg

        for type_var in self.type_vars:
            if type_var not in result:
                self.api.fail(
                    f"Failed to find an argument that matched the type var {type_var}", context
                )
                result[type_var] = AnyType(TypeOfAny.from_error)

        return result


class TypeChecking:
    def __init__(
        self, store: _store.Store, *, api: TypeChecker, lookup_info: LookupFunction
    ) -> None:
        self.api = api
        self.store = store
        self._lookup_info = lookup_info

    def _named_type(self, fullname: str, args: list[MypyType] | None = None) -> Instance:
        """
        Copied from what semantic analyzer does
        """
        node = self._lookup_info(fullname)
        assert isinstance(node, TypeInfo)
        if args:
            return Instance(node, args)
        return Instance(node, [AnyType(TypeOfAny.special_form)] * len(node.defn.type_vars))

    def _get_info(self, context: Context) -> BasicTypeInfo | None:
        if not isinstance(context, CallExpr):
            return None

        if hasattr(self.api, "get_expression_type"):
            # In later mypy versions
            func = self.api.get_expression_type(context.callee)
        else:
            func = self.api.expr_checker.accept(context.callee)

        func = get_proper_type(func)

        if not isinstance(func, CallableType):
            return None

        return BasicTypeInfo.create(
            api=self.api, context=context, func=func, lookup_info=self._lookup_info
        )

    def check_typeguard(self, context: Context) -> MypyType | None:
        info = self._get_info(context)
        if info is None:
            return None

        if info.is_guard and info.type_vars and info.contains_concrete_annotation:
            # Mypy plugin system doesn't currently provide an opportunity to resolve a type guard when it's for a concrete annotation that uses a type var
            self.api.fail(
                "Can't use a TypeGuard that uses a Concrete Annotation that uses type variables",
                context,
            )
            return AnyType(TypeOfAny.from_error)

        return None

    def modify_return_type(self, ctx: MethodContext | FunctionContext) -> MypyType | None:
        info = self._get_info(ctx.context)
        if info is None:
            return None

        if info.is_guard and info.type_vars and info.concrete_annotation is not None:
            # Mypy plugin system doesn't currently provide an opportunity to resolve a type guard when it's for a concrete annotation that uses a type var
            return None

        if not info.contains_concrete_annotation:
            return None

        result: list[MypyType] = []

        type_vars_map = info.map_type_vars(ctx.context, ctx.callee_arg_names, ctx.arg_types)

        for item in info.items():
            Known = _known_annotations.KnownAnnotations
            if item.concrete_annotation is None:
                if isinstance(item.item, TypeVarType):
                    result.append(type_vars_map.get(item.item, item.item))
                elif isinstance(item.item, UnboundType) and len(item.item.args) == 0:
                    result.append(type_vars_map.get(item.item.name, item.item))
                else:
                    result.append(item.item)
                continue

            is_type = item.is_type and not info.is_type

            # It has to be an instance or unbound type if it has a concrete annotation
            assert isinstance(item.item, Instance | UnboundType)

            if len(item.item.args) != 1:
                self.api.fail("Concrete Annotations must take exactly one argument", ctx.context)
                return AnyType(TypeOfAny.from_error)

            model = item.item.args[0]
            if isinstance(model, TypeVarType | UnboundType):
                found: ProperType | None = None
                if isinstance(model, TypeVarType):
                    found = type_vars_map.get(model)
                elif isinstance(model, UnboundType):
                    found = type_vars_map.get(model.name)

                if found is None:
                    self.api.fail(
                        f"Can't determine what model the type var {model} represents", ctx.context
                    )
                    return AnyType(TypeOfAny.from_error)
                else:
                    if isinstance(found, AnyType):
                        return found

                    model = found

            model = get_proper_type(model)
            instances: list[Instance] = []
            if isinstance(model, Instance):
                instances.append(model)
            elif isinstance(model, UnionType):
                for member in model.items:
                    member = get_proper_type(member)
                    if isinstance(member, Instance):
                        instances.append(member)
                    else:
                        self.api.fail(
                            f"Failed to have a list of instances to find for a concrete annotation, {member}",
                            ctx.context,
                        )
                        return AnyType(TypeOfAny.from_error)

            made: MypyType
            if item.concrete_annotation is Known.CONCRETE:
                made = self.get_concrete_types(ctx.context, instances=instances)
            elif item.concrete_annotation is Known.DEFAULT_QUERYSET:
                made = self.get_default_queryset_return_type(
                    ctx.context, instances=UnionType(tuple(instances))
                )
            elif item.concrete_annotation is Known.CONCRETE_QUERYSET:
                made = self.get_concrete_queryset_return_type(ctx.context, instances=instances)

            if is_type:
                made = TypeType(made)

            result.append(made)

        final: MypyType = UnionType(tuple(result))
        if info.is_type:
            final = TypeType(final)

        return final

    def get_concrete_queryset_return_type(
        self, context: Context, *, instances: list[Instance]
    ) -> MypyType:
        result: list[MypyType] = []
        for concrete in self.get_concrete_types(context, instances=instances).items:
            concrete = get_proper_type(concrete)
            assert isinstance(concrete, Instance)
            try:
                result.extend(self.store.realise_querysets(concrete, self.lookup_info))
            except _store.RestartDmypy as err:
                self.api.fail(f"You probably need to restart dmypy: {err}", context)
                return AnyType(TypeOfAny.from_error)
            except _store.UnionMustBeOfTypes:
                self.api.fail("Union must be of instances of models", context)
                return AnyType(TypeOfAny.from_error)

        return UnionType(tuple(result))

    def get_default_queryset_return_type(
        self, context: Context, *, instances: Instance | UnionType
    ) -> MypyType:
        try:
            querysets = tuple(self.store.realise_querysets(instances, self.lookup_info))
        except _store.RestartDmypy as err:
            self.api.fail(f"You probably need to restart dmypy: {err}", context)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", context)
            return AnyType(TypeOfAny.from_error)
        else:
            return UnionType(querysets)

    def get_concrete_types(self, context: Context, *, instances: list[Instance]) -> UnionType:
        result: list[MypyType] = []
        for item in instances:
            result.extend(
                self.store.retrieve_concrete_children_types(
                    item.type, self.lookup_info, self._named_type
                )
            )
        return UnionType(tuple(result))

    def extended_get_attribute_resolve_manager_method(
        self,
        ctx: AttributeContext,
        *,
        resolve_manager_method_from_instance: ResolveManagerMethodFromInstance,
    ) -> MypyType:
        """
        Copied from django-stubs after https://github.com/typeddjango/django-stubs/pull/2027

        A 'get_attribute_hook' that is intended to be invoked whenever the TypeChecker encounters
        an attribute on a class that has 'django.db.models.BaseManager' as a base.
        """
        # Skip (method) type that is currently something other than Any of type `implementation_artifact`
        default_attr_type = get_proper_type(ctx.default_attr_type)
        if not isinstance(default_attr_type, AnyType):
            return default_attr_type
        elif default_attr_type.type_of_any != TypeOfAny.implementation_artifact:
            return default_attr_type

        # (Current state is:) We wouldn't end up here when looking up a method from a custom _manager_.
        # That's why we only attempt to lookup the method for either a dynamically added or reverse manager.
        if isinstance(ctx.context, MemberExpr):
            method_name = ctx.context.name
        elif isinstance(ctx.context, CallExpr) and isinstance(ctx.context.callee, MemberExpr):
            method_name = ctx.context.callee.name
        else:
            ctx.api.fail("Unable to resolve return type of queryset/manager method", ctx.context)
            return AnyType(TypeOfAny.from_error)

        if isinstance(ctx.type, Instance):
            return resolve_manager_method_from_instance(
                instance=ctx.type, method_name=method_name, ctx=ctx
            )
        elif isinstance(ctx.type, UnionType) and all(
            isinstance(get_proper_type(instance), Instance) for instance in ctx.type.items
        ):
            items: list[Instance] = []
            for instance in ctx.type.items:
                inst = get_proper_type(instance)
                if isinstance(inst, Instance):
                    items.append(inst)

            resolved = tuple(
                resolve_manager_method_from_instance(
                    instance=inst, method_name=method_name, ctx=ctx
                )
                for inst in items
            )
            return UnionType(resolved)
        else:
            ctx.api.fail(
                f'Unable to resolve return type of queryset/manager method "{method_name}"',
                ctx.context,
            )
            return AnyType(TypeOfAny.from_error)

    def lookup_info(self, fullname: str) -> TypeInfo | None:
        return self.store._plugin_lookup_info(fullname)
