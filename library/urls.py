from django.urls import path
from . import views

app_name = "library"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("browse/", views.browse, name="browse"),
    path("doc/<int:pk>/download/", views.download, name="download"),
    path("assistant/ask/", views.assistant_ask, name="assistant_ask"),
    path("api/documents/search-for-link/", views.document_search_api, name="document_search_api"),
    path("api/documents/<int:pk>/preview-info/", views.document_preview_info, name="document_preview_info"),
    path("qms-calendar/", views.qms_calendar, name="qms_calendar"),
    path("qms-tasks/", views.qms_tasks, name="qms_tasks"),
    path("qms-tasks/<int:pk>/", views.qms_task_detail, name="qms_task_detail"),
    path("doc-control/", views.doc_control, name="doc_control"),
    path("doc-control/watchdog-api/", views.doc_control_watchdog_api, name="doc_control_watchdog_api"),
    path("doc-control/export/<str:dataset>/<str:fmt>/", views.doc_control_export, name="doc_control_export"),
    path("doc-control/agent/ask/", views.doc_control_agent_ask, name="doc_control_agent_ask"),
]
