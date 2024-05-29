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
    FunctionSigContext,
    MethodContext,
    MethodSigContext,
)
from mypy.semanal import SemanticAnalyzer
from mypy.typeanal import TypeAnalyser
from mypy.types import CallableType, FunctionLike
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

    Annotations = _known_annotations.KnownAnnotations

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

    def _is_installed_model(self, fullname: str, concrete_required: bool = False) -> bool:
        return self.dependencies.is_model_known(fullname, concrete_required=concrete_required)

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

    class SharedCallableHookLogic:
        """
        Shared logic for modifying the return type of methods and functions that use a concrete
        annotation with a type variable.

        Note that the signature hook will already raise errors if a concrete annotation is
        used with a type var in a type guard.
        """

        def __init__(self, fullname: str, plugin: "ExtendedMypyStubs") -> None:
            self.plugin = plugin
            self.store = plugin.store
            self.fullname = fullname

        def choose(self) -> bool:
            """
            Choose methods and functions either returning a type guard or have a generic
            return type.

            We determine whether the return type is a concrete annotation or not in the run method.
            """
            if self.fullname.startswith("builtins."):
                return False

            sym = self.plugin.lookup_fully_qualified(self.fullname)
            if not sym or not sym.node:
                return False

            call = getattr(sym.node, "type", None)
            if not isinstance(call, CallableType):
                return False

            return bool(call.type_guard or call.is_generic())

        def run(self, ctx: MethodContext | FunctionContext) -> MypyType | None:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)

            return type_checking.modify_return_type(ctx)

    class _get_method_or_function_hook(Hook[MethodContext | FunctionContext, MypyType]):
        def extra_init(self) -> None:
            self.shared_logic = self.plugin.SharedCallableHookLogic(
                fullname=self.fullname, plugin=self.plugin
            )

        def choose(self) -> bool:
            return self.shared_logic.choose()

        def run(self, ctx: FunctionContext | MethodContext) -> MypyType:
            result = self.shared_logic.run(ctx)
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

    class SharedSignatureHookLogic:
        """
        Shared logic for modifying the signature of methods and functions.

        This is only used to find cases where a concrete annotation with a type var
        is used in a type guard.

        In this situation the mypy plugin system does not provide an opportunity to fully resolve
        the type guard.
        """

        def __init__(self, fullname: str, plugin: "ExtendedMypyStubs") -> None:
            self.plugin = plugin
            self.store = plugin.store
            self.fullname = fullname

        def choose(self) -> bool:
            """
            Only choose methods and functions that are returning a type guard
            """
            if self.fullname.startswith("builtins."):
                return False

            sym = self.plugin.lookup_fully_qualified(self.fullname)
            if not sym or not sym.node:
                return False

            call = getattr(sym.node, "type", None)
            if not isinstance(call, CallableType):
                return False

            return call.type_guard is not None

        def run(self, ctx: MethodSigContext | FunctionSigContext) -> MypyType | None:
            assert isinstance(ctx.api, TypeChecker)

            type_checking = actions.TypeChecking(self.store, api=ctx.api)

            return type_checking.check_typeguard(ctx.context)

    class _get_method_or_function_signature_hook(
        Hook[MethodSigContext | FunctionSigContext, FunctionLike]
    ):
        def extra_init(self) -> None:
            self.shared_logic = self.plugin.SharedSignatureHookLogic(
                fullname=self.fullname, plugin=self.plugin
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
