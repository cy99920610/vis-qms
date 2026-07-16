import json

from django import forms
from django.contrib import admin
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Document, DownloadLog, Section
from .views import build_folder_tree


class FolderPathWidget(forms.TextInput):
    """Folder field enhanced with a level-by-level path builder: pick the
    section, then pick (or type a brand-new) subfolder, then the next
    subfolder under that, and so on — mirroring the frontend library tree
    instead of requiring a hand-typed backslash path. Also keeps a plain,
    directly-editable text input (with autocomplete) as a fallback."""

    def render(self, name, value, attrs=None, renderer=None):
        attrs = {**(attrs or {}), "id": "id_folder", "list": "folder-options", "autocomplete": "off",
                 "style": "width:520px", "placeholder": r"01_ISO-9001-QMS\04_QUALITY-PROCEDURES\QP-3"}
        input_html = super().render(name, value, attrs, renderer)

        folders = Document.objects.exclude(folder="").values_list("folder", flat=True).distinct().order_by("folder")
        datalist_html = "".join(f'<option value="{escape(f)}">' for f in folders)

        tree = build_folder_tree(Document.objects.all())
        tree_json = json.dumps(tree).replace("</", "<\\/")

        return mark_safe(f"""
<div id="folder-builder" style="margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"></div>
<div style="font-size:12px;color:#777;margin-bottom:4px">Build the path level by level above, or type/edit it directly below.</div>
{input_html}
<datalist id="folder-options">{datalist_html}</datalist>
<script>
(function () {{
  var TREE = {tree_json};
  var sectionField = document.getElementById("id_section");
  var folderField = document.getElementById("id_folder");
  var levelsBox = document.getElementById("folder-builder");

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
    levelsBox.appendChild(level);

    var select = document.createElement("select");
    select.add(new Option("(end path here)", ""));
    (parentNode && parentNode.children || []).forEach(function (c) {{
      select.add(new Option(c.name + " (" + c.count + ")", c.name));
    }});
    select.add(new Option("+ New folder\\u2026", "__new__"));
    level.appendChild(select);

    var textInput = document.createElement("input");
    textInput.type = "text";
    textInput.placeholder = "folder name";
    textInput.style.display = "none";
    textInput.style.minWidth = "140px";
    level.appendChild(textInput);

    function choose(chosenName, childNode) {{
      clearAfter(level);
      var newPath = pathSoFar.concat(chosenName ? [chosenName] : []);
      setFolderValue(sectionCode, newPath);
      if (chosenName) renderLevel(sectionCode, childNode, newPath, []);
    }}

    select.onchange = function () {{
      if (select.value === "__new__") {{
        select.style.display = "none";
        textInput.style.display = "";
        textInput.value = "";
        textInput.focus();
        clearAfter(level);
        setFolderValue(sectionCode, pathSoFar);
      }} else {{
        var child = (parentNode && parentNode.children || []).find(function (c) {{ return c.name === select.value; }});
        choose(select.value, child || null);
      }}
    }};
    textInput.oninput = function () {{
      clearAfter(level);
      setFolderValue(sectionCode, pathSoFar.concat(textInput.value ? [textInput.value] : []));
    }};
    textInput.onblur = function () {{
      if (textInput.value) choose(textInput.value, null);
    }};

    var presetName = presetSegments[0];
    if (presetName !== undefined) {{
      var match = (parentNode && parentNode.children || []).find(function (c) {{ return c.name === presetName; }});
      if (match) {{
        select.value = presetName;
        renderLevel(sectionCode, match, pathSoFar.concat([presetName]), presetSegments.slice(1));
      }} else {{
        select.value = "__new__";
        select.style.display = "none";
        textInput.style.display = "";
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
