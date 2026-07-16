from django import forms
from django.contrib import admin
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Document, DownloadLog, Section
from .views import build_folder_tree


class FolderTextInput(forms.TextInput):
    """Plain text input with a native HTML5 datalist of existing folder
    paths, so an admin can pick an existing folder (matching the frontend
    library tree) or type a new subfolder path with the right prefix."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "list": "folder-options", "autocomplete": "off",
                 "style": "width:520px", "placeholder": r"01_ISO-9001-QMS\04_QUALITY-PROCEDURES\QP-3"}
        html = super().render(name, value, attrs, renderer)
        folders = Document.objects.exclude(folder="").values_list("folder", flat=True).distinct().order_by("folder")
        options = "".join(f'<option value="{escape(f)}">' for f in folders)
        return mark_safe(f"{html}<datalist id=\"folder-options\">{options}</datalist>")


def render_tree_html(nodes):
    if not nodes:
        return ""
    items = "".join(
        f'<li><code>{escape(n["path"])}</code> <span style="color:#999">({n["count"]})</span>'
        f'{render_tree_html(n["children"])}</li>'
        for n in nodes
    )
    return f'<ul style="margin:2px 0 2px 16px;padding:0 0 0 16px;list-style:disc">{items}</ul>'


class DocumentAdminForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = "__all__"
        widgets = {"folder": FolderTextInput}

    def clean(self):
        cleaned = super().clean()
        section, folder = cleaned.get("section"), cleaned.get("folder")
        if section and folder and not (folder == section or folder.startswith(section + "\\")):
            self.add_error("folder",
                f'Folder must start with the chosen section\'s code ("{section}"), e.g. '
                f'"{section}\\Some Subfolder" — otherwise this document won\'t appear under '
                f'this section in the library tree.')
        return cleaned


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("order", "code", "label", "document_count")
    list_display_links = ("code",)
    list_editable = ("order", "label")
    ordering = ("order", "code")

    @admin.display(description="Documents")
    def document_count(self, obj):
        return Document.objects.filter(section=obj.code).count()

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form = DocumentAdminForm
    list_display = ("code", "title", "revision", "section", "folder", "issue_date", "is_final", "updated_at")
    list_filter = ("section", "is_final")
    search_fields = ("title", "code", "folder", "notes")
    list_editable = ("is_final",)
    date_hierarchy = "issue_date"
    readonly_fields = ("folder_tree_reference",)
    fields = ("title", "code", "revision", "section", "folder_tree_reference", "folder",
              "issue_date", "file", "is_final", "notes", "uploaded_by")

    @admin.display(description="Library structure — copy a path into Folder below")
    def folder_tree_reference(self, obj):
        tree = build_folder_tree(Document.objects.all())
        return mark_safe(
            '<div style="max-height:280px;overflow:auto;border:1px solid var(--border-color,#ccc);'
            f'border-radius:4px;padding:10px 14px;background:rgba(127,127,127,.06)">{render_tree_html(tree)}</div>'
        )

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
