from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    Instance,
    ProperType,
    TypeVarType,
    UnboundType,
    UnionType,
    get_proper_type,
)

from .. import _helpers, _store


class TypeAnalyzing:
    def __init__(self, store: _store.Store) -> None:
        self.store = store

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

        concrete = self.store.concrete_for(type_arg.type).instances(sem_api)
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

        concrete = self.store.concrete_for(type_arg.type).querysets(sem_api)
        return get_proper_type(UnionType(tuple(concrete)))

    def find_default_queryset(
        self, unbound_type: UnboundType, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> ProperType:
        args = unbound_type.args
        type_arg = api.analyze_type(args[0])

        if isinstance(type_arg, TypeVarType):
            func = self.store._lookup_fully_qualified(sem_api.scope.current_target())
            assert func is not None
            assert func.node is not None
            self.store.register_for_function_hook(func.node)
            return get_proper_type(unbound_type)
        else:
            if isinstance(type_arg, AnyType):
                api.fail("Can't get default query set for Any", unbound_type)
                return unbound_type

            if isinstance(type_arg, UnionType):
                concrete = self.store.make_concrete_children(
                    children=[
                        item.type.fullname for item in type_arg.items if isinstance(item, Instance)
                    ],
                    _lookup_fully_qualified=self.store._lookup_fully_qualified,
                    _django_context=self.store._django_context,
                    _fail_function=lambda reason: api.fail(reason, unbound_type),
                ).querysets(sem_api)
                return get_proper_type(UnionType(tuple(concrete)))

            assert isinstance(type_arg, Instance)
            return get_proper_type(
                self.store.make_concrete_children(
                    children=[],
                    _lookup_fully_qualified=self.store._lookup_fully_qualified,
                    _django_context=self.store._django_context,
                    _fail_function=lambda reason: api.fail(reason, unbound_type),
                ).make_one_queryset(sem_api, type_arg.type)
            )
