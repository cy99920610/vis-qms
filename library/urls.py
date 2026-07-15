from django.urls import path
from . import views

app_name = "library"
urlpatterns = [
    path("", views.browse, name="browse"),
    path("doc/<int:pk>/download/", views.download, name="download"),
]
