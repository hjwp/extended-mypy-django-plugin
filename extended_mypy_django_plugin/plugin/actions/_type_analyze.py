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
    get_proper_type,
)
from mypy.types import (
    Type as MypyType,
)

from .. import _store


class TypeAnalyzing:
    def __init__(
        self, store: _store.Store, *, api: TypeAnalyser, sem_api: SemanticAnalyzer
    ) -> None:
        self.api = api
        self.sem_api = sem_api
        self.store = store

    def find_concrete_models(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = get_proper_type(self.api.analyze_type(args[0]))

        if not isinstance(type_arg, Instance):
            return unbound_type

        if type_arg.type.fullname == "djangoexample.exampleapp.models.Parent":
            from mypy.nodes import ImportFrom, TypeAlias

            found = self.api.lookup_qualified("__virtual_Concrete__Parent", unbound_type)
            if found is None:
                imp = ImportFrom(
                    "__virtual_extended_mypy_django_plugin_report__.mod_3347844205",
                    0,
                    [("Concrete__Parent", "__virtual_Concrete__Parent")],
                )
                imp.line = unbound_type.line
                imp.column = unbound_type.column
                imp.is_mypy_only = True
                imp.accept(self.sem_api)

            name = "__virtual_Concrete__Parent"
            found = self.sem_api.lookup_qualified(name, unbound_type)
            if found is None:
                return unbound_type

            if isinstance(found.node, TypeAlias):
                return found.node.target
            else:
                return found.node

        concrete = tuple(
            self.store.retrieve_concrete_children_types(
                type_arg.type, self.lookup_info, self.sem_api.named_type_or_none
            )
        )
        if not concrete:
            if self.sem_api.final_iteration:
                self.api.fail(
                    f"No concrete models found for {type_arg.type.fullname}", unbound_type
                )
                return AnyType(TypeOfAny.from_error)
            else:
                self.sem_api.defer()
                return unbound_type

        return UnionType(concrete)

    def find_concrete_querysets(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = get_proper_type(self.api.analyze_type(args[0]))

        if not isinstance(type_arg, Instance):
            return UnionType(())

        concrete = tuple(
            self.store.retrieve_concrete_children_types(
                type_arg.type, self.lookup_info, self.sem_api.named_type_or_none
            )
        )
        if not concrete:
            self.api.fail(f"No concrete models found for {type_arg.type.fullname}", unbound_type)
            return AnyType(TypeOfAny.from_error)

        try:
            querysets = tuple(self.store.realise_querysets(UnionType(concrete), self.lookup_info))
        except _store.RestartDmypy as err:
            self.api.fail(f"You probably need to restart dmypy: {err}", unbound_type)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", unbound_type)
            return AnyType(TypeOfAny.from_error)
        else:
            return UnionType(querysets)

    def find_default_queryset(self, unbound_type: UnboundType) -> MypyType:
        args = unbound_type.args
        type_arg = get_proper_type(self.api.analyze_type(args[0]))

        if isinstance(type_arg, AnyType):
            self.api.fail("Can't get default query set for Any", unbound_type)
            return unbound_type

        if isinstance(type_arg, TypeVarType):
            return unbound_type

        if not isinstance(type_arg, Instance | UnionType):
            self.api.fail("Default queryset needs a class to find for", unbound_type)
            return unbound_type

        try:
            querysets = tuple(self.store.realise_querysets(type_arg, self.lookup_info))
        except _store.RestartDmypy as err:
            self.api.fail(f"You probably need to restart dmypy: {err}", unbound_type)
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
