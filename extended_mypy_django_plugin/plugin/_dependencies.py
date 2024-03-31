import ast
import hashlib
import importlib.resources
import inspect
import itertools
import pathlib
import sys
import time
import types
from collections import defaultdict
from typing import Protocol

from django.db import models
from mypy.nodes import SymbolTableNode
from mypy_django_plugin.django.context import DjangoContext
from mypy_django_plugin.exceptions import UnregisteredModelError


class WithDjangoContext(Protocol):
    django_context: DjangoContext

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

    def for_file(self, fullname: str, super_deps: DepList) -> DepList:
        deps = list(super_deps)

        if fullname not in self.model_dependencies:
            self.determine_model_deps(refresh_context=True)

        for mod in self.model_dependencies.get(fullname, ()):
            if ":" in mod:
                name, line = mod.split(":", 1)
                if not line.isdigit():
                    continue
                new_dep = (30, name, int(line))
            else:
                new_dep = (30, mod, -1)

            if new_dep not in deps:
                deps.append(new_dep)

        if fullname in self.model_dependencies:
            for _, dep, _ in deps:
                try:
                    if (
                        not self.plugin.lookup_fully_qualified(dep)
                        and dep in self.plugin.django_context.model_modules
                    ):
                        self.refresh_context()
                        break
                except AssertionError:
                    pass

        return deps

    def refresh_context(self) -> None:
        self.plugin.django_context = self.plugin.django_context.__class__(
            self.plugin.django_context.django_settings_module
        )

        # Creating django context adds to sys.path without checking if it's a duplicate
        without_duplicates: list[str] = []
        for p in sys.path:
            if p not in without_duplicates:
                without_duplicates.append(p)

        sys.path = without_duplicates
        self.determine_model_deps()

    def determine_model_deps(self, refresh_context: bool = False) -> None:
        all_deps: dict[str, set[str]] = defaultdict(set)
        all_imports: dict[str, set[str]] = {}
        known_models: dict[str, set[str]] = defaultdict(set)
        module_objects: dict[str, types.ModuleType] = {}

        for mod, known in self.plugin.django_context.model_modules.items():
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
                    except UnregisteredModelError:
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
                            imports.add(f"{node.module}.{alias.name}")
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
                self.refresh_context()

        self.model_dependencies = all_deps
