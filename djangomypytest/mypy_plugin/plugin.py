import dataclasses
from collections.abc import Callable

from mypy.nodes import (
    GDEF,
    AssignmentStmt,
    CallExpr,
    NameExpr,
    StrExpr,
    SymbolTableNode,
    TypeInfo,
    TypeVarExpr,
)
from mypy.plugin import AnalyzeTypeContext, ClassDefContext, DynamicClassDefContext
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import (
    AnyType,
    Instance,
    LiteralType,
    ProperType,
    TypeOfAny,
    UnionType,
    get_proper_type,
)
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.lib import fullnames, helpers


class Metadata:
    @dataclasses.dataclass(frozen=True)
    class ConcreteChildren:
        info: TypeInfo
        children: list[str]
        _lookup_fully_qualified: Callable[[str], SymbolTableNode | None]

        def add_child(self, name: str) -> None:
            if name not in self.children:
                self.children.append(name)

            reviewed: list[str] = []
            for child in self.children:
                child_sym = self._lookup_fully_qualified(child)
                if child_sym and isinstance(child_sym.node, TypeInfo):
                    if not is_abstract_model(child_sym.node):
                        reviewed.append(child_sym.node.fullname)

            if reviewed != self.children:
                self.children.clear()
                self.children.extend(reviewed)

        def instances(self, api: SemanticAnalyzer) -> list[Instance]:
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

    def __init__(self, lookup_fully_qualified: Callable[[str], SymbolTableNode | None]) -> None:
        self._metadata: dict[str, dict[str, object]] = {}
        self._lookup_fully_qualified = lookup_fully_qualified

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
        return self.ConcreteChildren(
            info=info, children=children, _lookup_fully_qualified=self._lookup_fully_qualified
        )

    def fill_out_concrete_children(self, fullname: str) -> None:
        if not fullname:
            return None

        sym = self._lookup_fully_qualified(fullname)
        if sym is not None and isinstance(sym.node, TypeInfo) and len(sym.node.mro) > 2:
            if any(
                m.fullname == fullnames.MODEL_CLASS_FULLNAME for m in sym.node.mro
            ) and not is_abstract_model(sym.node):
                for typ in sym.node.mro[1:-2]:
                    if typ.fullname != sym.node.fullname and is_abstract_model(typ):
                        self.concrete_for(typ).add_child(sym.node.fullname)

        return None

    def find_concrete_models(self, ctx: AnalyzeTypeContext) -> ProperType:
        args = ctx.type.args
        type_arg = ctx.api.analyze_type(args[0])

        if not isinstance(type_arg, Instance):
            return get_proper_type(UnionType(()))

        assert isinstance(ctx.api, TypeAnalyser)
        assert isinstance(ctx.api.api, SemanticAnalyzer)

        if helpers.is_annotated_model_fullname(type_arg.type.fullname):
            # If it's already a generated class, we want to use the original model as a base
            type_arg = type_arg.type.bases[0]

        concrete = self.concrete_for(type_arg.type).instances(ctx.api.api)
        return get_proper_type(UnionType(tuple(concrete)))

    def transform_type_var_classmethod(self, ctx: DynamicClassDefContext) -> None:
        assert isinstance(ctx.call, CallExpr)
        assert isinstance(ctx.call.args[0], StrExpr)
        assert isinstance(ctx.call.args[1], NameExpr)

        name = ctx.call.args[0].value
        parent = ctx.call.args[1].node
        assert isinstance(parent, TypeInfo)
        assert isinstance(ctx.api, SemanticAnalyzer)

        object_type = ctx.api.named_type("builtins.object")
        type_var_expr = TypeVarExpr(
            name=name,
            fullname=f"{ctx.api.cur_mod_id}.{name}",
            values=self.concrete_for(parent).instances(ctx.api),
            upper_bound=object_type,
            default=AnyType(TypeOfAny.from_omitted_generics),
        )
        module = ctx.api.modules[ctx.api.cur_mod_id]
        module.names[name] = SymbolTableNode(GDEF, type_var_expr, plugin_generated=True)
        return None


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    def __init__(self, options: main.Options):
        super().__init__(options)
        self.metadata = Metadata(self.lookup_fully_qualified)

    def get_type_analyze_hook(
        self, fullname: str
    ) -> Callable[[AnalyzeTypeContext], MypyType] | None:
        if fullname == "djangomypytest.mypy_plugin.annotations.Concrete":
            return self.metadata.find_concrete_models
        else:
            return super().get_type_analyze_hook(fullname)

    def get_customize_class_mro_hook(
        self, fullname: str
    ) -> Callable[[ClassDefContext], None] | None:
        self.metadata.fill_out_concrete_children(fullname)
        return super().get_customize_class_mro_hook(fullname)

    def get_dynamic_class_hook(
        self, fullname: str
    ) -> Callable[[DynamicClassDefContext], None] | None:
        class_name, _, method_name = fullname.rpartition(".")
        if method_name == "type_var":
            info = self._get_typeinfo_or_none(class_name)
            if info and info.has_base("djangomypytest.mypy_plugin.annotations.Concrete"):
                return self.metadata.transform_type_var_classmethod
        return super().get_dynamic_class_hook(fullname)


if hasattr(helpers, "is_abstract_model"):
    is_abstract_model = helpers.is_abstract_model
else:

    def is_model_type(info: TypeInfo) -> bool:
        return info.metaclass_type is not None and info.metaclass_type.type.has_base(
            "django.db.models.base.ModelBase"
        )

    def is_abstract_model(model: TypeInfo) -> bool:
        if not is_model_type(model):
            return False

        metadata = helpers.get_django_metadata(model)
        if metadata.get("is_abstract_model") is not None:
            return metadata["is_abstract_model"]

        meta = model.names.get("Meta")
        # Check if 'abstract' is declared in this model's 'class Meta' as
        # 'abstract = True' won't be inherited from a parent model.
        if meta is not None and isinstance(meta.node, TypeInfo) and "abstract" in meta.node.names:
            for stmt in meta.node.defn.defs.body:
                if (
                    # abstract =
                    isinstance(stmt, AssignmentStmt)
                    and len(stmt.lvalues) == 1
                    and isinstance(stmt.lvalues[0], NameExpr)
                    and stmt.lvalues[0].name == "abstract"
                ):
                    # abstract = True (builtins.bool)
                    rhs_is_true = helpers.parse_bool(stmt.rvalue) is True
                    # abstract: Literal[True]
                    is_literal_true = (
                        isinstance(stmt.type, LiteralType) and stmt.type.value is True
                    )
                    metadata["is_abstract_model"] = rhs_is_true or is_literal_true
                    return metadata["is_abstract_model"]

        metadata["is_abstract_model"] = False
        return False


def plugin(version):
    return ExtendedMypyStubs
