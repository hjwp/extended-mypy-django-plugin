from django.http import HttpResponse

from .exampleapp.models import Parent
from .mypy_plugin import Concrete


def ones(model: type[Concrete[Parent]]) -> list[str]:
    reveal_type(model.objects)
    return list(model.objects.values_list("one", flat=True))


def index(request):
    return HttpResponse("Hello there")
