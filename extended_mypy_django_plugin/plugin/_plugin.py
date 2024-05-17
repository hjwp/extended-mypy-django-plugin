import enum
import sys
from typing import Generic

from mypy.checker import TypeChecker
from mypy.modulefinder import mypy_path
from mypy.nodes import MypyFile, TypeInfo
from mypy.options import Options
from mypy.plugin import (
    AnalyzeTypeContext,
    AttributeContext,
    DynamicClassDefContext,
    FunctionContext,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import CallableType, Instance
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)
from typing_extensions import assert_never

from . import _config, _dependencies, _hook, _reports, _store, actions


class Hook(
    Generic[_hook.T_Ctx, _hook.T_Ret],
    _hook.Hook["ExtendedMypyStubs", _hook.T_Ctx, _hook.T_Ret],
):
    store: _store.Store

    def extra_init(self) -> None:
        self.store = self.plugin.store


class ExtendedMypyStubs(main.NewSemanalDjangoPlugin):
    """
    The ``ExtendedMypyStubs`` mypy plugin extends the
    ``mypy_django_plugin.main.NewSemanalDjangoPlugin`` found in the active python
    environment.

    It implements the following mypy plugin hooks:

    .. automethod:: get_additional_deps

    .. autoattribute:: get_dynamic_class_hook

    .. autoattribute:: get_type_analyze_hook

    .. autoattribute:: get_function_hook

    .. autoattribute:: get_attribute_hook
    """

    class Annotations(enum.Enum):
        CONCRETE = "extended_mypy_django_plugin.annotations.Concrete"
        CONCRETE_QUERYSET = "extended_mypy_django_plugin.annotations.ConcreteQuerySet"
        DEFAULT_QUERYSET = "extended_mypy_django_plugin.annotations.DefaultQuerySet"

    def __init__(self, options: Options, mypy_version_tuple: tuple[int, int]) -> None:
        super(main.NewSemanalDjangoPlugin, self).__init__(options)
        self.mypy_version_tuple = mypy_version_tuple

        self.plugin_config = _config.Config(options.config_file)
        # Add paths from MYPYPATH env var
        sys.path.extend(mypy_path())
        # Add paths from mypy_path config option
        sys.path.extend(options.mypy_path)

        self.running_in_daemon: bool = "dmypy" in sys.argv[0]
        self.report = _reports.Reports.create(
            installed_apps_script=self.plugin_config.installed_apps_script,
            django_settings_module=self.plugin_config.django_settings_module,
            scratch_path=self.plugin_config.scratch_path,
        )

        self.django_context = DjangoContext(self.plugin_config.django_settings_module)
        self.store = _store.Store(
            get_model_class_by_fullname=self.django_context.get_model_class_by_fullname,
            lookup_info=self._lookup_info,
            django_context_model_modules=self.django_context.model_modules,
            is_installed_model=self._is_installed_model,
            known_concrete_models=self.report.known_concrete_models,
        )

        self.dependencies = _dependencies.Dependencies(
            model_modules=self.store.model_modules,
            report_names_getter=self.report.report_names_getter(
                installed_apps=self.django_context.settings.INSTALLED_APPS,
                model_modules=self.store.model_modules,
                get_model_related_fields=self.django_context.get_model_related_fields,
                get_field_related_model_cls=self.django_context.get_field_related_model_cls,
            ),
        )

    def _is_installed_model(self, instance: Instance) -> bool:
        return self.dependencies.is_model_known(instance.type.fullname)

    def _lookup_info(self, fullname: str) -> TypeInfo | None:
        sym = self.lookup_fully_qualified(fullname)
        if sym and isinstance(sym.node, TypeInfo):
            return sym.node
        else:
            return None

    def determine_plugin_version(self) -> int:
        """
        Used to set `__version__' where the plugin is defined.

        This lets us tell dmypy to restart itself as necessary.
        """
        if not self.running_in_daemon:
            return 0
        else:
            return self.report.determine_version_hash()

    def get_additional_deps(self, file: MypyFile) -> list[tuple[int, str, int]]:
        """
        Ensure that models are re-analyzed if any other models that depend on
        them change.

        We use a generated "report" to re-analyze a file if a new dependency
        is discovered after this file has been processed.
        """
        results = self.dependencies.for_file(
            file.fullname, imports=file.imports, super_deps=super().get_additional_deps(file)
        )
        return results

    @_hook.hook
    class get_dynamic_class_hook(Hook[DynamicClassDefContext, None]):
        """
        This is used to find ``Concrete.type_var`` and turn that into a ``TypeVar``
        representing each Concrete class of the abstract model provided.

        So say we find::

            T_Child = Concrete.type_var("T_Child", Parent)

        Then we turn that into::

            T_Child = TypeVar("T_Child", Child1, Child2, Child3)
        """

        def choose(self) -> bool:
            class_name, _, method_name = self.fullname.rpartition(".")
            if method_name == "type_var":
                info = self.plugin._get_typeinfo_or_none(class_name)
                if info and info.has_base(ExtendedMypyStubs.Annotations.CONCRETE.value):
                    return True

            return False

        def run(self, ctx: DynamicClassDefContext) -> None:
            assert isinstance(ctx.api, SemanticAnalyzer)

            sem_analyzing = actions.SemAnalyzing(self.store, api=ctx.api)

            return sem_analyzing.transform_type_var_classmethod(ctx)

    @_hook.hook
    class get_type_analyze_hook(Hook[AnalyzeTypeContext, MypyType]):
        """
        Resolve classes annotated with ``Concrete``, ``ConcreteQuerySet`` and
        ``DefaultQuerySet``.
        """

        def choose(self) -> bool:
            return any(
                member.value == self.fullname
                for member in ExtendedMypyStubs.Annotations.__members__.values()
            )

        def run(self, ctx: AnalyzeTypeContext) -> MypyType:
            assert isinstance(ctx.api, TypeAnalyser)
            assert isinstance(ctx.api.api, SemanticAnalyzer)

            Known = ExtendedMypyStubs.Annotations
            name = Known(self.fullname)

            type_analyzer = actions.TypeAnalyzing(self.store, api=ctx.api, sem_api=ctx.api.api)

            if name is Known.CONCRETE:
                method = type_analyzer.find_concrete_models

            elif name is Known.CONCRETE_QUERYSET:
                method = type_analyzer.find_concrete_querysets

            elif name is Known.DEFAULT_QUERYSET:
                method = type_analyzer.find_default_queryset
            else:
                assert_never(name)

            return method(unbound_type=ctx.type)

    @_hook.hook
    class get_function_hook(Hook[FunctionContext, MypyType]):
        """
        Find functions that return a ``DefaultQuerySet`` annotation and resolve
        the annotation.
        """

        def choose(self) -> bool:
            sym = self.plugin.lookup_fully_qualified(self.fullname)
            if not sym or not sym.node:
                return False

            call = getattr(sym.node, "type", None)
            if not isinstance(call, CallableType):
                return False

            return call.is_generic()

        def run(self, ctx: FunctionContext) -> MypyType:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)

            return type_checking.modify_default_queryset_return_type(
                ctx,
                super_hook=self.super_hook,
                desired_annotation_fullname=ExtendedMypyStubs.Annotations.DEFAULT_QUERYSET.value,
            )

    @_hook.hook
    class get_attribute_hook(Hook[AttributeContext, MypyType]):
        """
        An implementation of the change found in
        https://github.com/typeddjango/django-stubs/pull/2027
        """

        def choose(self) -> bool:
            return self.super_hook is resolve_manager_method

        def run(self, ctx: AttributeContext) -> MypyType:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)

            return type_checking.extended_get_attribute_resolve_manager_method(
                ctx, resolve_manager_method_from_instance=resolve_manager_method_from_instance
            )
