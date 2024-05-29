from mypy.nodes import TypeInfo
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    Instance,
    TypeOfAny,
    TypeType,
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

    def _analyze_first_type_arg(
        self, unbound_type: UnboundType
    ) -> Instance | TypeType | UnionType | None:
        args = unbound_type.args
        type_arg = get_proper_type(self.api.analyze_type(args[0]))

        if not isinstance(type_arg, Instance | TypeType | UnionType):
            return None

        return type_arg

    def find_concrete_models(self, unbound_type: UnboundType) -> MypyType:
        type_arg = self._analyze_first_type_arg(unbound_type)
        if type_arg is None:
            return unbound_type

        is_type = False
        if isinstance(type_arg, TypeType):
            is_type = True
            type_arg = type_arg.item

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

        made = UnionType(concrete)
        if is_type:
            return TypeType(made)
        else:
            return made

    def find_concrete_querysets(self, unbound_type: UnboundType) -> MypyType:
        type_arg = self._analyze_first_type_arg(unbound_type)
        if type_arg is None:
            return unbound_type

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
        type_arg = self._analyze_first_type_arg(unbound_type)
        if type_arg is None:
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
