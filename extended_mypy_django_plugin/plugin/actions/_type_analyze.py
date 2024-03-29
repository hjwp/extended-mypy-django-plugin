from mypy.nodes import TypeInfo
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    Instance,
    TypeOfAny,
    TypeVarType,
    UnboundType,
    UnionType,
)
from mypy.types import (
    Type as MypyType,
)

from .. import _store


def is_annotated_model_fullname(model_cls_fullname: str) -> bool:
    return model_cls_fullname.startswith(_store.WITH_ANNOTATIONS_FULLNAME + "[")


class TypeAnalyzing:
    def __init__(
        self, store: _store.Store, *, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> None:
        self.api = api
        self.sem_api = sem_api
        self.store = store

    def find_concrete_models(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = self.api.analyze_type(args[0])

        if not isinstance(type_arg, Instance):
            return UnionType(())

        if is_annotated_model_fullname(type_arg.type.fullname):
            # If it's already a generated class, we want to use the original model as a base
            type_arg = type_arg.type.bases[0]

        concrete = tuple(
            self.store.concrete_children_for(self.sem_api, type_arg.type, self.lookup_info)
        )
        if not concrete:
            self.api.fail(f"No concrete models found for {type_arg.type.fullname}", unbound_type)
            return AnyType(TypeOfAny.from_error)

        return UnionType(concrete)

    def find_concrete_querysets(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = self.api.analyze_type(args[0])

        if not isinstance(type_arg, Instance | TypeVarType):
            return UnionType(())

        if hasattr(type_arg, "type"):
            if is_annotated_model_fullname(type_arg.type.fullname):
                # If it's already a generated class, we want to use the original model as a base
                type_arg = type_arg.type.bases[0]

        concrete = tuple(
            self.store.concrete_children_for(self.sem_api, type_arg.type, self.lookup_info)
        )
        if not concrete:
            self.api.fail(f"No concrete models found for {type_arg.type.fullname}", unbound_type)
            return AnyType(TypeOfAny.from_error)

        try:
            querysets = tuple(self.store.realise_querysets(UnionType(concrete), self.lookup_info))
        except _store.RestartDmypy:
            self.api.fail("You probably need to restart dmypy", unbound_type)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", unbound_type)
            return AnyType(TypeOfAny.from_error)
        else:
            return UnionType(querysets)

    def find_default_queryset(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = self.api.analyze_type(args[0])

        if isinstance(type_arg, AnyType):
            self.api.fail("Can't get default query set for Any", unbound_type)
            return unbound_type

        if isinstance(type_arg, TypeVarType):
            func = self.api.lookup_fully_qualified(self.sem_api.scope.current_target())
            assert func is not None
            assert func.node is not None
            self.store.register_for_function_hook(func.node)
            return unbound_type

        if not isinstance(type_arg, Instance | UnionType):
            self.api.fail("Default queryset needs a class to find for", unbound_type)
            return unbound_type

        try:
            querysets = tuple(self.store.realise_querysets(type_arg, self.lookup_info))
        except _store.RestartDmypy:
            self.api.fail("You probably need to restart dmypy", unbound_type)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", unbound_type)
            return unbound_type
        else:
            return UnionType(querysets)

    def lookup_info(self, fullname: str) -> TypeInfo | None:
        instance = self.sem_api.named_type_or_none(fullname)
        if instance:
            return instance.type

        return self.store._plugin_lookup_info(fullname)
