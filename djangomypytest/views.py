from django.http import HttpRequest, HttpResponse, HttpResponseBase

from .exampleapp.models import Child1, Child2, Parent
from .mypy_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

T_Child = Concrete.type_var("T_Child", Parent)


def make_child(child: type[T_Child]) -> T_Child:
    return child.objects.create()


def make_any_queryset(child: type[Concrete[Parent]]) -> ConcreteQuerySet[Parent]:
    return child.objects.all()


def make_child1_queryset() -> DefaultQuerySet[Child1]:
    return Child1.objects.all()


def make_child2_queryset() -> DefaultQuerySet[Child2]:
    return Child2.objects.all()


def ones(model: type[Concrete[Parent]]) -> list[str]:
    reveal_type(model.objects)
    return list(model.objects.values_list("one", flat=True))


def index(request: HttpRequest) -> HttpResponseBase:
    made = make_child(Child1)
    reveal_type(made)

    any_qs = make_any_queryset(Child1)
    reveal_type(any_qs)

    qs1 = make_child1_queryset()
    reveal_type(qs1)

    qs2 = make_child2_queryset()
    reveal_type(qs2)
    reveal_type(qs2.all())
    reveal_type(Child2.objects)
    reveal_type(Child2.objects.all())

    return HttpResponse("Hello there")
