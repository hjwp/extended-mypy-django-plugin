from collections.abc import Callable

from mypy.nodes import (
    SymbolNode,
    SymbolTableNode,
    TypeInfo,
)
from mypy.types import AnyType, Instance, TypeOfAny, UnionType
from mypy.types import Type as MypyType
from mypy_django_plugin.django.context import DjangoContext

from . import _concrete_children, _fullnames, _helpers


class Store:
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

    def concrete_for(self, info: TypeInfo) -> _concrete_children.ConcreteChildren:
        self.sync_metadata(info)
        metadata = self._metadata[info.fullname]
        if "concrete_children" not in metadata:
            metadata["concrete_children"] = []

        children = metadata["concrete_children"]
        assert isinstance(children, list)
        return _concrete_children.ConcreteChildren(
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

    def make_default_querysets(
        self,
        *,
        api: _concrete_children.ApiType,
        type_var: Instance | UnionType,
        fail_function: _concrete_children.FailFunction,
    ) -> MypyType:
        children: list[TypeInfo] = []
        if isinstance(type_var, UnionType):
            for item in type_var.items:
                if not isinstance(item, Instance):
                    fail_function("DefaultQuerySet needs to be given Type or an instance of Types")
                    return AnyType(TypeOfAny.from_error)
                children.append(item.type)
        else:
            children.append(type_var.type)

        concrete = tuple(
            _concrete_children.ConcreteChildren(
                children=[],
                _lookup_fully_qualified=self._lookup_fully_qualified,
                _django_context=self._django_context,
                _fail_function=fail_function,
            ).make_one_queryset(api, info)
            for info in children
        )
        return UnionType(concrete)
