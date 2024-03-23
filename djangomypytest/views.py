from django.http import HttpRequest, HttpResponse, HttpResponseBase

from .exampleapp.models import Child1, Parent
from .mypy_plugin import Concrete

T_Child = Concrete.type_var("T_Child", Parent)


def make_child(child: type[T_Child]) -> T_Child:
    return child.objects.create()


def ones(model: type[Concrete[Parent]]) -> list[str]:
    reveal_type(model.objects)
    return list(model.objects.values_list("one", flat=True))


def index(request: HttpRequest) -> HttpResponseBase:
    made = make_child(Child1)
    reveal_type(made)
    return HttpResponse("Hello there")
