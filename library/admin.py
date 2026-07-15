from django.contrib import admin
from .models import Document, DownloadLog

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "revision", "section", "folder", "issue_date", "is_final", "updated_at")
    list_filter = ("section", "is_final")
    search_fields = ("title", "code", "folder", "notes")
    list_editable = ("is_final",)
    date_hierarchy = "issue_date"

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "document")
    list_filter = ("user",)
    readonly_fields = ("document", "user", "at")

    def has_add_permission(self, request):
        return False
