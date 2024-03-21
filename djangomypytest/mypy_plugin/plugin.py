from collections.abc import Callable
from functools import partial
from typing import TypedDict, cast

from mypy.nodes import TypeInfo
from mypy.plugin import AnalyzeTypeContext, ClassDefContext
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import Instance, ProperType, UnionType, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.lib import helpers


class DjangoExtendedTypeMetadata(TypedDict):
    concrete_children: list[str]


def get_extended_django_metadata(model_info: TypeInfo) -> DjangoExtendedTypeMetadata:
    meta = cast(DjangoExtendedTypeMetadata, model_info.metadata.setdefault("django_extended", {}))
    if "concrete_children" not in meta:
        meta["concrete_children"] = []
    return meta


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    def __init__(self, options: main.Options):
        super().__init__(options)

        self._considered_for_concrete_models: set[str] = set()

    def get_type_analyze_hook(
        self, fullname: str
    ) -> Callable[[AnalyzeTypeContext], MypyType] | None:
        if fullname == "djangomypytest.mypy_plugin.annotations.Concrete":
            return partial(find_concrete_models, django_context=self.django_context)
        else:
            return super().get_type_analyze_hook(fullname)

    def get_customize_class_mro_hook(
        self, fullname: str
    ) -> Callable[[ClassDefContext], None] | None:
        if fullname and fullname not in self._considered_for_concrete_models:
            self._considered_for_concrete_models.add(fullname)
            sym = self.lookup_fully_qualified(fullname)
            if sym is not None and isinstance(sym.node, TypeInfo) and len(sym.node.mro) > 2:
                if not helpers.is_abstract_model(sym.node):
                    for typ in sym.node.mro[1:-2]:
                        if typ.fullname != sym.node.fullname and helpers.is_abstract_model(typ):
                            meta = get_extended_django_metadata(typ)
                            if sym.node.fullname not in meta["concrete_children"]:
                                meta["concrete_children"].append(sym.node.fullname)

                            reviewed: list[str] = []
                            for child in meta["concrete_children"]:
                                child_sym = self.lookup_fully_qualified(child)
                                if child_sym and isinstance(child_sym.node, TypeInfo):
                                    if not helpers.is_abstract_model(child_sym.node):
                                        reviewed.append(child_sym.node.fullname)

                            if reviewed != meta["concrete_children"]:
                                meta["concrete_children"].clear()
                                meta["concrete_children"].extend(reviewed)

        return super().get_customize_class_mro_hook(fullname)


def find_concrete_models(ctx: AnalyzeTypeContext, django_context: DjangoContext) -> ProperType:
    args = ctx.type.args
    type_arg = ctx.api.analyze_type(args[0])

    if not isinstance(type_arg, Instance):
        return get_proper_type(UnionType(()))

    assert isinstance(ctx.api, TypeAnalyser)
    assert isinstance(ctx.api.api, SemanticAnalyzer)

    if helpers.is_annotated_model_fullname(type_arg.type.fullname):
        # If it's already a generated class, we want to use the original model as a base
        type_arg = type_arg.type.bases[0]

    meta = get_extended_django_metadata(type_arg.type)

    return get_proper_type(
        UnionType(tuple(ctx.api.named_type(name) for name in meta["concrete_children"]))
    )


def plugin(version):
    return ExtendedMypyStubs
