import dataclasses
from collections.abc import Callable, Sequence
from typing import Any, ClassVar, Protocol

from django.db.models import Manager
from mypy.checker import TypeChecker
from mypy.nodes import (
    SymbolNode,
    SymbolTableNode,
    TypeInfo,
    Var,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import AnyType, Instance, ProperType, TypeOfAny
from mypy_django_plugin.django.context import DjangoContext

from . import _fullnames, _helpers


class FailFunction(Protocol):
    def __call__(self, reason: str) -> None: ...


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
                if not _helpers.is_abstract_model(child_sym.node):
                    reviewed.append(child_sym.node.fullname)

        if reviewed != self.children:
            self.children.clear()
            self.children.extend(reviewed)

    def get_dynamic_manager(
        self, api: TypeChecker | SemanticAnalyzer, fullname: str, manager: "Manager[Any]"
    ) -> TypeInfo | None:
        base_manager_fullname = _helpers.get_class_fullname(manager.__class__.__bases__[0])
        base_manager_info = _helpers.lookup_fully_qualified_typeinfo(api, base_manager_fullname)

        generated_managers: dict[str, str]
        if base_manager_info is None or "from_queryset_managers" not in base_manager_info.metadata:
            generated_managers = {}
        else:
            generated_managers = base_manager_info.metadata["from_queryset_managers"]

        generated_manager_name: str | None = generated_managers.get(fullname)
        if generated_manager_name is None:
            return None

        return _helpers.lookup_fully_qualified_typeinfo(api, generated_manager_name)

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
            manager_fullname = _helpers.get_class_fullname(manager.__class__)
            sem_api: SemanticAnalyzer | TypeChecker
            if isinstance(api, TypeAnalyser):
                assert isinstance(api.api, SemanticAnalyzer)
                sem_api = api.api
            else:
                sem_api = api

            manager_info = self.get_dynamic_manager(sem_api, manager_fullname, manager)

        if manager_info is None:
            found = self._lookup_fully_qualified(_fullnames.QUERYSET_CLASS_FULLNAME)
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

        metadata = _helpers.get_django_metadata(manager_info)
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


class Store:
    make_concrete_children: ClassVar[type[ConcreteChildren]] = ConcreteChildren

    def __init__(
        self,
        lookup_fully_qualified: Callable[[str], SymbolTableNode | None],
        django_context: DjangoContext,
    ) -> None:
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
        return ConcreteChildren(
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
                m.fullname == _fullnames.MODEL_CLASS_FULLNAME for m in sym.node.mro
            ) and not _helpers.is_abstract_model(sym.node):
                for typ in sym.node.mro[1:-2]:
                    if typ.fullname != sym.node.fullname and _helpers.is_abstract_model(typ):
                        self.concrete_for(typ).add_child(sym.node.fullname)

        return None
