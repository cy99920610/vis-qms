from django.db import models
from django.contrib.auth.models import User

SECTIONS = [
    ("00_REGISTER", "00 Register"),
    ("01_ISO-9001-QMS", "01 ISO 9001 QMS"),
    ("02_ISM-SMS", "02 ISM SMS"),
    ("03_RECORDS", "03 Records"),
    ("04_ENTITY-EVIDENCE", "04 Entity Evidence"),
    ("05_CERTIFICATES", "05 Certificates"),
]

class Document(models.Model):
    """A controlled document or record of the VIS-Recruit QMS."""
    title = models.CharField(max_length=300)
    code = models.CharField(max_length=40, blank=True, help_text="e.g. QP-03, OP-01, QM-01")
    revision = models.CharField(max_length=20, blank=True)
    section = models.CharField(max_length=40, choices=SECTIONS, default="01_ISO-9001-QMS")
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


class DownloadLog(models.Model):
    """Audit trail: who accessed which document, when (useful evidence for BV)."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
