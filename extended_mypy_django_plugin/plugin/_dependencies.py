import ast
import hashlib
import importlib.resources
import inspect
import itertools
import os
import pathlib
import sys
import time
import types
from collections import defaultdict
from typing import Protocol

from django.db import models
from mypy.nodes import SymbolTableNode
from mypy_django_plugin.django.context import DjangoContext, temp_environ


class WithDjangoContext(Protocol):
    django_context: DjangoContext
    dependencies: "Dependencies"

    def lookup_fully_qualified(self, fullname: str) -> SymbolTableNode | None: ...


Dep = tuple[int, str, int]
DepList = list[tuple[int, str, int]]


class Dependencies:
    def __init__(self, plugin: WithDjangoContext, project_identifier: str) -> None:
        self.plugin = plugin
        report_mod = "extended_mypy_django_plugin.reports"
        with importlib.resources.as_file(
            importlib.resources.files(report_mod) / f"{project_identifier}.py"
        ) as path:
            self.report_file = pathlib.Path(path)
        self.report_dep = f"{report_mod}.{project_identifier}"
        self.determine_model_deps()

    @property
    def model_modules(self) -> dict[str, dict[str, type[models.Model]]]:
        model_modules = self.plugin.django_context.model_modules
        return model_modules

    def for_file(self, fullname: str, super_deps: DepList) -> DepList:
        deps = list(super_deps)

        changed: bool = True
        while changed:
            self.determine_model_deps(refresh_context=True)
            changed = False

            if fullname in self.model_dependencies:
                for _, dep, _ in deps:
                    try:
                        if (
                            not self.plugin.lookup_fully_qualified(dep)
                            and dep in self.model_modules
                        ):
                            changed = True
                            if dep == fullname:
                                break
                            if dep in sys.modules:
                                del sys.modules[dep]
                            del self.plugin.django_context.model_modules

                            by_label: dict[str, list[type[models.Model]]] = defaultdict(list)

                            from django.apps import apps

                            for known in self.model_modules[dep].values():
                                if known._meta.app_label in apps.all_models:
                                    by_label[known._meta.app_label].append(known)

                            for label, ms in by_label.items():
                                keys = [k for k, v in apps.all_models[label].items() if v in ms]
                                app_models = apps.get_app_config(label).models
                                for k in keys:
                                    del apps.all_models[label][k]
                                    if k in app_models:
                                        del app_models[k]
                    except AssertionError:
                        pass

        for mod in self.model_dependencies.get(fullname, ()):
            if ":" in mod:
                name, line = mod.split(":", 1)
                if not line.isdigit():
                    continue
                new_dep = (10, name, int(line))
            else:
                new_dep = (10, mod, -1)

            if new_dep not in deps:
                deps.append(new_dep)

        return deps

    def refresh_context(self, do_imports: set[str] | None = None) -> dict[str, set[str]]:
        with temp_environ():
            os.environ["DJANGO_SETTINGS_MODULE"] = (
                self.plugin.django_context.django_settings_module
            )

            from django.apps import apps
            from django.conf import settings

            apps.get_swappable_settings_name.cache_clear()  # type: ignore[attr-defined]
            apps.clear_cache()

            self.reset_model_modules(do_imports)

            if not settings.configured:
                settings._setup()  # type: ignore[misc]
            apps.populate(settings.INSTALLED_APPS)

            assert apps.apps_ready, "Apps are not ready"
            assert settings.configured, "Settings are not configured"

        self.plugin.django_context.apps_registry = apps
        self.plugin.django_context.settings = settings

        return self.determine_model_deps(refresh_context=True)

    def reset_model_modules(self, do_imports: set[str] | None = None) -> None:
        if do_imports:
            for mod in do_imports:
                if mod not in sys.modules:
                    try:
                        importlib.import_module(mod)
                    except ModuleNotFoundError:
                        importlib.import_module(".".join(mod.split(".")[:-1]))

        if "model_modules" in self.plugin.django_context.__dict__:
            del self.plugin.django_context.__dict__["model_modules"]

    def determine_model_deps(self, refresh_context: bool = False) -> dict[str, set[str]]:
        all_deps: dict[str, set[str]] = defaultdict(set)
        all_imports: dict[str, set[str]] = {}
        known_models: dict[str, set[str]] = defaultdict(set)
        module_objects: dict[str, types.ModuleType] = {}

        for mod, known in self.model_modules.items():
            for name, cls in known.items():
                if mod not in module_objects:
                    try:
                        mod_obj = inspect.getmodule(cls)
                    except:
                        pass
                    else:
                        if mod_obj and mod_obj.__name__ == mod:
                            module_objects[mod] = mod_obj

                for mro in cls.mro():
                    if mro is cls:
                        continue
                    if mro is models.Model:
                        break

                    if mro.__module__ != mod:
                        all_deps[mod].add(mro.__module__)
                        known_models[mod].add(f"{mro.__module__}.{mro.__qualname__}")
                        if not mro.__module__.startswith("django."):
                            all_deps[mro.__module__].add(mod)
                            known_models[mro.__module__].add(
                                f"{cls.__module__}.{cls.__qualname__}"
                            )

                for field in itertools.chain(
                    # forward relations
                    self.plugin.django_context.get_model_related_fields(cls),
                    # reverse relations - `related_objects` is private API (according to docstring)
                    cls._meta.related_objects,  # type: ignore[attr-defined]
                ):
                    try:
                        related_model_cls = self.plugin.django_context.get_field_related_model_cls(
                            field
                        )
                    except Exception:
                        continue
                    related_model_module = related_model_cls.__module__
                    if related_model_module != mod:
                        all_deps[mod].add(related_model_module)
                        if not related_model_module.startswith("django."):
                            all_deps[related_model_module].add(mod)

        for mod in all_deps:
            imports: set[str] = set()
            try:
                content = ast.parse(inspect.getsource(module_objects[mod]))
            except:
                pass
            else:
                for node in ast.walk(content):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.add(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            module = node.module
                            if module is None:
                                module = ".".join(mod.split(".")[:-1])
                            imports.add(module)
                            imports.add(f"{module}.{alias.name}")
            all_imports[mod] = imports

        current: str = ""
        if self.report_file.exists():
            current = self.report_file.read_text()

        records: dict[str, tuple[int, str, str, str]] = {}
        by_line_number: dict[int, tuple[str, str, str]] = {}
        for i, line in enumerate(current.split("\n")):
            if line.startswith('"') and line.endswith('"'):
                line = line[1:-1]

            if line.count(",") != 2:
                continue

            mod, old_hsh, epoch = line.split(",", 2)
            records[mod] = (i + 1, mod, old_hsh, epoch)
            by_line_number[i + 1] = (mod, old_hsh, epoch)

        greatest_line_number = 0
        if by_line_number:
            greatest_line_number = max(by_line_number.keys())

        need_change: bool = False
        do_imports: set[str] = set()
        for mod, deps in all_deps.items():
            if mod in records:
                line_number, m, old_hsh, epoch = records[mod]
                if m != mod:
                    old_hsh = ""
                    epoch = ""
            else:
                greatest_line_number += 1
                line_number = greatest_line_number
                old_hsh = ""
                epoch = ""

            deps = (
                (all_deps.get(mod) or set())
                .union(known_models.get(mod) or set())
                .union(all_imports.get(mod) or set())
            )
            if not deps:
                new_hsh = ""
            else:
                new_hsh = hashlib.sha1(",".join(sorted(deps)).encode()).hexdigest()
            if old_hsh != new_hsh:
                if mod in self.model_modules or any(mod in deps for _, deps in all_deps.items()):
                    for import_mod in (all_deps.get(mod) or set()).union(
                        all_imports.get(mod) or set()
                    ):
                        do_imports.add(import_mod)
                need_change = True
                epoch = str(time.time_ns())
            deps.add(f"{self.report_dep}:{line_number}")
            by_line_number[line_number] = (mod, new_hsh, epoch)

        if need_change:
            with open(str(self.report_file), "w") as wfile:
                for i in range(len(by_line_number)):
                    found = by_line_number.get(i + 1)
                    if found:
                        mod, hsh, epoch = found
                        wfile.write(f'"{mod},{hsh},{epoch}"\n')
                    else:
                        wfile.write("\n")

            if refresh_context:
                all_deps = self.refresh_context(do_imports)

        self.model_dependencies = all_deps
        return all_deps
