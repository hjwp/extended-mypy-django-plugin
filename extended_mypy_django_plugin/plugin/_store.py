from collections.abc import Iterator, Sequence
from typing import Protocol

from django.db import models
from mypy.nodes import SymbolNode, TypeInfo
from mypy.semanal import SemanticAnalyzer
from mypy.types import Instance, UnionType
from mypy.types import Type as MypyType
from mypy_django_plugin.django.context import DjangoContext

WITH_ANNOTATIONS_FULLNAME = "django_stubs_ext.WithAnnotations"
QUERYSET_CLASS_FULLNAME = "django.db.models.query._QuerySet"
MODEL_CLASS_FULLNAME = "django.db.models.base.Model"


class UnionMustBeOfTypes(Exception):
    pass


class RestartDmypy(Exception):
    pass


class LookupFunction(Protocol):
    def __call__(self, fullname: str) -> TypeInfo | None: ...


class Store:
    def __init__(
        self,
        django_context: DjangoContext,
        lookup_info: LookupFunction,
    ) -> None:
        self._django_context = django_context
        self._metadata: dict[str, dict[str, dict[str, object]]] = {}

        self._registered_for_function_hook: set[str] = set()
        self._plugin_lookup_info = lookup_info

    def sync_metadata(self, info: TypeInfo) -> dict[str, dict[str, object]]:
        if "django_extended" not in info.metadata:
            info.metadata["django_extended"] = {}

        if "concrete_children" not in info.metadata["django_extended"]:
            info.metadata["django_extended"]["concrete_children"] = []

        return info.metadata

    def retrieve_concrete_children_from_metadata(self, parent: TypeInfo) -> list[str]:
        metadata = self.sync_metadata(parent)
        children = metadata["django_extended"]["concrete_children"]
        assert isinstance(children, list)
        return children

    def add_child(self, parent: TypeInfo, child: str) -> None:
        children = self.retrieve_concrete_children_from_metadata(parent)
        if child not in children:
            children.append(child)

    def concrete_children(
        self, parent: TypeInfo, lookup_info: LookupFunction
    ) -> Sequence[TypeInfo]:
        children = self.retrieve_concrete_children_from_metadata(parent)

        ret: list[TypeInfo] = []
        for child in children:
            info = lookup_info(child)
            if info:
                ret.append(info)

        return ret

    def register_for_function_hook(self, node: SymbolNode) -> None:
        assert node.fullname is not None
        self._registered_for_function_hook.add(node.fullname)

    def registered_for_function_hook(self, node: SymbolNode) -> bool:
        return node.fullname in self._registered_for_function_hook

    def fill_out_concrete_children(self, fullname: str, lookup_info: LookupFunction) -> None:
        if not fullname:
            return None

        info = lookup_info(fullname)
        if info and len(info.mro) > 2:
            model = self._django_context.get_model_class_by_fullname(info.fullname)
            if model and not model._meta.abstract:
                for typ in info.mro[1:-2]:
                    parent_model = self._django_context.get_model_class_by_fullname(typ.fullname)
                    if parent_model and parent_model is not model and parent_model._meta.abstract:
                        self.add_child(typ, info.fullname)

        return None

    def get_queryset_fullnames(
        self, type_var: Instance | UnionType, lookup_info: LookupFunction
    ) -> Iterator[tuple[str, TypeInfo]]:
        children: list[TypeInfo] = []
        if isinstance(type_var, UnionType):
            for item in type_var.items:
                if not isinstance(item, Instance):
                    raise UnionMustBeOfTypes()
                children.append(item.type)
        else:
            children.append(type_var.type)

        for child in children:
            yield (
                self.get_dynamic_queryset_fullname(child, lookup_info) or QUERYSET_CLASS_FULLNAME,
                child,
            )

    def get_dynamic_manager(self, model: TypeInfo, lookup_info: LookupFunction) -> TypeInfo | None:
        model_cls = self._django_context.get_model_class_by_fullname(model.fullname)
        assert model_cls is not None
        manager = model_cls._default_manager
        if manager is None:
            return None

        if not isinstance(manager, models.Manager):
            return None

        manager_fullname = manager.__class__.__module__ + "." + manager.__class__.__qualname__
        manager_info = lookup_info(manager_fullname)
        if manager_info is None:
            base_manager_class = manager.__class__.__bases__[0]
            base_manager_fullname = (
                base_manager_class.__module__ + "." + base_manager_class.__qualname__
            )

            base_manager_info = lookup_info(base_manager_fullname)
            if not base_manager_info:
                raise RestartDmypy()

            metadata = self.sync_metadata(base_manager_info)

            generated_managers: dict[str, str]
            if "from_queryset_managers" not in metadata:
                metadata["from_queryset_managers"] = {}
            generated_managers = {
                k: v for k, v in metadata["from_queryset_managers"].items() if isinstance(v, str)
            }

            generated_manager_name: str | None = generated_managers.get(manager_fullname)
            if generated_manager_name is None:
                return None

            manager_info = lookup_info(generated_manager_name)

        return manager_info

    def get_dynamic_queryset_fullname(
        self, model: TypeInfo, lookup_info: LookupFunction
    ) -> str | None:
        dynamic_manager = self.get_dynamic_manager(model, lookup_info)
        if not dynamic_manager:
            return None

        name = self.sync_metadata(dynamic_manager)["django"].get("from_queryset_manager")
        if name is not None and isinstance(name, str):
            return name

        return None

    def realise_querysets(
        self, type_var: Instance | UnionType, lookup_info: LookupFunction
    ) -> Iterator[Instance]:
        querysets = self.get_queryset_fullnames(type_var, lookup_info)
        for fullname, model in querysets:
            queryset = lookup_info(fullname)
            if not queryset:
                raise RestartDmypy()

            if not queryset.is_generic():
                yield Instance(queryset, [])
            else:
                yield Instance(
                    queryset,
                    [Instance(model, []) for _ in range(len(queryset.type_vars))],
                )

    def concrete_children_for(
        self, api: SemanticAnalyzer, parent: TypeInfo, lookup_info: LookupFunction
    ) -> Sequence[MypyType]:
        values: list[MypyType] = []

        concrete_type_infos = self.concrete_children(parent, lookup_info)
        for info in concrete_type_infos:
            instance = api.named_type_or_none(info.fullname)
            if instance:
                values.append(instance)

        return values
