"""Document Control Watchdog — consistency checks for the admin-only
Document Control / Maintenance Tool (QP-05A1-style revision control).

Runs over the FULL Document/QMSTask/QMSTaskTemplate tables (not
visible_documents()) since this is a management/admin-only surface that must
be able to see and flag drafts, obsolete records, and hidden documents —
that is the whole point of a maintenance watchdog. Read-only: nothing here
ever writes to a Document, moves a file, or deletes anything.

Field mapping note: the QP-05A1 sheet has a separate "Revision Date" and
"Approved by MD" date column; the Document model has a single `issue_date`
field, so it is used here as the approval/revision-date proxy for the
"missing approval date" check.
"""
from collections import defaultdict

from django.urls import reverse

from .models import Document, QMSTask, QMSTaskTemplate, folder_section_mismatch_error

CATEGORY_LABELS = {
    "missing_revision": "Missing revision",
    "missing_approval_date": "Missing approval/revision date",
    "duplicate_code": "Duplicate code (final)",
    "folder_status_mismatch": "Wrong folder for section/status",
    "final_not_pdf": "Final document not PDF",
    "draft_visible_incorrectly": "Draft/source file visible incorrectly",
    "obsolete_marked_final": "Obsolete document marked final",
    "missing_pdf_final": "Missing PDF final version",
    "mismatched_reference": "Mismatched QMS Task reference",
}
# Display order for the summary badges / category filter.
CATEGORY_ORDER = list(CATEGORY_LABELS)

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


def _doc_finding(category, severity, doc, message):
    return {
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "severity": severity,
        "message": message,
        "target_type": "document",
        "target_id": doc.pk,
        "code": doc.code,
        "title": doc.title,
        "folder": doc.folder,
        "is_final": doc.is_final,
        "admin_url": reverse("admin:library_document_change", args=[doc.pk]),
    }


def _task_finding(category, severity, task, message, is_template=False):
    admin_name = "admin:library_qmstasktemplate_change" if is_template else "admin:library_qmstask_change"
    return {
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "severity": severity,
        "message": message,
        "target_type": "task_template" if is_template else "task",
        "target_id": task.pk,
        "code": "",
        "title": task.title,
        "folder": "",
        "is_final": None,
        "admin_url": reverse(admin_name, args=[task.pk]),
    }


def _check_missing_revision(docs):
    return [
        _doc_finding("missing_revision", SEVERITY_MEDIUM, d,
                     f'Final document "{d.title}" has no revision number recorded.')
        for d in docs if d.is_final and not d.revision.strip()
    ]


def _check_missing_approval_date(docs):
    return [
        _doc_finding("missing_approval_date", SEVERITY_MEDIUM, d,
                     f'Final document "{d.title}" has no issue/approval date recorded.')
        for d in docs if d.is_final and d.issue_date is None
    ]


def _check_duplicate_code(docs):
    by_code = defaultdict(list)
    for d in docs:
        if d.is_final and d.code.strip():
            by_code[d.code.strip().lower()].append(d)
    findings = []
    for code, group in by_code.items():
        if len(group) > 1:
            for d in group:
                findings.append(_doc_finding(
                    "duplicate_code", SEVERITY_HIGH, d,
                    f'Code "{d.code}" is shared by {len(group)} final documents — only one current '
                    f'approved version per code is expected.',
                ))
    return findings


def _check_folder_status(docs):
    findings = []
    for d in docs:
        error = folder_section_mismatch_error(d.section, d.folder)
        if error:
            findings.append(_doc_finding("folder_status_mismatch", SEVERITY_HIGH, d, error))
    return findings


def _check_final_not_pdf(docs):
    return [
        _doc_finding("final_not_pdf", SEVERITY_LOW, d,
                     f'Final document "{d.title}" is a .{_ext(d)} file, not a PDF.')
        for d in docs if d.is_final and _ext(d) != "pdf"
    ]


def _ext(doc):
    name = doc.file.name
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _check_draft_visible_incorrectly(docs):
    findings = []
    for d in docs:
        folder_lower = d.folder.lower()
        looks_draft = any(tok in folder_lower for tok in ("source-editable", "editable", "draft"))
        looks_final_folder = "pdf-final" in folder_lower
        if looks_draft and d.is_final:
            findings.append(_doc_finding(
                "draft_visible_incorrectly", SEVERITY_HIGH, d,
                f'Folder "{d.folder}" looks like a draft/source-editable location, but the document '
                f'is marked Final — it would be visible to employee/auditor roles.',
            ))
        elif looks_final_folder and not d.is_final:
            findings.append(_doc_finding(
                "draft_visible_incorrectly", SEVERITY_MEDIUM, d,
                f'Folder "{d.folder}" looks like a final/pdf-final location, but the document is '
                f'marked Draft — it would be hidden from employee/auditor roles that should see it.',
            ))
    return findings


def _check_obsolete_marked_final(docs):
    findings = []
    for d in docs:
        is_obsolete_location = d.section == "06_OBSOLETE" or "obsolete" in d.folder.lower()
        if is_obsolete_location and d.is_final:
            findings.append(_doc_finding(
                "obsolete_marked_final", SEVERITY_HIGH, d,
                f'"{d.title}" is filed under an Obsolete location but is still marked Final — it '
                f'would remain visible as if it were a current approved document.',
            ))
    return findings


def _check_missing_pdf_final(docs):
    by_code = defaultdict(list)
    for d in docs:
        if d.is_final and d.code.strip():
            by_code[d.code.strip().lower()].append(d)
    findings = []
    for code, group in by_code.items():
        has_pdf = any(_ext(d) == "pdf" for d in group)
        has_nonpdf = any(_ext(d) != "pdf" for d in group)
        if has_nonpdf and not has_pdf:
            for d in group:
                findings.append(_doc_finding(
                    "missing_pdf_final", SEVERITY_MEDIUM, d,
                    f'Code "{d.code}" has a final {_ext(d).upper()} document but no final PDF version '
                    f'on file.',
                ))
    return findings


def _check_mismatched_references(tasks, templates):
    findings = []
    for t in tasks:
        for field_name, doc in (("related_document", t.related_document), ("evidence_document", t.evidence_document)):
            if doc is None:
                continue
            if not doc.is_final:
                findings.append(_task_finding(
                    "mismatched_reference", SEVERITY_MEDIUM, t,
                    f'QMS Task "{t.title}" links {field_name.replace("_", " ")} "{doc.title}", which is '
                    f'a Draft, not the current approved version.',
                ))
            elif doc.section == "06_OBSOLETE" or "obsolete" in doc.folder.lower():
                findings.append(_task_finding(
                    "mismatched_reference", SEVERITY_MEDIUM, t,
                    f'QMS Task "{t.title}" links {field_name.replace("_", " ")} "{doc.title}", which is '
                    f'filed as Obsolete.',
                ))
        if t.status == "completed" and t.evidence_required and t.evidence_document_id is None:
            findings.append(_task_finding(
                "mismatched_reference", SEVERITY_HIGH, t,
                f'QMS Task "{t.title}" is marked Completed and requires evidence, but no evidence '
                f'document is linked.',
            ))
    for tpl in templates:
        doc = tpl.related_document
        if doc is not None and not doc.is_final:
            findings.append(_task_finding(
                "mismatched_reference", SEVERITY_LOW, tpl,
                f'Template "{tpl.name}" links related document "{doc.title}", which is a Draft, not '
                f'the current approved version.', is_template=True,
            ))
    return findings


def run_watchdog_checks():
    """Runs all 9 checks and returns (findings, summary).

    findings: flat list of finding dicts, most-severe first.
    summary: {category: {"label": ..., "count": ..., "high": n, "medium": n, "low": n}}
    """
    docs = list(Document.objects.all().only(
        "id", "title", "code", "revision", "section", "folder", "issue_date", "is_final", "file",
    ))
    tasks = list(QMSTask.objects.select_related("related_document", "evidence_document"))
    templates = list(QMSTaskTemplate.objects.select_related("related_document"))

    findings = (
        _check_missing_revision(docs)
        + _check_missing_approval_date(docs)
        + _check_duplicate_code(docs)
        + _check_folder_status(docs)
        + _check_final_not_pdf(docs)
        + _check_draft_visible_incorrectly(docs)
        + _check_obsolete_marked_final(docs)
        + _check_missing_pdf_final(docs)
        + _check_mismatched_references(tasks, templates)
    )

    severity_rank = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    findings.sort(key=lambda f: (severity_rank.get(f["severity"], 3), f["category"]))

    summary = {}
    for cat in CATEGORY_ORDER:
        cat_findings = [f for f in findings if f["category"] == cat]
        summary[cat] = {
            "label": CATEGORY_LABELS[cat],
            "count": len(cat_findings),
            "high": sum(1 for f in cat_findings if f["severity"] == SEVERITY_HIGH),
            "medium": sum(1 for f in cat_findings if f["severity"] == SEVERITY_MEDIUM),
            "low": sum(1 for f in cat_findings if f["severity"] == SEVERITY_LOW),
        }
    return findings, summary
