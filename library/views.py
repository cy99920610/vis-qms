from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from .models import Document, DownloadLog


def visible_documents(user):
    """Role-based visibility:
    - superuser / 'management' group: everything incl. drafts
    - 'employee' and 'auditor' groups: final approved documents only
    """
    qs = Document.objects.all()
    if user.is_superuser or user.groups.filter(name="management").exists():
        return qs
    return qs.filter(is_final=True)


@login_required
def browse(request):
    qs = visible_documents(request.user)
    q = request.GET.get("q", "").strip()
    section = request.GET.get("section", "")
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(code__icontains=q) | Q(folder__icontains=q) | Q(notes__icontains=q))
    if section:
        qs = qs.filter(section=section)
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    sections = Document._meta.get_field("section").choices
    role = "management" if (request.user.is_superuser or request.user.groups.filter(name="management").exists()) \
        else ("auditor" if request.user.groups.filter(name="auditor").exists() else "employee")
    return render(request, "library/browse.html", {"page": page, "q": q, "section": section, "sections": sections, "role": role})


@login_required
def download(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    if doc not in visible_documents(request.user):
        return HttpResponseForbidden("This document is not available for your role.")
    DownloadLog.objects.create(document=doc, user=request.user)
    return FileResponse(doc.file.open("rb"), as_attachment=False, filename=doc.file.name.split("/")[-1])
