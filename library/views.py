import calendar as calendar_module
import io
import json
import re
from collections import Counter
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from .models import (
    Document, DownloadLog, QmsEntity, QMS_CATEGORY_CHOICES, QMS_STATUS_CHOICES, QMSTask,
    RoleAccessProfile, ROLE_PROFILE_DEFAULTS, Section,
)

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


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
    """Role-based visibility — the single central query filter. Every listing
    (browse, search, folder counts, AI assistant, QMS task/calendar linked
    documents) is built from this queryset, so a document the role can't see
    never appears anywhere, not just in the main table:
    - superuser / 'management' group: everything incl. drafts
    - everyone else: gated by their RoleAccessProfile — draft / source-editable /
      obsolete / external-auditor-package-only visibility, AND file format
      (a document whose extension isn't in the role's allowed preview or
      download formats is excluded from the queryset entirely, at the DB
      level, not just hidden behind a UI badge) — never anything filed under
      a section hidden from one of their groups, and never an individual
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
    qs = qs.exclude(hidden_from_groups__in=user.groups.all())

    allowed_formats = profile.preview_formats_set() | profile.download_formats_set()
    if not allowed_formats:
        return qs.none()
    format_q = Q()
    for ext in allowed_formats:
        format_q |= Q(file__iendswith="." + ext)
    return qs.filter(format_q)


def can_user_view_document(user, document):
    """Single-document visibility check — same rule as visible_documents(),
    for call sites that already have a Document instance in hand (QMS task
    related/evidence links) rather than a queryset to filter."""
    if document is None:
        return False
    return visible_documents(user).filter(pk=document.pk).exists()


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
    if not can_user_view_document(user, doc):
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


def is_management(user):
    return user.is_superuser or user.groups.filter(name="management").exists()


def user_role(user):
    if is_management(user):
        return "management"
    if user.groups.filter(name="internal_auditor").exists():
        return "internal_auditor"
    if user.groups.filter(name="auditor").exists():
        return "auditor"
    return "employee"


def management_required(view_func):
    """Full-page guard for admin-only tools (the Document Control /
    Maintenance Tool) — unlike visible_documents()'s row-level filtering,
    this blocks the whole view for non-management roles."""
    @login_required
    def wrapper(request, *args, **kwargs):
        if not is_management(request.user):
            return HttpResponseForbidden("The Document Control tool is available to QMS Manager/Admin accounts only.")
        return view_func(request, *args, **kwargs)
    return wrapper


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


def _keyword_query(fields, keyword):
    """AND across words in `keyword`, OR across `fields` for each word —
    e.g. two-word "annual appraisal" requires both words present, each
    matched in any of the given fields."""
    q = Q()
    for word in keyword.split():
        word_q = Q()
        for field in fields:
            word_q |= Q(**{f"{field}__icontains": word})
        q &= word_q
    return q


def build_snippet(text, keyword, context=70):
    """A short excerpt of `text` around the first matching word from
    `keyword`, with every occurrence of every query word wrapped in <mark>.
    Escapes first, then highlights, so this is always safe to render
    unescaped (it returns a mark_safe string)."""
    words = [w for w in keyword.split() if w]
    if not text or not words:
        return ""
    lower = text.lower()
    positions = [lower.find(w.lower()) for w in words]
    positions = [p for p in positions if p != -1]
    if not positions:
        return ""

    idx = min(positions)
    start = max(0, idx - context)
    end = min(len(text), idx + context + max(len(w) for w in words))
    snippet = " ".join(text[start:end].split())
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(text) else ""

    highlighted = escape(prefix + snippet + suffix)
    for word in sorted(set(words), key=len, reverse=True):
        pattern = re.compile(re.escape(escape(word)), re.IGNORECASE)
        highlighted = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", highlighted)
    return mark_safe(highlighted)


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

    if q:
        fields = ["title", "code", "folder", "notes"]
        if content_search:
            fields.append("content_text")
        qs = qs.filter(_keyword_query(fields, q))

    tree = build_folder_tree(base_qs, user_sections, current_folder=folder)
    # base_qs is already format-filtered by visible_documents(), so this
    # naturally only lists formats the role can actually see.
    formats = distinct_formats(base_qs)
    preview_formats, download_formats, access_profile = get_access_context(request.user)

    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    if content_search and q:
        for d in page.object_list:
            d.snippet = build_snippet(d.content_text, q)
    else:
        for d in page.object_list:
            d.snippet = ""

    return render(request, "library/browse.html", {
        "page": page, "q": q, "section": section, "folder": folder, "format": fmt,
        "sections": user_sections, "formats": formats, "role": user_role(request.user), "tree": tree,
        "content_search": content_search,
        "preview_formats": preview_formats, "download_formats": download_formats,
        "can_view_notes": access_profile.can_view_internal_notes if access_profile else True,
    })


@login_required
def document_search_api(request):
    """Metadata search behind the 'Link evidence document' autocomplete
    widget — same permission gate as Browse Documents (visible_documents),
    so a role never sees a document here it couldn't already see/open in
    the library. Not a general-purpose API; used only by that widget."""
    q = request.GET.get("q", "").strip()
    qs = visible_documents(request.user)
    if q:
        qs = qs.filter(_keyword_query(["title", "code", "folder"], q))
    qs = qs.order_by("-issue_date")[:30]
    results = [
        {
            "id": d.pk,
            "text": f"{d.code + ' — ' if d.code else ''}{d.title}",
            "format": file_ext(d).upper(),
            "folder": d.folder,
            "status": "Final" if d.is_final else "Draft",
        }
        for d in qs
    ]
    return JsonResponse({"results": results})


@login_required
def document_preview_info(request, pk):
    """Backs the same-page evidence-document preview panel on the QMS Task
    detail page. Gated by doc_is_openable() — the same visibility +
    preview-format check used everywhere else — so a document the user
    isn't allowed to see or preview yields only {"allowed": False}, with no
    title/folder/content leaked. Download eligibility is checked separately
    since RoleAccessProfile allows preview and download formats to differ."""
    doc = Document.objects.filter(pk=pk).first()
    if doc is None or not doc_is_openable(request.user, doc):
        return JsonResponse({"id": pk, "allowed": False})

    ext = file_ext(doc)
    if ext == "pdf":
        embed_type = "pdf"
    elif ext in ("txt", "md"):
        embed_type = "text"
    else:
        embed_type = "none"

    can_download = can_download_format(request.user, ext)
    return JsonResponse({
        "id": doc.pk,
        "allowed": True,
        "title": doc.title,
        "code": doc.code,
        "filename": doc.file.name.rsplit("/", 1)[-1],
        "folder": doc.folder,
        "format": ext.upper(),
        "issue_date": doc.issue_date.isoformat() if doc.issue_date else None,
        "is_final": doc.is_final,
        "preview_url": reverse("library:download", args=[doc.pk]),
        "download_url": reverse("library:download", args=[doc.pk]) + "?dl=1" if can_download else None,
        "embed_type": embed_type,
        "text_preview": doc.content_text[:4000] if embed_type == "text" else None,
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
    filename = doc.file.name.split("/")[-1]

    # Chrome's built-in PDF viewer always probes with a Range request before
    # rendering an embedded PDF; without a real 206 response it fails to
    # parse the file and renders a blank/black page (this is what was
    # breaking the PDF preview panel/modal). django.http.FileResponse
    # doesn't implement Range handling itself (that's only built into
    # django.views.static.serve), so it's done by hand here.
    range_match = None if force_download else RANGE_RE.match(request.headers.get("Range", ""))
    if range_match:
        file_size = doc.file.size
        start = int(range_match.group(1)) if range_match.group(1) else 0
        end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        with doc.file.open("rb") as f:
            f.seek(start)
            chunk = f.read(length)
        response = FileResponse(io.BytesIO(chunk), filename=filename)
        response.status_code = 206
        response["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        response["Content-Length"] = str(length)
    else:
        response = FileResponse(doc.file.open("rb"), as_attachment=force_download, filename=filename)
    response["Accept-Ranges"] = "bytes"
    return response


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
    qs = QMSTask.objects.select_related("responsible_person", "related_document", "entity")
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
        qs = qs.filter(entity_id=entity)
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
        "entity_choices": QmsEntity.objects.filter(active=True).order_by("name"),
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
            if "evidence_document" in request.POST:
                evidence_id = request.POST.get("evidence_document")
                if not evidence_id:
                    task.evidence_document_id = None
                elif visible_documents(request.user).filter(pk=evidence_id).exists():
                    task.evidence_document_id = evidence_id
                else:
                    messages.error(request, "Selected evidence document is not available for your role.")
            task.save()
            messages.success(request, "Task updated.")
        return redirect("library:qms_task_detail", pk=pk)

    return render(request, "library/qms_task_detail.html", {
        "task": task, "can_edit": can_edit, "role": user_role(request.user),
        "status_choices": QMS_STATUS_CHOICES,
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


# ---------------------------------------------------------------------------
# Document Control / Maintenance Tool (admin-only)
# ---------------------------------------------------------------------------

def _doc_control_filters(request):
    """Read + apply the per-column filters for the Final Approved PDF
    Register. The is_final/PDF floor is always applied — that's the
    'dynamically show final approved PDF documents only' requirement — every
    other filter only narrows further."""
    filters = {
        "code": request.GET.get("code", "").strip(),
        "title": request.GET.get("title", "").strip(),
        "revision": request.GET.get("revision", "").strip(),
        "section": request.GET.get("section", "").strip(),
        "folder": request.GET.get("folder", "").strip(),
        "uploaded_by": request.GET.get("uploaded_by", "").strip(),
        "issue_from": request.GET.get("issue_from", "").strip(),
        "issue_to": request.GET.get("issue_to", "").strip(),
    }
    qs = Document.objects.filter(is_final=True, file__iendswith=".pdf").select_related("uploaded_by")
    if filters["code"]:
        qs = qs.filter(code__icontains=filters["code"])
    if filters["title"]:
        qs = qs.filter(title__icontains=filters["title"])
    if filters["revision"]:
        qs = qs.filter(revision__icontains=filters["revision"])
    if filters["section"]:
        qs = qs.filter(section=filters["section"])
    if filters["folder"]:
        qs = qs.filter(folder__icontains=filters["folder"])
    if filters["uploaded_by"]:
        qs = qs.filter(uploaded_by_id=filters["uploaded_by"])
    if filters["issue_from"]:
        qs = qs.filter(issue_date__gte=filters["issue_from"])
    if filters["issue_to"]:
        qs = qs.filter(issue_date__lte=filters["issue_to"])
    return qs.order_by("section", "folder", "code", "title"), filters


@management_required
def doc_control(request):
    from .watchdog import run_watchdog_checks

    qs, filters = _doc_control_filters(request)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))

    uploader_ids = Document.objects.exclude(uploaded_by__isnull=True).values_list("uploaded_by", flat=True).distinct()
    uploaders = User.objects.filter(pk__in=uploader_ids).order_by("username")

    findings, summary = run_watchdog_checks()
    watchdog_category = request.GET.get("wd_category", "").strip()
    shown_findings = [f for f in findings if not watchdog_category or f["category"] == watchdog_category]

    export_params = request.GET.copy()
    export_params.pop("page", None)
    high_count = sum(1 for f in findings if f["severity"] == "high")

    return render(request, "library/doc_control.html", {
        "role": user_role(request.user),
        "page": page,
        "filters": filters,
        "sections": list(Section.objects.values_list("code", "label")),
        "uploaders": uploaders,
        "register_count": qs.count(),
        "findings": shown_findings[:300],
        "findings_shown_count": len(shown_findings),
        "findings_total": len(findings),
        "findings_high_count": high_count,
        "summary": summary,
        "watchdog_category": watchdog_category,
        "export_qs": export_params.urlencode(),
    })


@management_required
def doc_control_watchdog_api(request):
    from .watchdog import run_watchdog_checks

    findings, _ = run_watchdog_checks()
    category = request.GET.get("wd_category", "").strip()
    if category:
        findings = [f for f in findings if f["category"] == category]

    return JsonResponse({
        "category": category,
        "total_count": len(findings),
        "findings": findings[:300],
    })


@management_required
def doc_control_export(request, fmt, dataset):
    from . import exports
    from .watchdog import run_watchdog_checks

    if fmt not in ("xlsx", "pdf") or dataset not in ("register", "watchdog"):
        return HttpResponseForbidden("Unknown export requested.")

    if dataset == "register":
        qs, _ = _doc_control_filters(request)
        buf = exports.register_xlsx(qs) if fmt == "xlsx" else exports.register_pdf(qs)
        filename = f"VIS-QMS_Final_PDF_Register.{fmt}"
    else:
        findings, _ = run_watchdog_checks()
        category = request.GET.get("wd_category", "").strip()
        if category:
            findings = [f for f in findings if f["category"] == category]
        buf = exports.findings_xlsx(findings) if fmt == "xlsx" else exports.findings_pdf(findings)
        filename = f"VIS-QMS_Watchdog_Findings.{fmt}"

    content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/pdf"
    response = HttpResponse(buf.getvalue(), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@management_required
@require_POST
def doc_control_agent_ask(request):
    from . import qms_agent
    import anthropic

    try:
        body = json.loads(request.body)
        message = body.get("message", "").strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        return JsonResponse({"error": True, "reply": "Invalid request.", "documents": []}, status=400)
    if not message:
        return JsonResponse({"error": True, "reply": "Please enter a question.", "documents": []}, status=400)

    history = request.session.get("qms_agent_history", [])
    try:
        reply, documents, new_history = qms_agent.run_qms_agent_turn(request.user, message, history)
    except RuntimeError:
        return JsonResponse({"error": True, "reply": "The QMS agent isn't configured yet (missing API key).", "documents": []})
    except anthropic.APITimeoutError:
        return JsonResponse({"error": True, "reply": "The QMS agent timed out — please try again.", "documents": []})
    except anthropic.APIConnectionError:
        return JsonResponse({"error": True, "reply": "Could not reach the assistant service.", "documents": []})
    except anthropic.APIStatusError:
        return JsonResponse({"error": True, "reply": "The QMS agent is temporarily unavailable.", "documents": []})

    request.session["qms_agent_history"] = new_history
    return JsonResponse({"error": False, "reply": reply, "documents": documents})
