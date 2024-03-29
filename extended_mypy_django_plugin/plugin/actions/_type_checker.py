from collections.abc import Callable

from mypy.checker import TypeChecker
from mypy.nodes import (
    CallExpr,
    MemberExpr,
)
from mypy.plugin import (
    AttributeContext,
    FunctionContext,
)
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
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)

from .. import _store


class TypeChecking:
    def __init__(self, store: _store.Store) -> None:
        self.store = store

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

            concrete = self.store.make_concrete_children(
                children=[
                    item.type.fullname for item in type_var.items if isinstance(item, Instance)
                ],
                _lookup_fully_qualified=self.store._lookup_fully_qualified,
                _django_context=self.store._django_context,
                _fail_function=lambda reason: api.fail(reason, context),
            ).querysets(api)
            return get_proper_type(UnionType(tuple(concrete)))

        return get_proper_type(
            self.store.make_concrete_children(
                children=[],
                _lookup_fully_qualified=self.store._lookup_fully_qualified,
                _django_context=self.store._django_context,
                _fail_function=lambda reason: api.fail(reason, context),
            ).make_one_queryset(api, type_var.type)
        )

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
