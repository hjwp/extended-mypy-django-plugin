from extended_mypy_django_plugin_test_driver import OutputBuilder, Scenario


class TestConcreteAnnotations:
    def test_simple_annotation(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet
                from typing import cast, TypeGuard

                from myapp.models import Parent, Child1

                models: Concrete[Parent]
                reveal_type(models)

                qs: ConcreteQuerySet[Parent]
                reveal_type(qs)

                def check_cls_with_type_guard(cls: type[Parent]) -> TypeGuard[type[Concrete[Parent]]]:
                    return True

                def check_instance_with_type_guard(cls: Parent) -> TypeGuard[Concrete[Parent]]:
                    return True

                cls: type[Parent] = Child1
                assert check_cls_with_type_guard(cls)
                reveal_type(cls)

                instance: Parent = cast(Child1, None)
                assert check_instance_with_type_guard(instance)
                reveal_type(instance)
                """,
            )

            (
                expected.on("main.py")
                .add_revealed_type(
                    7,
                    "Union[myapp.models.Child1, myapp.models.Child2, myapp.models.Child3, myapp2.models.ChildOther]",
                )
                .add_revealed_type(
                    10,
                    "Union[django.db.models.query.QuerySet[myapp.models.Child1, myapp.models.Child1], myapp.models.Child2QuerySet, django.db.models.query.QuerySet[myapp.models.Child3, myapp.models.Child3], django.db.models.query.QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]]",
                )
                .add_revealed_type(
                    20,
                    "Union[type[myapp.models.Child1], type[myapp.models.Child2], type[myapp.models.Child3], type[myapp2.models.ChildOther]]",
                )
                .add_revealed_type(
                    24,
                    "Union[myapp.models.Child1, myapp.models.Child2, myapp.models.Child3, myapp2.models.ChildOther]",
                )
            )

    def test_sees_apps_removed_when_they_still_exist_but_no_longer_installed(
        self, scenario: Scenario
    ) -> None:
        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from myapp.models import Parent

                models: Concrete[Parent]
                reveal_type(models)

                qs: ConcreteQuerySet[Parent]
                reveal_type(qs)
                """,
            )

            (
                expected.on("main.py")
                .add_revealed_type(
                    6,
                    "Union[myapp.models.Child1, myapp.models.Child2, myapp.models.Child3, myapp2.models.ChildOther]",
                )
                .add_revealed_type(
                    9,
                    "Union[django.db.models.query.QuerySet[myapp.models.Child1, myapp.models.Child1], myapp.models.Child2QuerySet, django.db.models.query.QuerySet[myapp.models.Child3, myapp.models.Child3], django.db.models.query.QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]]",
                )
            )

        # Now let's remove myapp2 from the installed_apps and see that the daemon restarts and myapp2 is removed from the revealed types

        @scenario.run_and_check_mypy_after(installed_apps=["myapp"])
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .remove_from_revealed_type(
                    6,
                    ", myapp2.models.ChildOther",
                )
                .remove_from_revealed_type(
                    9,
                    ", django.db.models.query.QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]",
                )
            )

    def test_does_not_see_apps_that_exist_but_are_not_installed(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after(
            installed_apps=["myapp"], copied_apps=["myapp", "myapp2"]
        )
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from myapp.models import Parent

                model: Concrete[Parent]
                model.concrete_from_myapp

                qs: ConcreteQuerySet[Parent]
                qs.values("concrete_from_myapp")
                """,
            )

        # And after installing the app means the types expand

        @scenario.run_and_check_mypy_after(installed_apps=["myapp", "myapp2"])
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .add_error(
                    6,
                    "union-attr",
                    'Item "ChildOther" of "Child1 | Child2 | Child3 | ChildOther" has no attribute "concrete_from_myapp"',
                )
                .add_error(
                    9,
                    "misc",
                    "Cannot resolve keyword 'concrete_from_myapp' into field. Choices are: concrete_from_myapp2, id, one, two",
                )
            )

    def test_sees_models_when_they_are_added_and_installed(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after(installed_apps=["myapp"])
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from myapp.models import Parent

                models: Concrete[Parent]
                reveal_type(models)

                qs: ConcreteQuerySet[Parent]
                qs.values("concrete_from_myapp")
                """,
            )

            (
                expected.on("main.py").add_revealed_type(
                    6, "Union[myapp.models.Child1, myapp.models.Child2, myapp.models.Child3]"
                )
            )

        # and the models become available after being installed

        @scenario.run_and_check_mypy_after(installed_apps=["myapp", "myapp2"])
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .change_revealed_type(
                    6,
                    "Union[myapp.models.Child1, myapp.models.Child2, myapp.models.Child3, myapp2.models.ChildOther]",
                )
                .add_error(
                    9,
                    "misc",
                    "Cannot resolve keyword 'concrete_from_myapp' into field. Choices are: concrete_from_myapp2, id, one, two",
                )
            )

        # And same output if nothing changes

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_not_restart()

    def test_sees_new_models(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from myapp.models import Parent

                models: Concrete[Parent]
                models.two

                qs: ConcreteQuerySet[Parent]
                qs.values("two")
                """,
            )

        # And if we add some more models

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.append_to_file(
                "myapp2/models.py",
                """
                class Another(Parent):
                    pass
                """,
            )

            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .add_error(
                    6,
                    "union-attr",
                    'Item "Another" of "Child1 | Child2 | Child3 | Another | ChildOther" has no attribute "two"',
                )
                .add_error(
                    9, "misc", "Cannot resolve keyword 'two' into field. Choices are: id, one"
                )
            )

        # And the new model remains after a rerun

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_not_restart()

    def test_sees_changes_in_custom_querysets_within_app(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after(installed_apps=["leader", "follower1"])
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from leader.models import Leader

                models: Concrete[Leader]
                reveal_type(models)

                qs: ConcreteQuerySet[Leader]
                reveal_type(qs)
                qs.good_ones().values("nup")
                """,
            )

            (
                expected.on("main.py")
                .add_revealed_type(6, "Union[follower1.models.follower1.Follower1]")
                .add_revealed_type(9, "Union[follower1.models.follower1.Follower1QuerySet]")
                .add_error(
                    10,
                    "misc",
                    "Cannot resolve keyword 'nup' into field. Choices are: from_follower1, good, id",
                )
            )

        # And then add another model

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.update_file(
                "follower1/models/__init__.py",
                """
                from .follower1 import Follower1
                from .follower2 import Follower2

                __all__ = ["Follower1", "Follower2"]
                """,
            )

            scenario.update_file(
                "follower1/models/follower2.py",
                """
                from django.db import models
                from leader.models import Leader

                class Follower2QuerySet(models.QuerySet["Follower2"]):
                    def good_ones(self) -> "Follower2QuerySet":
                        return self.filter(good=True)

                Follower2Manager = models.Manager.from_queryset(Follower2QuerySet)

                class Follower2(Leader):
                    good = models.BooleanField()

                    objects = Follower2Manager()
                """,
            )

            expected.clear()

            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .add_revealed_type(
                    6,
                    "Union[follower1.models.follower1.Follower1, follower1.models.follower2.Follower2]",
                )
                .add_revealed_type(
                    9,
                    "Union[follower1.models.follower1.Follower1QuerySet, follower1.models.follower2.Follower2QuerySet]",
                )
                .add_error(
                    10,
                    "misc",
                    "Cannot resolve keyword 'nup' into field. Choices are: from_follower1, good, id",
                )
                .add_error(
                    10, "misc", "Cannot resolve keyword 'nup' into field. Choices are: good, id"
                )
            )

        # And same output, no daemon restart on no change

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_not_restart()

        # And removing the method from the queryset

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.update_file(
                "follower1/models/follower2.py",
                """
                from django.db import models
                from leader.models import Leader

                class Follower2QuerySet(models.QuerySet["Follower2"]):
                    pass

                Follower2Manager = models.Manager.from_queryset(Follower2QuerySet)

                class Follower2(Leader):
                    good = models.BooleanField()

                    objects = Follower2Manager()
                """,
            )

            (
                expected.on("main.py")
                .remove_errors(10)
                .add_error(
                    10,
                    "union-attr",
                    'Item "Follower2QuerySet" of "Follower1QuerySet | Follower2QuerySet" has no attribute "good_ones"',
                )
                .add_error(
                    10,
                    "misc",
                    "Cannot resolve keyword 'nup' into field. Choices are: from_follower1, good, id",
                )
            )

    def test_sees_changes_in_custom_querysets_in_new_apps(self, scenario: Scenario) -> None:
        @scenario.run_and_check_mypy_after(installed_apps=["leader", "follower1"])
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                from leader.models import Leader

                models: Concrete[Leader]
                reveal_type(models)

                qs: ConcreteQuerySet[Leader]
                reveal_type(qs)
                qs.good_ones().values("nup")
                """,
            )

            (
                expected.on("main.py")
                .add_revealed_type(6, "Union[follower1.models.follower1.Follower1]")
                .add_revealed_type(9, "Union[follower1.models.follower1.Follower1QuerySet]")
                .add_error(
                    10,
                    "misc",
                    "Cannot resolve keyword 'nup' into field. Choices are: from_follower1, good, id",
                )
            )

        # Let's then add a new app with new models

        @scenario.run_and_check_mypy_after(installed_apps=["leader", "follower1", "follower2"])
        def _(expected: OutputBuilder) -> None:
            scenario.update_file("follower2/__init__.py", "")

            scenario.update_file(
                "follower2/apps.py",
                """
                from django.apps import AppConfig
                class Config(AppConfig):
                    default_auto_field = "django.db.models.BigAutoField"
                    name = "follower2"
                """,
            )

            scenario.update_file(
                "follower2/models.py",
                """
                from django.db import models
                from leader.models import Leader

                class Follower2QuerySet(models.QuerySet["Follower2"]):
                    def good_ones(self) -> "Follower2QuerySet":
                        return self.filter(good=True)

                Follower2Manager = models.Manager.from_queryset(Follower2QuerySet)

                class Follower2(Leader):
                    good = models.BooleanField()

                    objects = Follower2Manager()
                """,
            )

            expected.daemon_should_restart()

            (
                expected.on("main.py")
                .change_revealed_type(
                    6, "Union[follower1.models.follower1.Follower1, follower2.models.Follower2]"
                )
                .change_revealed_type(
                    9,
                    "Union[follower1.models.follower1.Follower1QuerySet, follower2.models.Follower2QuerySet]",
                )
                .add_error(
                    10, "misc", "Cannot resolve keyword 'nup' into field. Choices are: good, id"
                )
            )

        # And everything stays the same on rerun

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            expected.daemon_should_not_restart()

        # And it sees where custom queryset gets queryset manager removed

        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.update_file(
                "follower2/models.py",
                """
                from django.db import models
                from leader.models import Leader

                class Follower2QuerySet(models.QuerySet["Follower2"]):
                    pass

                Follower2Manager = models.Manager.from_queryset(Follower2QuerySet)

                class Follower2(Leader):
                    good = models.BooleanField()

                    objects = Follower2Manager()
                """,
            )

            (
                expected.on("main.py")
                .remove_errors(10)
                .add_error(
                    10,
                    "union-attr",
                    'Item "Follower2QuerySet" of "Follower1QuerySet | Follower2QuerySet" has no attribute "good_ones"',
                )
                .add_error(
                    10,
                    "misc",
                    "Cannot resolve keyword 'nup' into field. Choices are: from_follower1, good, id",
                )
            )
