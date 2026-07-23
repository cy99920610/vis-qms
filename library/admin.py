import json
from datetime import date

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import (
    Document, DownloadLog, FORMAT_CHOICES, QmsEntity, QMSTask, QMSTaskTemplate,
    RoleAccessProfile, Section, folder_section_mismatch_error, section_choices,
)
from .views import build_folder_tree


class FolderPathWidget(forms.TextInput):
    """Folder field driven by a level-by-level breadcrumb picker: pick the
    section, then pick an existing subfolder from a dropdown, then the next
    subfolder under that, and so on for as many levels as already exist —
    only once you're past the last existing level does it ask you to type a
    new folder name. The raw path field is read-only by default (built for
    you) with a manual-edit escape hatch for power users."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "id": "id_folder", "readonly": "readonly",
                 "style": "width:100%;max-width:640px;background:#f3f3f3;font-family:monospace"}
        input_html = super().render(name, value, attrs, renderer)

        all_sections = list(Section.objects.values_list("code", "label"))
        tree = build_folder_tree(Document.objects.all(), all_sections)
        tree_json = json.dumps(tree).replace("</", "<\\/")

        return mark_safe(f"""
<div style="border:1px solid #ccc;border-radius:6px;padding:10px 12px;max-width:720px;background:#fafafa">
  <div style="font-size:12px;color:#666;margin-bottom:8px">
    Pick a subfolder at each step — the dropdown offers every existing subfolder first;
    choose <b>+ New folder…</b> only once you're past the last existing level.
  </div>
  <div id="folder-builder" style="display:flex;flex-wrap:wrap;gap:4px;align-items:center"></div>
</div>
<div style="margin:8px 0 4px">
  <label style="font-size:12px;color:#666">
    <input type="checkbox" id="folder-manual-toggle" style="width:auto;vertical-align:middle">
    Edit path manually instead
  </label>
</div>
{input_html}
<div style="font-size:11.5px;color:#999;margin-top:2px">Full path (built automatically from your picks above).</div>
<script>
(function () {{
  var TREE = {tree_json};
  var sectionField = document.getElementById("id_section");
  var folderField = document.getElementById("id_folder");
  var levelsBox = document.getElementById("folder-builder");
  var manualToggle = document.getElementById("folder-manual-toggle");
  var SEP = " \\u203a ";

  function splitFolder(folder, sectionCode) {{
    if (!folder) return [];
    var parts = folder.split(/[\\\\/]+/).filter(Boolean);
    if (parts.length && parts[0] === sectionCode) parts = parts.slice(1);
    return parts;
  }}

  function setFolderValue(sectionCode, segments) {{
    folderField.value = [sectionCode].concat(segments).join("\\\\");
  }}

  function clearAfter(levelEl) {{
    while (levelsBox.lastChild && levelsBox.lastChild !== levelEl) {{
      levelsBox.removeChild(levelsBox.lastChild);
    }}
  }}

  function renderLevel(sectionCode, parentNode, pathSoFar, presetSegments) {{
    var level = document.createElement("span");
    level.style.display = "inline-flex";
    level.style.alignItems = "center";
    level.style.gap = "4px";
    if (pathSoFar.length) {{
      var sep = document.createElement("span");
      sep.textContent = SEP;
      sep.style.color = "#999";
      level.appendChild(sep);
    }}
    levelsBox.appendChild(level);

    var children = (parentNode && parentNode.children) || [];
    var select = document.createElement("select");
    select.style.minWidth = "170px";
    select.add(new Option(children.length ? "\\u2014 stop here \\u2014" : "\\u2014 stop here \\u2014", ""));
    children.forEach(function (c) {{
      select.add(new Option(c.name + " (" + c.count + " doc" + (c.count === 1 ? "" : "s") + ")", c.name));
    }});
    select.add(new Option("+ New folder\\u2026", "__new__"));
    level.appendChild(select);

    var textInput = document.createElement("input");
    textInput.type = "text";
    textInput.placeholder = "new folder name";
    textInput.style.display = "none";
    textInput.style.minWidth = "160px";
    level.appendChild(textInput);

    var confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.textContent = "\\u2713";
    confirmBtn.title = "Confirm new folder name";
    confirmBtn.style.display = "none";
    confirmBtn.style.cursor = "pointer";
    level.appendChild(confirmBtn);

    function choose(chosenName, childNode) {{
      clearAfter(level);
      var newPath = pathSoFar.concat(chosenName ? [chosenName] : []);
      setFolderValue(sectionCode, newPath);
      if (chosenName) renderLevel(sectionCode, childNode, newPath, []);
    }}

    function enterNewMode() {{
      select.style.display = "none";
      textInput.style.display = "";
      confirmBtn.style.display = "";
      textInput.value = "";
      textInput.focus();
      clearAfter(level);
      setFolderValue(sectionCode, pathSoFar);
    }}

    select.onchange = function () {{
      if (select.value === "__new__") {{
        enterNewMode();
      }} else {{
        var child = children.find(function (c) {{ return c.name === select.value; }});
        choose(select.value, child || null);
      }}
    }};
    textInput.oninput = function () {{
      clearAfter(level);
      setFolderValue(sectionCode, pathSoFar.concat(textInput.value ? [textInput.value] : []));
    }};
    function commitNew() {{
      if (textInput.value) choose(textInput.value, null);
    }}
    textInput.addEventListener("keydown", function (e) {{
      if (e.key === "Enter") {{ e.preventDefault(); commitNew(); }}
    }});
    confirmBtn.onclick = commitNew;

    var presetName = presetSegments[0];
    if (presetName !== undefined) {{
      var match = children.find(function (c) {{ return c.name === presetName; }});
      if (match) {{
        select.value = presetName;
        renderLevel(sectionCode, match, pathSoFar.concat([presetName]), presetSegments.slice(1));
      }} else {{
        select.value = "__new__";
        select.style.display = "none";
        textInput.style.display = "";
        confirmBtn.style.display = "";
        textInput.value = presetName;
        renderLevel(sectionCode, null, pathSoFar.concat([presetName]), presetSegments.slice(1));
      }}
    }}
  }}

  function bootstrap() {{
    levelsBox.innerHTML = "";
    var sectionCode = sectionField.value;
    var root = TREE.find(function (n) {{ return n.path === sectionCode; }});
    var segments = splitFolder(folderField.value, sectionCode);
    renderLevel(sectionCode, root, [], segments);
  }}

  sectionField.addEventListener("change", function () {{
    folderField.value = sectionField.value;
    bootstrap();
  }});
  manualToggle.addEventListener("change", function () {{
    folderField.readOnly = !manualToggle.checked;
    folderField.style.background = manualToggle.checked ? "#fff" : "#f3f3f3";
  }});

  bootstrap();
}})();
</script>
""")


class DocumentAdminForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = "__all__"
        widgets = {"folder": FolderPathWidget}

    def clean(self):
        cleaned = super().clean()
        error = folder_section_mismatch_error(cleaned.get("section"), cleaned.get("folder"))
        if error:
            self.add_error("folder", error)
        return cleaned


class MoveToFolderForm(forms.Form):
    section = forms.ChoiceField(choices=section_choices, label="Section")
    folder = forms.CharField(label="Folder", widget=FolderPathWidget)

    def clean(self):
        cleaned = super().clean()
        error = folder_section_mismatch_error(cleaned.get("section"), cleaned.get("folder"))
        if error:
            self.add_error("folder", error)
        return cleaned


class RoleAccessProfileForm(forms.ModelForm):
    """The model stores allowed formats as a comma-separated CharField (kept
    portable — no Postgres-only ArrayField); this form presents them as
    checkboxes and converts both ways."""
    allowed_preview_formats = forms.MultipleChoiceField(
        choices=FORMAT_CHOICES, widget=forms.CheckboxSelectMultiple, required=False,
        help_text="Formats this role may preview/open in-browser.")
    allowed_download_formats = forms.MultipleChoiceField(
        choices=FORMAT_CHOICES, widget=forms.CheckboxSelectMultiple, required=False,
        help_text="Formats this role may download.")

    class Meta:
        model = RoleAccessProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["allowed_preview_formats"].initial = self.instance.preview_formats_list()
            self.fields["allowed_download_formats"].initial = self.instance.download_formats_list()

    def clean_allowed_preview_formats(self):
        return ",".join(self.cleaned_data["allowed_preview_formats"])

    def clean_allowed_download_formats(self):
        return ",".join(self.cleaned_data["allowed_download_formats"])


@admin.register(RoleAccessProfile)
class RoleAccessProfileAdmin(admin.ModelAdmin):
    form = RoleAccessProfileForm
    list_display = ("get_role_display_", "allowed_preview_formats", "allowed_download_formats",
                     "can_view_draft_documents", "can_view_source_editable_files",
                     "can_view_obsolete_documents", "can_view_internal_notes",
                     "can_view_external_auditor_package_only")
    fieldsets = (
        (None, {"fields": ("role",)}),
        ("Format access", {"fields": ("allowed_preview_formats", "allowed_download_formats")}),
        ("Visibility", {"fields": ("can_view_draft_documents", "can_view_source_editable_files",
                                    "can_view_obsolete_documents", "can_view_internal_notes",
                                    "can_view_external_auditor_package_only")}),
    )

    def has_add_permission(self, request):
        # Exactly one profile per role — the four rows are seeded by migration
        # and auto-created on first access; admins edit, they don't add more.
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Role")
    def get_role_display_(self, obj):
        return obj.get_role_display()


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("order", "code", "label", "document_count", "hidden_from")
    list_display_links = ("code",)
    list_editable = ("order", "label")
    ordering = ("order", "code")
    filter_horizontal = ("hidden_from_groups",)

    @admin.display(description="Documents")
    def document_count(self, obj):
        return Document.objects.filter(section=obj.code).count()

    @admin.display(description="Hidden from")
    def hidden_from(self, obj):
        names = [g.name for g in obj.hidden_from_groups.all()]
        return ", ".join(names) if names else "—"

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form = DocumentAdminForm
    list_display = ("code", "title", "revision", "section", "folder", "issue_date", "is_final", "hidden_from", "content_indexed_at", "updated_at")
    list_filter = ("section", "is_final", "hidden_from_groups")
    search_fields = ("title", "code", "folder", "notes")
    list_editable = ("is_final",)
    date_hierarchy = "issue_date"
    filter_horizontal = ("hidden_from_groups",)
    actions = ["move_to_folder", "clone_as_template", "reindex_selected"]
    # content_text/content_indexed_at are populated by `manage.py index_qms_documents`
    # (or the reindex action below), never hand-edited.
    readonly_fields = ("content_indexed_at", "content_text")

    @admin.display(description="Hidden from")
    def hidden_from(self, obj):
        names = [g.name for g in obj.hidden_from_groups.all()]
        return ", ".join(names) if names else "—"

    def save_model(self, request, obj, form, change):
        if not obj.uploaded_by:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Reindex selected documents (extract searchable text)")
    def reindex_selected(self, request, queryset):
        from django.utils import timezone
        from .content import extract_document_text

        indexed = unsupported = 0
        for document in queryset:
            text = extract_document_text(document)
            if text is None:
                unsupported += 1
            else:
                document.content_text = text
                indexed += 1
            document.content_indexed_at = timezone.now()
            document.save(update_fields=["content_text", "content_indexed_at"])
        self.message_user(request, f"Reindexed {indexed} document(s); {unsupported} unsupported/unreadable format(s).")

    @admin.action(description="Move selected documents to a different folder…")
    def move_to_folder(self, request, queryset):
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return HttpResponseRedirect(f"move-to-folder/?ids={ids}")

    def get_urls(self):
        custom = [
            path("move-to-folder/", self.admin_site.admin_view(self.move_to_folder_view),
                 name="library_document_move_to_folder"),
            path("<int:pk>/clone-as-template/", self.admin_site.admin_view(self.clone_as_template_view),
                 name="library_document_clone"),
        ]
        return custom + super().get_urls()

    @staticmethod
    def _can_clone(user):
        # Only QMS Manager/Admin accounts may clone — matches who already has
        # add/change permission on Document in practice, checked explicitly
        # here so this stays true even if that permission setup ever drifts.
        return user.is_superuser or user.groups.filter(name="management").exists()

    def _clone_document(self, original, request):
        """Create a new draft Document copied from `original`. File is left
        empty (safest option — never reuses the original's stored file) so
        the admin uploads a new one; is_final is forced off so the copy is
        never mistaken for an approved document; the source document is
        never modified."""
        clone = Document(
            title=f"COPY - {original.title}",
            code=original.code,
            revision=original.revision,
            section=original.section,
            folder=original.folder,
            issue_date=date.today(),
            file="",
            is_final=False,
            notes=original.notes,
            uploaded_by=request.user,
        )
        clone.save()
        clone.hidden_from_groups.set(original.hidden_from_groups.all())
        return clone

    def clone_as_template_view(self, request, pk):
        if not self._can_clone(request.user):
            raise PermissionDenied("Only QMS Manager/Admin accounts can clone documents.")
        original = get_object_or_404(Document, pk=pk)
        clone = self._clone_document(original, request)

        change_url = reverse("admin:library_document_change", args=[clone.pk])
        self.message_user(request, format_html(
            'Cloned "{}" as a new draft — upload the new file and edit details, then save. '
            '<a href="{}">Original document</a> was not changed.',
            original.title, reverse("admin:library_document_change", args=[original.pk]),
        ))
        return HttpResponseRedirect(change_url)

    @admin.action(description="Clone as Template (creates a draft copy)")
    def clone_as_template(self, request, queryset):
        if not self._can_clone(request.user):
            self.message_user(request, "Only QMS Manager/Admin accounts can clone documents.", level="error")
            return None
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one document to clone.", level="warning")
            return None
        return self.clone_as_template_view(request, queryset.first().pk)

    def move_to_folder_view(self, request):
        ids = request.POST.get("ids") if request.method == "POST" else request.GET.get("ids", "")
        pks = [int(pk) for pk in ids.split(",") if pk.isdigit()]
        docs = Document.objects.filter(pk__in=pks)

        if not docs:
            self.message_user(request, "No documents were selected.", level="warning")
            return HttpResponseRedirect("..")

        if request.method == "POST":
            form = MoveToFolderForm(request.POST)
            if form.is_valid():
                count = docs.update(section=form.cleaned_data["section"], folder=form.cleaned_data["folder"])
                self.message_user(request, f'Moved {count} document(s) to "{form.cleaned_data["folder"]}".')
                return HttpResponseRedirect("..")
        else:
            first = docs.first()
            form = MoveToFolderForm(initial={"section": first.section, "folder": first.folder})

        context = {
            **self.admin_site.each_context(request),
            "title": "Move documents to folder",
            "form": form,
            "docs": docs,
            "ids": ids,
            "opts": self.model._meta,
        }
        return render(request, "admin/library/document/move_to_folder.html", context)


@admin.register(QmsEntity)
class QmsEntityAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name", "entity_type", "country", "active")
    list_filter = ("entity_type", "active")
    search_fields = ("name", "short_name", "country")  # required for use as an autocomplete_fields target


@admin.register(QMSTaskTemplate)
class QMSTaskTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "recurrence_type", "recurrence_rule", "default_responsible", "is_active")
    list_filter = ("category", "recurrence_type", "is_active")
    search_fields = ("name", "description", "process", "iso_clause")
    autocomplete_fields = ["related_document", "default_entity"]
    actions = ["generate_task_now"]
    fieldsets = (
        (None, {"fields": ("name", "category", "description", "is_active")}),
        ("QMS context", {"fields": ("process", "iso_clause", "related_document", "default_entity")}),
        ("Recurrence", {"fields": ("recurrence_type", "recurrence_rule", "reminder_days_before")}),
        ("Defaults for generated tasks", {"fields": ("default_responsible", "default_priority", "evidence_required")}),
    )

    @admin.action(description="Generate next task now")
    def generate_task_now(self, request, queryset):
        for template in queryset:
            template.generate_task()
        self.message_user(request, f"Generated a task for {queryset.count()} template(s).")


@admin.register(QMSTask)
class QMSTaskAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "due_date", "priority", "status_badge", "responsible_person", "entity")
    list_filter = ("category", "status", "priority", "entity", "recurrence_type")
    search_fields = ("title", "description", "process", "iso_clause", "notes")
    date_hierarchy = "due_date"
    filter_horizontal = ("assigned_users",)
    autocomplete_fields = ["related_document", "evidence_document", "entity"]
    actions = ["mark_completed_action"]
    fieldsets = (
        (None, {"fields": ("title", "description", "category", "template")}),
        ("QMS context", {"fields": ("process", "iso_clause", "related_document", "entity")}),
        ("People", {"fields": ("responsible_person", "assigned_users", "created_by")}),
        ("Schedule", {"fields": ("start_date", "due_date", "completion_date", "recurrence_type", "reminder_days_before")}),
        ("Status", {"fields": ("priority", "status", "evidence_required", "evidence_document", "completion_notes", "notes")}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        return obj.display_status_label

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Mark selected as completed (generates next occurrence if recurring)")
    def mark_completed_action(self, request, queryset):
        created = 0
        for task in queryset.exclude(status="completed"):
            if task.mark_completed():
                created += 1
        self.message_user(request, f"Marked {queryset.count()} task(s) completed; {created} recurring follow-up task(s) generated.")


@admin.register(DownloadLog)
class DownloadLogAdmin(admin.ModelAdmin):
    list_display = ("at", "user", "document")
    list_filter = ("user",)
    readonly_fields = ("document", "user", "at")

    def has_add_permission(self, request):
        return False
