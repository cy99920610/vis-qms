from django.urls import path
from . import views

app_name = "library"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("browse/", views.browse, name="browse"),
    path("doc/<int:pk>/download/", views.download, name="download"),
    path("assistant/ask/", views.assistant_ask, name="assistant_ask"),
]
