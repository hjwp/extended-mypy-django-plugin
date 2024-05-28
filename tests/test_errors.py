import importlib.metadata

from extended_mypy_django_plugin_test_driver import OutputBuilder, Scenario


class TestErrors:
    def test_cant_use_typevar_concrete_annotation_in_function_or_method_typeguard(
        self, scenario: Scenario
    ) -> None:
        @scenario.run_and_check_mypy_after
        def _(expected: OutputBuilder) -> None:
            scenario.make_file(
                "main.py",
                """
                from typing import TypeGuard, TypeVar, cast

                from myapp.models import Child1, Parent

                from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

                T_Parent = Concrete.type_var("T_Parent", Parent)

                def function_with_type_typeguard(
                    cls: type[Parent], expect: type[T_Parent]
                ) -> TypeGuard[type[Concrete[T_Parent]]]:
                    return hasattr(cls, "objects")

                cls1: type[Parent] = Child1
                assert function_with_type_typeguard(cls1, Parent)
                reveal_type(cls1)

                def function_with_instance_typeguard(
                    instance: Parent, expect: type[T_Parent]
                ) -> TypeGuard[Concrete[T_Parent]]:
                    return True

                instance1: Parent = cast(Child1, None)
                assert function_with_instance_typeguard(instance1, Parent)
                reveal_type(instance1)

                class Logic:
                    def method_with_type_typeguard(
                        self, cls: type[Parent], expect: type[T_Parent]
                    ) -> TypeGuard[type[Concrete[T_Parent]]]:
                        return hasattr(cls, "objects")

                    def method_with_instance_typeguard(
                        self, instance: T_Parent, expect: type[T_Parent]
                    ) -> TypeGuard[Concrete[T_Parent]]:
                        return True

                logic = Logic()
                cls2: type[Parent] = Child1
                assert logic.method_with_type_typeguard(cls2, Parent)
                reveal_type(cls2)

                instance2: Parent = cast(Child1, None)
                assert logic.method_with_instance_typeguard(instance2, Parent)
                reveal_type(instance2)
                """,
            )

            out = """
            main:15: error: Can't use a TypeGuard that uses a Concrete Annotation that uses type variables  [misc]
            main:15: error: Value of type variable "T_Parent" of "function_with_type_typeguard" cannot be "Parent"  [type-var]
            main:15: error: Only concrete class can be given where "type[Parent]" is expected  [type-abstract]
            main:16: note: Revealed type is "type[Concrete?[T_Parent?]]"
            main:24: error: Can't use a TypeGuard that uses a Concrete Annotation that uses type variables  [misc]
            main:24: error: Value of type variable "T_Parent" of "function_with_instance_typeguard" cannot be "Parent"  [type-var]
            main:24: error: Only concrete class can be given where "type[Parent]" is expected  [type-abstract]
            main:25: note: Revealed type is "Concrete?[T_Parent?]"
            main:40: error: Can't use a TypeGuard that uses a Concrete Annotation that uses type variables  [misc]
            main:40: error: Value of type variable "T_Parent" of "method_with_type_typeguard" of "Logic" cannot be "Parent"  [type-var]
            main:40: error: Only concrete class can be given where "type[Parent]" is expected  [type-abstract]
            main:41: note: Revealed type is "type[Concrete?[T_Parent?]]"
            main:44: error: Can't use a TypeGuard that uses a Concrete Annotation that uses type variables  [misc]
            main:44: error: Value of type variable "T_Parent" of "method_with_instance_typeguard" of "Logic" cannot be "Parent"  [type-var]
            main:44: error: Only concrete class can be given where "type[Parent]" is expected  [type-abstract]
            main:45: note: Revealed type is "Concrete?[T_Parent?]"
            """

            if importlib.metadata.version("mypy") == "1.4.0":
                out = "\n".join(
                    line
                    for line in out.split("\n")
                    if "Only concrete class can be given" not in line
                )

            expected.from_out(out)
