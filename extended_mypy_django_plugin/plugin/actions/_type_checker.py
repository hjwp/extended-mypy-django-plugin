import dataclasses
from collections.abc import Iterator, Mapping
from itertools import chain
from typing import Protocol

from mypy.checker import TypeChecker
from mypy.nodes import CallExpr, Context, MemberExpr, TypeInfo
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

    return result


@dataclasses.dataclass
class BasicTypeInfo:
    is_type: bool
    is_guard: bool
    api: TypeChecker
    func: CallableType

    item: ProperType
    type_vars: list[TypeVarType | str]
    concrete_annotation: _known_annotations.KnownAnnotations | None

    @classmethod
    def create(cls, api: TypeChecker, func: CallableType, item: MypyType | None = None) -> Self:
        is_type: bool = False
        is_guard: bool = False

        if item is None:
            if func.type_guard:
                is_guard = True
                item = func.type_guard
            else:
                item = func.ret_type

        item = get_proper_type(item)
        if isinstance(item, TypeType):
            is_type = True
            item = item.item

        type_vars = _find_type_vars(item)

        concrete_annotation: _known_annotations.KnownAnnotations | None = None
        if isinstance(item, UnboundType):
            try:
                named_generic_type_name = api.named_generic_type(
                    item.name, list(item.args)
                ).type.fullname
            except AssertionError:
                named_generic_type_name = ""

            try:
                concrete_annotation = _known_annotations.KnownAnnotations(named_generic_type_name)
            except ValueError:
                pass

        return cls(
            api=api,
            func=func,
            item=item,
            is_type=is_type,
            is_guard=is_guard,
            type_vars=type_vars,
            concrete_annotation=concrete_annotation,
        )

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
                yield self.__class__.create(self.api, self.func, item)
        else:
            yield self

    def map_type_vars(
        self, context: Context, callee_arg_names: list[str | None], arg_types: list[list[MypyType]]
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

        for type_var in self.type_vars:
            if type_var not in result:
                self.api.fail(
                    f"Failed to find an argument that matched the type var {type_var}", context
                )
                result[type_var] = AnyType(TypeOfAny.from_error)

        return result


class TypeChecking:
    def __init__(self, store: _store.Store, *, api: TypeChecker) -> None:
        self.api = api
        self.store = store

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

        return BasicTypeInfo.create(api=self.api, func=func)

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
        return UnionType(
            tuple(
                chain.from_iterable(
                    [
                        self.store.retrieve_concrete_children(
                            item.type, self.api.named_type, context.line
                        )
                        for item in instances
                    ]
                )
            )
        )

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
