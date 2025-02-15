import enum
import sys
from collections.abc import Callable
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
    FunctionSigContext,
    MethodContext,
    MethodSigContext,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import FunctionLike, Instance
from mypy.types import Type as MypyType
from mypy_django_plugin import main
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.transformers.managers import (
    resolve_manager_method,
    resolve_manager_method_from_instance,
)
from typing_extensions import assert_never

from . import _config, _dependencies, _hook, _known_annotations, _reports, _store, actions


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

    plugin_config: _config.Config

    def __init__(self, options: Options, mypy_version_tuple: tuple[int, int]) -> None:
        super(main.NewSemanalDjangoPlugin, self).__init__(options)
        self.mypy_version_tuple = mypy_version_tuple

        self.plugin_config = _config.Config(options.config_file)
        # Add paths from MYPYPATH env var
        sys.path.extend(mypy_path())
        # Add paths from mypy_path config option
        sys.path.extend(options.mypy_path)

        self.running_in_daemon: bool = "dmypy" in sys.argv[0]

        # Ensure we have a working django context before doing anything
        # So when we try to import things that depend on that, they don't crash us!
        self.django_context = DjangoContext(self.plugin_config.django_settings_module)

        self.report = _reports.Reports.create(
            determine_django_state_script=self.plugin_config.determine_django_state_script,
            django_settings_module=self.plugin_config.django_settings_module,
            scratch_path=self.plugin_config.scratch_path,
        )

        self.store = _store.Store(
            get_model_class_by_fullname=self.django_context.get_model_class_by_fullname,
            lookup_info=self._lookup_info,
            lookup_fully_qualified=self.lookup_fully_qualified,
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

    def determine_plugin_version(self, previous_version: int | None = None) -> int:
        """
        Used to set `__version__' where the plugin is defined.

        This lets us tell dmypy to restart itself as necessary.
        """
        if not self.running_in_daemon:
            return 0
        else:
            return self.report.determine_version_hash(
                self.plugin_config.scratch_path, previous_version
            )

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
        This is used to find special methods on the ``Concrete`` class and do appropriate actions.

        For ``Concrete.type_var`` we turn the result into a ``TypeVar`` that can only be one of
        the concrete descendants of the specified class.

        So say we find::

            T_Child = Concrete.type_var("T_Child", Parent)

        Then we turn that into::

            T_Child = TypeVar("T_Child", Child1, Child2, Child3)
        """

        class KnownConcreteMethods(enum.Enum):
            type_var = "type_var"

        method_name: KnownConcreteMethods

        def choose(self) -> bool:
            class_name, _, method_name = self.fullname.rpartition(".")
            try:
                self.method_name = self.KnownConcreteMethods(method_name)
            except ValueError:
                return False
            else:
                info = self.plugin._get_typeinfo_or_none(class_name)
                return bool(info and info.has_base(_known_annotations.KnownClasses.CONCRETE.value))

        def run(self, ctx: DynamicClassDefContext) -> None:
            assert isinstance(ctx.api, SemanticAnalyzer)

            sem_analyzing = actions.SemAnalyzing(self.store, api=ctx.api)

            if self.method_name is self.KnownConcreteMethods.type_var:
                return sem_analyzing.transform_type_var_classmethod(ctx)
            else:
                assert_never(self.method_name)

    @_hook.hook
    class get_type_analyze_hook(Hook[AnalyzeTypeContext, MypyType]):
        """
        Resolve classes annotated with ``Concrete`` or ``DefaultQuerySet``.
        """

        annotation: _known_annotations.KnownAnnotations

        def choose(self) -> bool:
            try:
                self.annotation = _known_annotations.KnownAnnotations(self.fullname)
            except ValueError:
                return False
            else:
                return True

        def run(self, ctx: AnalyzeTypeContext) -> MypyType:
            assert isinstance(ctx.api, TypeAnalyser)
            assert isinstance(ctx.api.api, SemanticAnalyzer)

            type_analyzer = actions.TypeAnalyzer(self.store, ctx.api, ctx.api.api)
            return type_analyzer.analyze(ctx, self.annotation)

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

    class _get_method_or_function_hook(Hook[MethodContext | FunctionContext, MypyType]):
        runner: Callable[[MethodContext | FunctionContext], MypyType | None]

        def extra_init(self) -> None:
            super().extra_init()
            self.shared_logic = actions.SharedAnnotationHookLogic(
                self.store, fullname=self.fullname
            )

        def choose(self) -> bool:
            if self.shared_logic.choose():
                self.runner = self.shared_logic.run
                return True
            else:
                return False

        def run(self, ctx: FunctionContext | MethodContext) -> MypyType:
            result = self.runner(ctx)
            if result is not None:
                return result

            if self.super_hook is not None:
                return self.super_hook(ctx)

            return ctx.default_return_type

    @_hook.hook
    class get_method_hook(_get_method_or_function_hook):
        pass

    @_hook.hook
    class get_function_hook(_get_method_or_function_hook):
        pass

    class _get_method_or_function_signature_hook(
        Hook[MethodSigContext | FunctionSigContext, FunctionLike]
    ):
        def extra_init(self) -> None:
            super().extra_init()
            self.shared_logic = actions.SharedSignatureHookLogic(
                self.store, fullname=self.fullname
            )

        def choose(self) -> bool:
            return self.shared_logic.choose()

        def run(self, ctx: MethodSigContext | FunctionSigContext) -> FunctionLike:
            result = self.shared_logic.run(ctx)
            if result is not None:
                return ctx.default_signature.copy_modified(ret_type=result)

            if self.super_hook is not None:
                return self.super_hook(ctx)

            return ctx.default_signature

    @_hook.hook
    class get_method_signature_hook(_get_method_or_function_signature_hook):
        pass

    @_hook.hook
    class get_function_signature_hook(_get_method_or_function_signature_hook):
        pass
