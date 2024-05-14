from collections.abc import Iterator, Mapping, Sequence
from typing import Protocol

from django.db import models
from mypy.nodes import TypeInfo
from mypy.types import Instance, UnionType, get_proper_type
from mypy.types import Type as MypyType

from ._reports import ModelModules

QUERYSET_CLASS_FULLNAME = "django.db.models.query._QuerySet"
MODEL_CLASS_FULLNAME = "django.db.models.base.Model"


class UnionMustBeOfTypes(Exception):
    pass


class RestartDmypy(Exception):
    pass


class LookupFunction(Protocol):
    def __call__(self, fullname: str) -> TypeInfo | None: ...


class LookupInstanceFunction(Protocol):
    def __call__(self, fullname: str) -> Instance | None: ...


class GetModelClassByFullname(Protocol):
    def __call__(self, fullname: str) -> type[models.Model] | None: ...


class Store:
    """
    The store is used to interrogate the metadata on ``TypeInfo`` objects to determine
    the available concrete models and queryset objects when resolving the annotations
    this plugin provides.

    .. automethod:: retrieve_concrete_children_types

    .. automethod:: associate_model_heirarchy

    .. automethod:: realise_querysets
    """

    def __init__(
        self,
        get_model_class_by_fullname: GetModelClassByFullname,
        lookup_info: LookupFunction,
        django_context_model_modules: Mapping[str, object],
    ) -> None:
        self._get_model_class_by_fullname = get_model_class_by_fullname
        self._plugin_lookup_info = lookup_info
        self._django_context_model_modules = django_context_model_modules
        self.model_modules = self._determine_model_modules()

    def retrieve_concrete_children_types(
        self,
        parent: TypeInfo,
        lookup_info: LookupFunction,
        lookup_instance: LookupInstanceFunction,
    ) -> Sequence[MypyType]:
        """
        Given a ``TypeInfo`` representing some model, return ``MypyType`` objects
        for all the concrete children related to the specified model.
        """
        values: list[MypyType] = []

        concrete_type_infos = self._retrieve_concrete_children_info_from_metadata(
            parent, lookup_info
        )
        for info in concrete_type_infos:
            instance = lookup_instance(info.fullname)
            if instance:
                values.append(instance)

        return values

    def associate_model_heirarchy(self, fullname: str, lookup_info: LookupFunction) -> None:
        """
        For a particular fullname, find all the classes in it's mro (the classes
        it inherits from) and register with those classes that this one is a
        descendant.
        """
        if not fullname:
            return None

        info = lookup_info(fullname)
        if info and len(info.mro) > 2:
            for typ in info.mro[1:-2]:
                self._add_child_to_metadata(typ, info.fullname)

        return None

    def realise_querysets(
        self, type_var: Instance | UnionType, lookup_info: LookupFunction
    ) -> Iterator[Instance]:
        """
        Given either a specific model, or a union of models, return the
        default querysets for those models.
        """
        querysets = self._get_queryset_fullnames(type_var, lookup_info)
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

    def _determine_model_modules(self) -> ModelModules:
        """
        Old version of django-stubs has this as a different datastructure
        """
        result: dict[str, dict[str, type[models.Model]]] = {}
        for k, v in self._django_context_model_modules.items():
            if isinstance(v, dict):
                result[k] = v
            elif isinstance(v, set):
                result[k] = {
                    cls.__name__: cls
                    for cls in v
                    if isinstance(cls, type) and issubclass(cls, models.Model)
                }
        return result

    def _sync_metadata(self, info: TypeInfo) -> dict[str, dict[str, object]]:
        """
        Ensure there is a {"django_extended": {"all_children": []}} in the metadata
        """
        if "django_extended" not in info.metadata:
            info.metadata["django_extended"] = {}

        if not isinstance(info.metadata["django_extended"].get("all_children"), list):
            info.metadata["django_extended"]["all_children"] = []

        return info.metadata

    def _retrieve_all_children_from_metadata(self, parent: TypeInfo) -> list[str]:
        """
        Ensure the ``all_children`` in the metadata is a list and return it
        """
        metadata = self._sync_metadata(parent)
        if not isinstance(children := metadata["django_extended"].get("all_children"), list):
            children = metadata["django_extended"]["all_children"] = []

        return children

    def _retrieve_concrete_children_info_from_metadata(
        self, parent: TypeInfo, lookup_info: LookupFunction
    ) -> Sequence[TypeInfo]:
        """
        For the children recorded in the metadata for this model, return those
        that aren't abstract
        """
        children = self._retrieve_all_children_from_metadata(parent)

        ret: list[TypeInfo] = []
        for child in children:
            info = lookup_info(child)
            if not info:
                continue

            abstract: bool = False
            if "django" not in info.metadata:
                # Old versions of mypy/django-stubs don't have metadata at this point
                model_cls = self._get_model_class_by_fullname(info.fullname)
                abstract = bool(model_cls and model_cls._meta.abstract)
            else:
                abstract = info.metadata.get("django", {}).get("is_abstract_model", False)

            if not abstract:
                ret.append(info)

        return ret

    def _add_child_to_metadata(self, parent: TypeInfo, child: str) -> None:
        """
        Record a child for this ``TypeInfo`` if it's not already recorded
        """
        children = self._retrieve_all_children_from_metadata(parent)
        if child not in children:
            children.append(child)

    def _get_queryset_fullnames(
        self, type_var: Instance | UnionType, lookup_info: LookupFunction
    ) -> Iterator[tuple[str, TypeInfo]]:
        """
        Return the fullnames of the default querysets for the models represented
        by this instance or Union of instances.
        """
        children: list[TypeInfo] = []
        if isinstance(type_var, UnionType):
            for item in type_var.items:
                item = get_proper_type(item)
                if not isinstance(item, Instance):
                    raise UnionMustBeOfTypes()
                children.append(item.type)
        else:
            children.append(type_var.type)

        for child in children:
            yield (
                self._get_dynamic_queryset_fullname(child, lookup_info) or QUERYSET_CLASS_FULLNAME,
                child,
            )

    def _get_dynamic_manager(
        self, model: TypeInfo, lookup_info: LookupFunction
    ) -> TypeInfo | None:
        """
        For some model return a custom manager if one exists
        """
        model_cls = self._get_model_class_by_fullname(model.fullname)
        if model_cls is None:
            raise RestartDmypy()

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

            metadata = self._sync_metadata(base_manager_info)

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

    def _get_dynamic_queryset_fullname(
        self, model: TypeInfo, lookup_info: LookupFunction
    ) -> str | None:
        """
        For this model, return the fullname of the custom queryset for the
        default manager if there is such a custom QuerySet.
        """
        dynamic_manager = self._get_dynamic_manager(model, lookup_info)
        if not dynamic_manager:
            model_cls = self._get_model_class_by_fullname(model.fullname)
            if (
                model_cls
                and hasattr(model_cls, "_default_manager")
                and isinstance(model_cls._default_manager, models.Manager)
                and hasattr(model_cls._default_manager, "_queryset_class")
            ):
                queryset = model_cls._default_manager._queryset_class
                if isinstance(queryset, type) and issubclass(queryset, models.QuerySet):
                    return queryset.__module__ + "." + queryset.__qualname__
            return None

        name = self._sync_metadata(dynamic_manager)["django"].get("from_queryset_manager")
        if name is not None and isinstance(name, str):
            return name

        return None
