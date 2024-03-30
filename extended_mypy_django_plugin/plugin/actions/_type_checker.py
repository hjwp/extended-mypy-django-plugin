from collections.abc import Callable
from typing import Protocol

from mypy.checker import TypeChecker
from mypy.nodes import CallExpr, MemberExpr, TypeInfo
from mypy.plugin import (
    AttributeContext,
    FunctionContext,
)
from mypy.types import (
    AnyType,
    CallableType,
    FormalArgument,
    Instance,
    TypeOfAny,
    TypeType,
    TypeVarType,
    UnboundType,
    UnionType,
    get_proper_type,
)
from mypy.types import Type as MypyType

from .. import _store


class ResolveManagerMethodFromInstance(Protocol):
    def __call__(
        self, instance: Instance, method_name: str, ctx: AttributeContext
    ) -> MypyType: ...


class TypeChecking:
    def __init__(self, store: _store.Store, *, api: TypeChecker) -> None:
        self.api = api
        self.store = store

    def modify_default_queryset_return_type(
        self,
        ctx: FunctionContext,
        *,
        super_hook: Callable[[FunctionContext], MypyType] | None,
        desired_annotation_fullname: str,
    ) -> MypyType:
        if not isinstance(ctx.default_return_type, UnboundType):
            return ctx.default_return_type

        context = ctx.context
        if not isinstance(context, CallExpr):
            return ctx.default_return_type

        if hasattr(self.api, "get_expression_type"):
            # In later mypy versions
            func = self.api.get_expression_type(context.callee)
        else:
            func = self.api.expr_checker.accept(context.callee)

        func = get_proper_type(func)

        if not isinstance(func, CallableType):
            self.api.fail("Expected to be operating on a callable", context)
            return AnyType(TypeOfAny.from_error)

        if not isinstance(func.ret_type, UnboundType):
            return ctx.default_return_type

        as_generic_type = self.api.named_generic_type(func.ret_type.name, [func.ret_type.args[0]])
        if as_generic_type.type.fullname != desired_annotation_fullname:
            return ctx.default_return_type

        if len(func.ret_type.args) != 1:
            self.api.fail("DefaultQuerySet takes only one argument", context)
            return AnyType(TypeOfAny.from_error)

        found_type: MypyType | None = None

        type_var = func.ret_type.args[0]

        if isinstance(type_var, UnboundType):
            match: FormalArgument | None = None
            for arg in func.formal_arguments():
                arg_name: str | None = None
                formal_arg_type = get_proper_type(arg.typ)
                if isinstance(formal_arg_type, TypeType) and isinstance(
                    formal_arg_type.item, TypeVarType
                ):
                    arg_name = formal_arg_type.item.name

                elif isinstance(arg, TypeVarType):
                    arg_name = formal_arg_type.name

                if arg_name and arg_name == type_var.name:
                    match = arg
                    break

            if match is not None:
                for arg_name, arg_type in zip(ctx.callee_arg_names, ctx.arg_types):
                    if arg_name == match.name:
                        found_type = arg_type[0]

            if found_type is None:
                self.api.fail("Failed to find an argument that matched the type var", context)
                return AnyType(TypeOfAny.from_error)

            found_type = get_proper_type(found_type)
            if isinstance(found_type, CallableType):
                type_var = found_type.ret_type
            else:
                type_var = found_type

        if not isinstance(type_var, Instance | UnionType):
            self.api.fail("Don't know what to do with what DefaultQuerySet was given", context)
            return AnyType(TypeOfAny.from_error)

        try:
            querysets = tuple(self.store.realise_querysets(type_var, self.lookup_info))
        except _store.RestartDmypy:
            self.api.fail("You probably need to restart dmypy", context)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", context)
            return AnyType(TypeOfAny.from_error)
        else:
            return UnionType(querysets)

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
