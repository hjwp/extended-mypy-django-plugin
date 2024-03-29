from django.http import HttpRequest, HttpResponse, HttpResponseBase

from extended_mypy_django_plugin import Concrete, ConcreteQuerySet, DefaultQuerySet

from .exampleapp.models import Child1, Child2, Parent

T_Child = Concrete.type_var("T_Child", Parent)


def make_child(child: type[T_Child]) -> T_Child:
    return child.objects.create()


def make_any_queryset(child: type[Concrete[Parent]]) -> ConcreteQuerySet[Parent]:
    return child.objects.all()


def make_child1_queryset() -> DefaultQuerySet[Child1]:
    return Child1.objects.all()


def make_child2_queryset() -> DefaultQuerySet[Child2]:
    return Child2.objects.all()


def make_child_typevar_queryset(child: type[T_Child]) -> DefaultQuerySet[T_Child]:
    return child.objects.all()


def ones(model: type[Concrete[Parent]]) -> list[str]:
    # Union[django.db.models.manager.Manager[djangoexample.exampleapp.models.Child1], djangoexample.exampleapp.models.ManagerFromChild2QuerySet[djangoexample.exampleapp.models.Child2], django.db.models.manager.Manager[djangoexample.exampleapp.models.Child3]]
    reveal_type(model.objects)
    return list(model.objects.values_list("one", flat=True))


def index(request: HttpRequest) -> HttpResponseBase:
    made = make_child(Child1)
    # djangoexample.exampleapp.models.Child1
    reveal_type(made)

    any_qs = make_any_queryset(Child1)
    # Union[django.db.models.query._QuerySet[djangoexample.exampleapp.models.Child1], djangoexample.exampleapp.models.Child2QuerySet, django.db.models.query._QuerySet[djangoexample.exampleapp.models.Child3]]
    reveal_type(any_qs)

    qs1 = make_child1_queryset()
    # django.db.models.query._QuerySet[djangoexample.exampleapp.models.Child1]
    reveal_type(qs1)

    qs2 = make_child2_queryset()
    # djangoexample.exampleapp.models.Child2QuerySet
    reveal_type(qs2)
    # djangoexample.exampleapp.models.Child2QuerySet
    reveal_type(qs2.all())
    # djangoexample.exampleapp.models.ManagerFromChild2QuerySet[djangoexample.exampleapp.models.Child2]
    reveal_type(Child2.objects)
    # djangoexample.exampleapp.models.Child2QuerySet[djangoexample.exampleapp.models.Child2]
    reveal_type(Child2.objects.all())

    tvqs1 = make_child_typevar_queryset(Child1)
    # django.db.models.query._QuerySet[djangoexample.exampleapp.models.Child1]
    reveal_type(tvqs1)

    tvqs2 = make_child_typevar_queryset(Child2)
    # djangoexample.exampleapp.models.Child2QuerySet
    reveal_type(tvqs2)

    return HttpResponse("Hello there")
