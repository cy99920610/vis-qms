import json

from django import forms
from django.contrib import admin
from django.utils.safestring import mark_safe

from .models import Document, DownloadLog, Section
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
        section, folder = cleaned.get("section"), cleaned.get("folder")
        if section and folder and not (folder == section or folder.startswith(section + "\\")):
            self.add_error("folder",
                f'Folder must start with the chosen section\'s code ("{section}"), e.g. '
                f'"{section}\\Some Subfolder" — otherwise this document won\'t appear under '
                f'this section in the library tree.')
        return cleaned


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
    list_display = ("code", "title", "revision", "section", "folder", "issue_date", "is_final", "hidden_from", "updated_at")
    list_filter = ("section", "is_final", "hidden_from_groups")
    search_fields = ("title", "code", "folder", "notes")
    list_editable = ("is_final",)
    date_hierarchy = "issue_date"
    filter_horizontal = ("hidden_from_groups",)

    @admin.display(description="Hidden from")
    def hidden_from(self, obj):
        names = [g.name for g in obj.hidden_from_groups.all()]
        return ", ".join(names) if names else "—"

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
