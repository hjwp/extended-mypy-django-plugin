from collections.abc import Iterator, Sequence

from mypy.nodes import TypeInfo
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    Instance,
    ProperType,
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

    def _flatten_union(self, typ: ProperType) -> Iterator[ProperType]:
        if isinstance(typ, UnionType):
            for item in typ.items:
                yield from self._flatten_union(get_proper_type(item))
        else:
            yield typ

    def _analyze_first_type_arg(
        self, unbound_type: UnboundType, expand: bool = True
    ) -> tuple[bool, Sequence[Instance] | None]:
        args = unbound_type.args
        type_arg = get_proper_type(self.api.analyze_type(args[0]))

        is_type: bool = False
        if isinstance(type_arg, TypeType):
            is_type = True
            type_arg = type_arg.item

        if isinstance(type_arg, AnyType):
            self.api.fail("Tried to use concrete annotations on a typing.Any", unbound_type)
            return False, None

        if not isinstance(type_arg, Instance | UnionType):
            return False, None

        if isinstance(type_arg, Instance):
            type_arg = UnionType((type_arg,))

        all_types = list(self._flatten_union(type_arg))
        all_instances: list[Instance] = []
        not_all_instances: bool = False
        for item in all_types:
            if not isinstance(item, Instance):
                self.sem_api.fail(
                    f"Expected to operate on specific classes, got a {item.__class__.__name__}: {item}",
                    unbound_type,
                )
                not_all_instances = True
            else:
                all_instances.append(item)

        if not_all_instances:
            return False, None

        if not expand:
            return is_type, tuple(all_instances)

        concrete: list[Instance] = []
        names = ", ".join([item.type.fullname for item in all_instances])

        for item in all_instances:
            concrete.extend(
                self.store.retrieve_concrete_children_types(
                    item.type, self.lookup_info, self.sem_api.named_type_or_none
                )
            )

        if not concrete:
            if self.sem_api.final_iteration:
                self.api.fail(f"No concrete models found for {names}", unbound_type)
                return False, None
            else:
                self.sem_api.defer()
                return False, None

        return is_type, tuple(concrete)

    def _make_union(
        self, is_type: bool, instances: Sequence[Instance]
    ) -> UnionType | Instance | TypeType:
        made: UnionType | TypeType | Instance
        if len(instances) == 1:
            made = instances[0]
        else:
            made = UnionType(instances)

        if is_type:
            return TypeType(made)
        else:
            return made

    def find_concrete_models(self, unbound_type: UnboundType) -> MypyType:
        is_type, concrete = self._analyze_first_type_arg(unbound_type)
        if concrete is None:
            return unbound_type

        return self._make_union(is_type, concrete)

    def find_concrete_querysets(self, unbound_type: UnboundType) -> MypyType:
        is_type, concrete = self._analyze_first_type_arg(unbound_type)
        if concrete is None:
            return unbound_type

        try:
            querysets = tuple(self.store.realise_querysets(UnionType(concrete), self.lookup_info))
        except _store.RestartDmypy as err:
            self.api.fail(f"You probably need to restart dmypy: {err}", unbound_type)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", unbound_type)
            return AnyType(TypeOfAny.from_error)
        else:
            return self._make_union(is_type, querysets)

    def find_default_queryset(self, unbound_type: UnboundType) -> MypyType:
        is_type, concrete = self._analyze_first_type_arg(unbound_type, expand=False)
        if concrete is None:
            return unbound_type

        try:
            querysets = tuple(self.store.realise_querysets(UnionType(concrete), self.lookup_info))
        except _store.RestartDmypy as err:
            self.api.fail(f"You probably need to restart dmypy: {err}", unbound_type)
            return AnyType(TypeOfAny.from_error)
        except _store.UnionMustBeOfTypes:
            self.api.fail("Union must be of instances of models", unbound_type)
            return unbound_type
        else:
            return self._make_union(is_type, querysets)

    def lookup_info(self, fullname: str) -> TypeInfo | None:
        instance = self.sem_api.named_type_or_none(fullname)
        if instance:
            return instance.type

        return self.store._plugin_lookup_info(fullname)
