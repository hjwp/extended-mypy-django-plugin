from mypy.nodes import AssignmentStmt, NameExpr, TypeInfo
from mypy.types import LiteralType
from mypy_django_plugin.lib import helpers

get_django_metadata = helpers.get_django_metadata
get_class_fullname = helpers.get_class_fullname
lookup_fully_qualified_typeinfo = helpers.lookup_fully_qualified_typeinfo


if hasattr(helpers, "is_abstract_model"):
    is_abstract_model = helpers.is_abstract_model
else:
    # This code is also copied over to an older version of django-stubs

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
