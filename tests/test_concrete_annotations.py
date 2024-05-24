from extended_mypy_django_plugin_test_driver import OutputBuilder, Scenario


class TestConcreteAnnotations:
    def test_simple_annotation(self, scenario: Scenario) -> None:
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
                    "Union[django.db.models.query._QuerySet[myapp.models.Child1, myapp.models.Child1], myapp.models.Child2QuerySet, django.db.models.query._QuerySet[myapp.models.Child3, myapp.models.Child3], django.db.models.query._QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]]",
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
                    "Union[django.db.models.query._QuerySet[myapp.models.Child1, myapp.models.Child1], myapp.models.Child2QuerySet, django.db.models.query._QuerySet[myapp.models.Child3, myapp.models.Child3], django.db.models.query._QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]]",
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
                    ", django.db.models.query._QuerySet[myapp2.models.ChildOther, myapp2.models.ChildOther]",
                )
            )
