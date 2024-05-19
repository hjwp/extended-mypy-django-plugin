from mypy.nodes import Import, ImportBase, ImportFrom

from ._reports import ModelModules, ReportNamesGetter

Dep = tuple[int, str, int]
DepList = list[tuple[int, str, int]]


class Dependencies:
    """
    This object is used to determine if a model is known, and also what
    dependencies to return for a file for the get_additional_deps hook
    """

    def __init__(
        self, model_modules: ModelModules, report_names_getter: ReportNamesGetter
    ) -> None:
        self._model_modules = model_modules
        self._report_names_getter = report_names_getter

    def is_model_known(self, fullname: str) -> bool:
        for mod, known in self._model_modules.items():
            for cls in known.values():
                if fullname == f"{cls.__module__}.{cls.__qualname__}":
                    return True

        return False

    def for_file(self, fullname: str, imports: list[ImportBase], super_deps: DepList) -> DepList:
        deps = list(super_deps)

        if fullname.startswith("django."):
            return deps

        for imp in imports:
            found: set[str] = set()
            if isinstance(imp, ImportFrom):
                for name in imp.names:
                    found.add(f"{imp.id}.{name[0]}")
            elif isinstance(imp, Import):
                for name in imp.ids:
                    found.add(name[0])

            for full in found:
                for mod in self._model_modules:
                    if full == fullname:
                        continue

                    if full == mod or full.startswith(f"{mod}."):
                        new_dep = (10, mod, -1)
                        if new_dep not in deps:
                            deps.append(new_dep)
                        break

        for report in self._report_names_getter(fullname, {m for _, m, _ in deps}):
            new_dep = (10, report, -1)

            if new_dep not in deps:
                deps.append(new_dep)

        return deps
