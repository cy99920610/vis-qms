import calendar as calendar_module
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .content import extract_document_text
from .models import (
    Document, DownloadLog, QMS_CATEGORY_CHOICES, QMS_STATUS_CHOICES, QMSTask,
    RoleAccessProfile, ROLE_PROFILE_DEFAULTS, Section,
)

CONTENT_SEARCH_SCAN_LIMIT = 60


def visible_sections(user):
    """Sections this user's role is allowed to see at all — tree, dashboard,
    section dropdown, and AI assistant. A Section hidden from one of the
    user's groups (via Section.hidden_from_groups) is excluded entirely.
    Management/superusers always see every section."""
    qs = Section.objects.all()
    if user.is_superuser or user.groups.filter(name="management").exists():
        return qs
    return qs.exclude(hidden_from_groups__in=user.groups.all()).distinct()


def visible_documents(user):
    """Role-based visibility:
    - superuser / 'management' group: everything incl. drafts
    - everyone else: gated by their RoleAccessProfile (draft / source-editable /
      obsolete / external-auditor-package-only), never anything filed under a
      section hidden from one of their groups, and never an individual
      document hidden from one of their groups
    """
    qs = Document.objects.all()
    if user.is_superuser or user.groups.filter(name="management").exists():
        return qs

    profile = get_role_profile(user)
    if not profile.can_view_draft_documents:
        qs = qs.filter(is_final=True)
    if not profile.can_view_source_editable_files:
        qs = qs.exclude(folder__icontains="source-editable").exclude(folder__icontains="editable")
    if not profile.can_view_obsolete_documents:
        qs = qs.exclude(folder__icontains="obsolete").exclude(section="06_OBSOLETE")
    if profile.can_view_external_auditor_package_only:
        qs = (qs.exclude(folder__icontains="unsorted").exclude(folder__icontains="duplicate")
                .exclude(title__icontains="unsorted").exclude(title__icontains="duplicate"))

    hidden_codes = Section.objects.filter(hidden_from_groups__in=user.groups.all()).values_list("code", flat=True)
    if hidden_codes:
        qs = qs.exclude(section__in=list(hidden_codes))
    return qs.exclude(hidden_from_groups__in=user.groups.all())


def get_role_profile(user):
    """The RoleAccessProfile row for this user's role, auto-created with
    conservative defaults on first access (see ROLE_PROFILE_DEFAULTS) so the
    feature works even before an admin has visited the Role Access Profiles
    admin page."""
    role = user_role(user)
    profile, _ = RoleAccessProfile.objects.get_or_create(role=role, defaults=ROLE_PROFILE_DEFAULTS.get(role, {}))
    return profile


def get_access_context(user):
    """(preview_formats, download_formats, profile) — the two format sets are
    None for management/superuser (unrestricted); profile is None too, since
    there's nothing to check. Compute once per request and reuse — avoids a
    profile fetch (or, worse, N+1 queries) per document row in a template."""
    if user.is_superuser or user.groups.filter(name="management").exists():
        return None, None, None
    profile = get_role_profile(user)
    return profile.preview_formats_set(), profile.download_formats_set(), profile


def file_ext(doc):
    name = doc.file.name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def can_preview_format(user, ext):
    preview_formats, _, _ = get_access_context(user)
    return preview_formats is None or ext.lower() in preview_formats


def can_download_format(user, ext):
    _, download_formats, _ = get_access_context(user)
    return download_formats is None or ext.lower() in download_formats


def doc_is_openable(user, doc):
    """Combined visibility + preview-format check for a single document
    reference shown outside the main browse listing (e.g. a QMSTask's
    related/evidence document), which isn't already pre-filtered through
    visible_documents()."""
    if doc is None:
        return False
    if not visible_documents(user).filter(pk=doc.pk).exists():
        return False
    return can_preview_format(user, file_ext(doc))


def build_folder_tree(qs, sections, current_folder=""):
    """Build a nested folder tree (one root per visible Section) from a
    role-filtered Document queryset. `sections` is the (code, label) list
    of sections this user is allowed to see at all — a hidden section gets
    no root node, not just an empty one. The tree always reflects the full
    library structure visible to the user, independent of any active
    text/section search."""
    sections = list(sections)
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
    if user.groups.filter(name="internal_auditor").exists():
        return "internal_auditor"
    if user.groups.filter(name="auditor").exists():
        return "auditor"
    return "employee"


@login_required
def dashboard(request):
    qs = visible_documents(request.user)
    section_counts = [
        {"code": code, "label": label, "count": qs.filter(section=code).count()}
        for code, label in visible_sections(request.user).values_list("code", "label")
    ]
    recent = qs.order_by("-uploaded_at")[:8]

    today = date.today()
    open_qms = QMSTask.objects.exclude(status__in=["completed", "cancelled"]).select_related("responsible_person")
    qms_overdue = [t for t in open_qms if t.due_date < today]
    qms_today = [t for t in open_qms if t.due_date == today]
    qms_upcoming = sorted(
        (t for t in open_qms if today < t.due_date <= today + timedelta(days=14)),
        key=lambda t: t.due_date,
    )
    qms_next_deadline = min((t for t in open_qms if t.due_date >= today), key=lambda t: t.due_date, default=None)
    qms_this_month_count = QMSTask.objects.filter(due_date__year=today.year, due_date__month=today.month).count()

    return render(request, "library/dashboard.html", {
        "role": user_role(request.user),
        "total": qs.count(),
        "section_counts": section_counts,
        "recent": recent,
        "qms_overdue": qms_overdue[:8], "qms_overdue_count": len(qms_overdue),
        "qms_today": qms_today, "qms_upcoming": qms_upcoming[:8],
        "qms_next_deadline": qms_next_deadline, "qms_this_month_count": qms_this_month_count,
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
    user_sections = list(visible_sections(request.user).values_list("code", "label"))
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

    tree = build_folder_tree(base_qs, user_sections, current_folder=folder)
    formats = distinct_formats(base_qs)
    preview_formats, download_formats, access_profile = get_access_context(request.user)
    if preview_formats is not None:
        allowed_formats = preview_formats | download_formats
        formats = [f for f in formats if f in allowed_formats]

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "library/browse.html", {
        "page": page, "q": q, "section": section, "folder": folder, "format": fmt,
        "sections": user_sections, "formats": formats, "role": user_role(request.user), "tree": tree,
        "content_search": content_search, "content_truncated": content_truncated,
        "content_scan_limit": CONTENT_SEARCH_SCAN_LIMIT,
        "preview_formats": preview_formats, "download_formats": download_formats,
        "can_view_notes": access_profile.can_view_internal_notes if access_profile else True,
    })


@login_required
@xframe_options_sameorigin
def download(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if doc not in visible_documents(request.user):
        return HttpResponseForbidden("This document is not available for your role.")

    force_download = request.GET.get("dl") == "1"
    ext = file_ext(doc)
    allowed = can_download_format(request.user, ext) if force_download else can_preview_format(request.user, ext)
    if not allowed:
        return HttpResponseForbidden("You do not have permission to access this document format.")

    DownloadLog.objects.create(document=doc, user=request.user)
    return FileResponse(doc.file.open("rb"), as_attachment=force_download, filename=doc.file.name.split("/")[-1])


def qms_can_edit(user, task):
    """Management/superusers can edit any task. Auditors are always
    read-only (explicit requirement). Everyone else can only update a task
    they're responsible for or assigned to."""
    if user.is_superuser or user.groups.filter(name="management").exists():
        return True
    if user.groups.filter(name="auditor").exists():
        return False
    return task.responsible_person_id == user.id or task.assigned_users.filter(pk=user.id).exists()


def _qms_filtered(request):
    qs = QMSTask.objects.select_related("responsible_person", "related_document")
    category = request.GET.get("category", "")
    responsible = request.GET.get("responsible", "")
    entity = request.GET.get("entity", "")
    iso_clause = request.GET.get("iso_clause", "").strip()
    status = request.GET.get("status", "")
    if category:
        qs = qs.filter(category=category)
    if responsible:
        qs = qs.filter(responsible_person_id=responsible)
    if entity:
        qs = qs.filter(entity=entity)
    if iso_clause:
        qs = qs.filter(iso_clause__icontains=iso_clause)
    tasks = list(qs)
    if status:
        tasks = [t for t in tasks if t.display_status == status]
    filters = {"category": category, "responsible": responsible, "entity": entity,
               "iso_clause": iso_clause, "status": status}
    return tasks, filters


def _qms_filter_choices():
    return {
        "category_choices": QMS_CATEGORY_CHOICES,
        "status_choices": QMS_STATUS_CHOICES,
        "responsible_choices": User.objects.filter(qms_tasks_responsible__isnull=False)
            .distinct().order_by("username"),
        "entity_choices": QMSTask.objects.exclude(entity="").values_list("entity", flat=True).distinct().order_by("entity"),
    }


# Most-attention-worthy first — used to pick a single highlight color for a
# calendar day that has more than one task on it (year view).
_QMS_DAY_COLOR_PRIORITY = ["overdue", "due_soon", "needs_review", "in_progress", "planned", "completed", "cancelled"]


def _day_color(day_tasks):
    statuses = {t.display_status: t.status_color for t in day_tasks}
    for s in _QMS_DAY_COLOR_PRIORITY:
        if s in statuses:
            return statuses[s]
    return "grey"


def _task_popup_dict(t):
    if t.responsible_person:
        responsible = t.responsible_person.get_full_name() or t.responsible_person.username
    else:
        responsible = "—"
    return {
        "title": t.title, "category": t.get_category_display(),
        "due_date": t.due_date.strftime("%d %b %Y"), "responsible": responsible,
        "status": t.display_status_label, "status_color": t.status_color,
        "url": reverse("library:qms_task_detail", args=[t.pk]),
    }


@login_required
def qms_calendar(request):
    view = request.GET.get("view", "month")
    today = date.today()
    tasks, filters = _qms_filtered(request)

    context = {"view": view, "today": today, "role": user_role(request.user), **filters, **_qms_filter_choices()}

    if view == "week":
        start = request.GET.get("start", "")
        try:
            week_start = date.fromisoformat(start) if start else today - timedelta(days=today.weekday())
        except ValueError:
            week_start = today - timedelta(days=today.weekday())
        days = [week_start + timedelta(days=i) for i in range(7)]
        tasks_by_day = {d: [t for t in tasks if t.due_date == d] for d in days}
        context.update({
            "days": days, "tasks_by_day": tasks_by_day,
            "prev_start": (week_start - timedelta(days=7)).isoformat(),
            "next_start": (week_start + timedelta(days=7)).isoformat(),
        })
    elif view == "list":
        context["tasks"] = sorted(tasks, key=lambda t: t.due_date)
    elif view == "year":
        try:
            year = int(request.GET.get("year", today.year))
        except ValueError:
            year = today.year
        tasks_by_day = {}
        for t in tasks:
            tasks_by_day.setdefault(t.due_date, []).append(t)
        day_colors = {d: _day_color(ts) for d, ts in tasks_by_day.items()}
        day_popup = {d.isoformat(): [_task_popup_dict(t) for t in ts] for d, ts in tasks_by_day.items()}
        cal = calendar_module.Calendar(firstweekday=0)
        months = [
            {"num": m, "name": calendar_module.month_abbr[m], "weeks": cal.monthdatescalendar(year, m)}
            for m in range(1, 13)
        ]
        context.update({
            "view": "year", "year": year, "months": months,
            "tasks_by_day": tasks_by_day, "day_colors": day_colors,
            "day_popup_json": json.dumps(day_popup).replace("</", "<\\/"),
            "prev_year": year - 1, "next_year": year + 1,
        })
    else:
        view = "month"
        try:
            year = int(request.GET.get("year", today.year))
            month = int(request.GET.get("month", today.month))
        except ValueError:
            year, month = today.year, today.month
        weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
        tasks_by_day = {}
        for t in tasks:
            tasks_by_day.setdefault(t.due_date, []).append(t)
        prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
        next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
        context.update({
            "view": "month", "weeks": weeks, "tasks_by_day": tasks_by_day,
            "year": year, "month": month, "month_name": calendar_module.month_name[month],
            "prev_year": prev_year, "prev_month": prev_month,
            "next_year": next_year, "next_month": next_month,
        })

    return render(request, "library/qms_calendar.html", context)


@login_required
def qms_tasks(request):
    tasks, filters = _qms_filtered(request)
    open_count = sum(1 for t in tasks if t.status not in ("completed", "cancelled"))
    overdue_count = sum(1 for t in tasks if t.display_status == "overdue")
    completed_count = sum(1 for t in tasks if t.status == "completed")
    by_category = Counter(t.get_category_display() for t in tasks)

    page = Paginator(sorted(tasks, key=lambda t: t.due_date), 50).get_page(request.GET.get("page"))
    return render(request, "library/qms_tasks.html", {
        "page": page, "role": user_role(request.user), **filters, **_qms_filter_choices(),
        "open_count": open_count, "overdue_count": overdue_count, "completed_count": completed_count,
        "total_count": len(tasks), "by_category": by_category.most_common(),
    })


@login_required
def qms_task_detail(request, pk):
    task = get_object_or_404(QMSTask, pk=pk)
    can_edit = qms_can_edit(request.user, task)

    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("You don't have permission to update this task.")
        if request.POST.get("action") == "mark_completed":
            next_task = task.mark_completed(notes=request.POST.get("completion_notes", "").strip())
            msg = "Task marked completed."
            if next_task:
                msg += f" Next occurrence created for {next_task.due_date:%d %b %Y}."
            messages.success(request, msg)
        else:
            new_status = request.POST.get("status")
            if new_status in dict(QMS_STATUS_CHOICES):
                task.status = new_status
            task.notes = request.POST.get("notes", task.notes)
            evidence_id = request.POST.get("evidence_document")
            if evidence_id:
                task.evidence_document_id = evidence_id
            task.save()
            messages.success(request, "Task updated.")
        return redirect("library:qms_task_detail", pk=pk)

    evidence_options = visible_documents(request.user).order_by("-issue_date")[:300] if can_edit else []
    return render(request, "library/qms_task_detail.html", {
        "task": task, "can_edit": can_edit, "role": user_role(request.user),
        "status_choices": QMS_STATUS_CHOICES, "evidence_options": evidence_options,
        "related_doc_openable": doc_is_openable(request.user, task.related_document),
        "evidence_doc_openable": doc_is_openable(request.user, task.evidence_document),
    })


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
