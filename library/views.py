import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .content import extract_document_text
from .models import Document, DownloadLog, Section

CONTENT_SEARCH_SCAN_LIMIT = 60


def visible_documents(user):
    """Role-based visibility:
    - superuser / 'management' group: everything incl. drafts
    - 'employee' and 'auditor' groups: final approved documents only
    """
    qs = Document.objects.all()
    if user.is_superuser or user.groups.filter(name="management").exists():
        return qs
    return qs.filter(is_final=True)


def build_folder_tree(qs, current_folder=""):
    """Build a nested folder tree (one root per Section) from a
    role-filtered Document queryset. The tree always reflects the full
    library structure visible to the user, independent of any active
    text/section search."""
    sections = list(Section.objects.values_list("code", "label"))
    folder_counts = Counter(qs.exclude(folder="").values_list("folder", flat=True))

    roots = {code: {"name": label, "path": code, "section_code": code,
                     "own_count": 0, "children_map": {}} for code, label in sections}

    for folder, count in folder_counts.items():
        parts = [p for p in re.split(r"[\\/]+", folder.strip("\\/ ")) if p]
        if not parts:
            continue
        section_code = parts[0]
        root = roots.get(section_code)
        if root is None:
            continue
        sub_parts = parts[1:] if parts[0] == section_code else parts
        cur = root
        cur_path = section_code
        for part in sub_parts:
            cur_path = cur_path + "\\" + part
            child = cur["children_map"].get(part)
            if child is None:
                child = {"name": part, "path": cur_path, "section_code": section_code,
                          "own_count": 0, "children_map": {}}
                cur["children_map"][part] = child
            cur = child
        cur["own_count"] = count

    def is_ancestor_or_self(node_path):
        return bool(current_folder) and (
            current_folder == node_path or current_folder.startswith(node_path + "\\")
        )

    def finalize(node):
        children = sorted(node["children_map"].values(), key=lambda n: n["name"])
        for c in children:
            finalize(c)
        node["children"] = children
        node["count"] = node["own_count"] + sum(c["count"] for c in children)
        node["is_active"] = node["path"] == current_folder
        node["is_open"] = is_ancestor_or_self(node["path"])
        del node["children_map"]

    tree = []
    for code, _ in sections:
        finalize(roots[code])
        tree.append(roots[code])
    return tree


def user_role(user):
    if user.is_superuser or user.groups.filter(name="management").exists():
        return "management"
    if user.groups.filter(name="auditor").exists():
        return "auditor"
    return "employee"


@login_required
def dashboard(request):
    qs = visible_documents(request.user)
    section_counts = [
        {"code": code, "label": label, "count": qs.filter(section=code).count()}
        for code, label in Section.objects.values_list("code", "label")
    ]
    recent = qs.order_by("-uploaded_at")[:8]
    return render(request, "library/dashboard.html", {
        "role": user_role(request.user),
        "total": qs.count(),
        "section_counts": section_counts,
        "recent": recent,
    })


def distinct_formats(qs):
    """Distinct lowercase file extensions present in a Document queryset, for the format filter dropdown."""
    exts = set()
    for name in qs.values_list("file", flat=True):
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext:
            exts.add(ext)
    return sorted(exts)


def search_document_contents(qs, keyword, limit=CONTENT_SEARCH_SCAN_LIMIT):
    """Scans the most recent `limit` documents in qs for keyword inside the
    actual file text (PDF/DOCX/XLSX/CSV/MD/TXT only). Returns a Document
    queryset of the matches, most recent first. Fetches run in parallel
    since each is a network round-trip to file storage."""
    candidates = list(qs.order_by("-issue_date")[:limit])
    needle = keyword.lower()

    def matches(doc):
        text = extract_document_text(doc)
        return doc.pk if text and needle in text.lower() else None

    with ThreadPoolExecutor(max_workers=8) as pool:
        matched_ids = [pk for pk in pool.map(matches, candidates) if pk]

    order = {pk: i for i, pk in enumerate(matched_ids)}
    return sorted(
        Document.objects.filter(pk__in=matched_ids),
        key=lambda d: order[d.pk],
    )


@login_required
def browse(request):
    base_qs = visible_documents(request.user)
    q = request.GET.get("q", "").strip()
    section = request.GET.get("section", "")
    folder = request.GET.get("folder", "").strip()
    fmt = request.GET.get("format", "").strip().lower()
    content_search = request.GET.get("content") == "1"

    qs = base_qs
    if section:
        qs = qs.filter(section=section)
    if folder:
        qs = qs.filter(Q(folder=folder) | Q(folder__startswith=folder + "\\"))
    if fmt:
        qs = qs.filter(file__iendswith="." + fmt)

    content_truncated = False
    if q and content_search:
        content_truncated = qs.count() > CONTENT_SEARCH_SCAN_LIMIT
        qs = search_document_contents(qs, q)
    elif q:
        qs = qs.filter(Q(title__icontains=q) | Q(code__icontains=q) | Q(folder__icontains=q) | Q(notes__icontains=q))

    tree = build_folder_tree(base_qs, current_folder=folder)
    formats = distinct_formats(base_qs)

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    sections = Document._meta.get_field("section").choices
    return render(request, "library/browse.html", {
        "page": page, "q": q, "section": section, "folder": folder, "format": fmt,
        "sections": sections, "formats": formats, "role": user_role(request.user), "tree": tree,
        "content_search": content_search, "content_truncated": content_truncated,
        "content_scan_limit": CONTENT_SEARCH_SCAN_LIMIT,
    })


@login_required
@xframe_options_sameorigin
def download(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if doc not in visible_documents(request.user):
        return HttpResponseForbidden("This document is not available for your role.")
    DownloadLog.objects.create(document=doc, user=request.user)
    force_download = request.GET.get("dl") == "1"
    return FileResponse(doc.file.open("rb"), as_attachment=force_download, filename=doc.file.name.split("/")[-1])


@login_required
@require_POST
def assistant_ask(request):
    from . import assistant
    import anthropic

    try:
        body = json.loads(request.body)
        message = body.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        return JsonResponse({"error": True, "reply": "Invalid request.", "documents": []}, status=400)
    if not message:
        return JsonResponse({"error": True, "reply": "Please enter a question.", "documents": []}, status=400)

    history = request.session.get("assistant_history", [])
    try:
        reply, documents, new_history = assistant.run_agent_turn(request.user, message, history)
    except RuntimeError:
        return JsonResponse({"error": True, "reply": "The assistant isn't configured yet. Try the search or folder browser instead.", "documents": []})
    except anthropic.APITimeoutError:
        return JsonResponse({"error": True, "reply": "The assistant timed out. Try the search or folder browser instead.", "documents": []})
    except anthropic.APIConnectionError:
        return JsonResponse({"error": True, "reply": "Could not reach the assistant service. Try the search or folder browser instead.", "documents": []})
    except anthropic.APIStatusError:
        return JsonResponse({"error": True, "reply": "The assistant is temporarily unavailable. Try the search or folder browser instead.", "documents": []})

    request.session["assistant_history"] = new_history
    return JsonResponse({"error": False, "reply": reply, "documents": documents})
