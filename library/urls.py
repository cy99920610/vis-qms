from django.urls import path
from . import views

app_name = "library"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("browse/", views.browse, name="browse"),
    path("doc/<int:pk>/download/", views.download, name="download"),
    path("assistant/ask/", views.assistant_ask, name="assistant_ask"),
    path("qms-calendar/", views.qms_calendar, name="qms_calendar"),
    path("qms-tasks/", views.qms_tasks, name="qms_tasks"),
    path("qms-tasks/<int:pk>/", views.qms_task_detail, name="qms_task_detail"),
]
