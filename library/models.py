from django.db import models
from django.db.utils import Error as DBError
from django.contrib.auth.models import User


class Section(models.Model):
    """A top-level library category (e.g. '01 ISO 9001 QMS'). Manageable
    from the admin so new categories (e.g. 'Obsolete') can be added without
    a code change. `code` must match the top-level folder-path prefix used
    by documents filed under it."""
    code = models.CharField(max_length=40, unique=True,
        help_text="Must match the top-level folder name used by documents in this section, e.g. 06_OBSOLETE")
    label = models.CharField(max_length=100, help_text="Display name shown in the library tree and dropdowns, e.g. '06 Obsolete'")
    order = models.PositiveIntegerField(default=0, help_text="Display order, lowest first")

    class Meta:
        ordering = ["order", "code"]

    def __str__(self):
        return self.label


def section_choices():
    """Dynamic choices for Document.section — evaluated on access, so new
    Sections added via admin show up immediately without a restart. Falls
    back to no choices if the table isn't there yet (e.g. before the first
    migrate on a fresh database, or during makemigrations' system checks)."""
    try:
        return [(s.code, s.label) for s in Section.objects.all()]
    except DBError:
        return []


class Document(models.Model):
    """A controlled document or record of the VIS-Recruit QMS."""
    title = models.CharField(max_length=300)
    code = models.CharField(max_length=40, blank=True, help_text="e.g. QP-03, OP-01, QM-01")
    revision = models.CharField(max_length=20, blank=True)
    section = models.CharField(max_length=40, choices=section_choices, default="01_ISO-9001-QMS")
    folder = models.CharField(max_length=300, blank=True, help_text="Location within the library structure")
    issue_date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="library/%Y/")
    is_final = models.BooleanField(default=True, help_text="Final approved document (visible to auditor/employees). Untick for drafts/working versions.")
    notes = models.CharField(max_length=400, blank=True)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "folder", "title"]
        indexes = [models.Index(fields=["code"]), models.Index(fields=["section"])]

    def __str__(self):
        return f"{self.code + ' — ' if self.code else ''}{self.title}"

    @property
    def is_pdf(self):
        return self.file.name.lower().endswith(".pdf")


class DownloadLog(models.Model):
    """Audit trail: who accessed which document, when (useful evidence for BV)."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
