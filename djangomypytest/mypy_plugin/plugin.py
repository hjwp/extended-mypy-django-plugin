from collections import defaultdict
from collections.abc import Callable
from functools import partial
from typing import TypedDict, cast

from mypy.nodes import TypeInfo
from mypy.plugin import AnalyzeTypeContext, ClassDefContext, SymbolTableNode
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import Instance, ProperType, UnionType, get_proper_type
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.lib import fullnames, helpers


class DjangoExtendedTypeMetadata(TypedDict):
    concrete_children: list[str]


def get_extended_django_metadata(model_info: TypeInfo) -> DjangoExtendedTypeMetadata:
    return cast(DjangoExtendedTypeMetadata, model_info.metadata.setdefault("django_extended", {}))


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    def __init__(self, options: main.Options):
        super().__init__(options)

        self.concrete_children: dict[str, list[str]] = defaultdict(list)

    def get_type_analyze_hook(
        self, fullname: str
    ) -> Callable[[AnalyzeTypeContext], MypyType] | None:
        if fullname == "djangomypytest.mypy_plugin.annotations.Concrete":
            return partial(
                find_concrete_models,
                django_context=self.django_context,
                concrete_children=self.concrete_children,
            )
        else:
            return super().get_type_analyze_hook(fullname)

    def get_customize_class_mro_hook(
        self, fullname: str
    ) -> Callable[[ClassDefContext], None] | None:
        if fullname:
            _fill_out_concrete_children(
                fullname, self.concrete_children, self.lookup_fully_qualified
            )
        return super().get_customize_class_mro_hook(fullname)


def _fill_out_concrete_children(
    fullname: str,
    concrete_children: dict[str, list[str]],
    lookup_fully_qualified: Callable[[str], SymbolTableNode | None],
) -> None:
    sym = lookup_fully_qualified(fullname)
    if sym is not None and isinstance(sym.node, TypeInfo) and len(sym.node.mro) > 2:
        if any(
            m.fullname == fullnames.MODEL_CLASS_FULLNAME for m in sym.node.mro
        ) and not helpers.is_abstract_model(sym.node):
            for typ in sym.node.mro[1:-2]:
                if typ.fullname != sym.node.fullname and helpers.is_abstract_model(typ):
                    meta = get_extended_django_metadata(typ)
                    if "concrete_children" not in meta:
                        meta["concrete_children"] = concrete_children[typ.fullname]

                    if sym.node.fullname not in meta["concrete_children"]:
                        meta["concrete_children"].append(sym.node.fullname)

                    reviewed: list[str] = []
                    for child in meta["concrete_children"]:
                        child_sym = lookup_fully_qualified(child)
                        if child_sym and isinstance(child_sym.node, TypeInfo):
                            if not helpers.is_abstract_model(child_sym.node):
                                reviewed.append(child_sym.node.fullname)

                    if reviewed != meta["concrete_children"]:
                        meta["concrete_children"].clear()
                        meta["concrete_children"].extend(reviewed)


def find_concrete_models(
    ctx: AnalyzeTypeContext, django_context: DjangoContext, concrete_children: dict[str, list[str]]
) -> ProperType:
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
    if "concrete_children" not in meta:
        meta["concrete_children"] = concrete_children[type_arg.type.fullname]

    concrete: list[Instance] = []
    reviewed: list[str] = []
    for name in meta["concrete_children"]:
        try:
            nxt = ctx.api.api.named_type(name)
        except AssertionError:
            pass
        else:
            concrete.append(nxt)
            reviewed.append(name)

    if meta["concrete_children"] != reviewed:
        meta["concrete_children"].clear()
        meta["concrete_children"].extend(reviewed)

    return get_proper_type(UnionType(tuple(concrete)))


def plugin(version):
    return ExtendedMypyStubs
