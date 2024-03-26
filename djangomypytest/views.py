from django.http import HttpRequest, HttpResponse, HttpResponseBase


def index(request: HttpRequest) -> HttpResponseBase:
    return HttpResponse("Hello there")
