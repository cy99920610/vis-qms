import calendar
from datetime import date, timedelta

from django.db import models
from django.db.utils import Error as DBError
from django.contrib.auth.models import User, Group


class Section(models.Model):
    """A top-level library category (e.g. '01 ISO 9001 QMS'). Manageable
    from the admin so new categories (e.g. 'Obsolete') can be added without
    a code change. `code` must match the top-level folder-path prefix used
    by documents filed under it."""
    code = models.CharField(max_length=40, unique=True,
        help_text="Must match the top-level folder name used by documents in this section, e.g. 06_OBSOLETE")
    label = models.CharField(max_length=100, help_text="Display name shown in the library tree and dropdowns, e.g. '06 Obsolete'")
    order = models.PositiveIntegerField(default=0, help_text="Display order, lowest first")
    hidden_from_groups = models.ManyToManyField(Group, blank=True, related_name="hidden_sections",
        help_text="Members of these groups (e.g. 'employee', 'auditor') won't see this section at all — "
                   "not in the library tree, dashboard, section filter, or AI assistant. Management/superusers always see everything.")

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
    hidden_from_groups = models.ManyToManyField(Group, blank=True, related_name="hidden_documents",
        help_text="Members of these groups won't see this specific document — in the library, search, "
                   "or AI assistant — even if its section is otherwise visible to them. Management/superusers always see everything.")

    class Meta:
        ordering = ["section", "folder", "title"]
        indexes = [models.Index(fields=["code"]), models.Index(fields=["section"])]

    def __str__(self):
        return f"{self.code + ' — ' if self.code else ''}{self.title}"

    @property
    def is_pdf(self):
        return self.file.name.lower().endswith(".pdf")


ROLE_CHOICES = [
    ("employee", "Employee"),
    ("auditor", "External Auditor"),
    ("internal_auditor", "Internal Auditor"),
    ("management", "QMS Manager / Admin"),
]

FORMAT_CHOICES = [
    ("pdf", "PDF"), ("docx", "DOCX"), ("xlsx", "XLSX"), ("doc", "DOC"),
    ("xls", "XLS"), ("txt", "TXT"), ("md", "MD"), ("csv", "CSV"),
]

# Single source of truth for the profile a role gets the first time it's
# looked up (views.get_role_profile) and for the data migration that seeds
# the admin list. Deliberately conservative — nothing here is looser than
# the hardcoded behaviour that existed before this feature.
ROLE_PROFILE_DEFAULTS = {
    "employee": dict(
        allowed_preview_formats="pdf", allowed_download_formats="pdf",
        can_view_draft_documents=False, can_view_source_editable_files=False,
        can_view_obsolete_documents=False, can_view_internal_notes=True,
        can_view_external_auditor_package_only=False,
    ),
    "auditor": dict(
        allowed_preview_formats="pdf", allowed_download_formats="pdf",
        can_view_draft_documents=False, can_view_source_editable_files=False,
        can_view_obsolete_documents=False, can_view_internal_notes=False,
        can_view_external_auditor_package_only=True,
    ),
    "internal_auditor": dict(
        allowed_preview_formats="pdf,xlsx", allowed_download_formats="pdf,xlsx",
        can_view_draft_documents=False, can_view_source_editable_files=False,
        can_view_obsolete_documents=False, can_view_internal_notes=True,
        can_view_external_auditor_package_only=False,
    ),
    "management": dict(
        allowed_preview_formats="pdf,docx,xlsx,doc,xls,txt,md,csv",
        allowed_download_formats="pdf,docx,xlsx,doc,xls,txt,md,csv",
        can_view_draft_documents=True, can_view_source_editable_files=True,
        can_view_obsolete_documents=True, can_view_internal_notes=True,
        can_view_external_auditor_package_only=False,
    ),
}


class RoleAccessProfile(models.Model):
    """Per-role document-format and visibility permissions. `role` matches
    the values returned by views.user_role(). Management/superusers always
    bypass this model entirely (hard-coded in views.py) — it only ever
    constrains employee / auditor / internal_auditor."""
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    allowed_preview_formats = models.CharField(max_length=200, blank=True,
        help_text="Comma-separated file extensions this role may preview/open in-browser, e.g. pdf,xlsx")
    allowed_download_formats = models.CharField(max_length=200, blank=True,
        help_text="Comma-separated file extensions this role may download, e.g. pdf,xlsx")
    can_view_draft_documents = models.BooleanField(default=False)
    can_view_source_editable_files = models.BooleanField(default=False,
        help_text="Folders whose path contains 'source-editable' or 'editable'")
    can_view_obsolete_documents = models.BooleanField(default=False,
        help_text="The 06 Obsolete section, or any folder whose path contains 'obsolete'")
    can_view_internal_notes = models.BooleanField(default=True)
    can_view_external_auditor_package_only = models.BooleanField(default=False,
        help_text="Extra lockdown for this role: also hide unsorted/duplicate-review records, "
                   "regardless of the flags above.")

    class Meta:
        ordering = ["role"]
        verbose_name = "Role access profile"

    def __str__(self):
        return self.get_role_display()

    @staticmethod
    def _parse(raw):
        return {f.strip().lower() for f in raw.split(",") if f.strip()}

    def preview_formats_set(self):
        return self._parse(self.allowed_preview_formats)

    def download_formats_set(self):
        return self._parse(self.allowed_download_formats)

    def preview_formats_list(self):
        return sorted(self.preview_formats_set())

    def download_formats_list(self):
        return sorted(self.download_formats_set())


class DownloadLog(models.Model):
    """Audit trail: who accessed which document, when (useful evidence for BV)."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="downloads")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]


# ---------------------------------------------------------------------------
# QMS Activities Calendar & Reminder System
# See QMS_ACTIVITY_CALENDAR_PLAN.md for the design rationale.
# ---------------------------------------------------------------------------

QMS_CATEGORY_CHOICES = [
    ("BOARD_MEETING", "Board Meeting"),
    ("MANAGEMENT_REVIEW", "Management Review"),
    ("INTERNAL_AUDIT", "Internal Audit"),
    ("EXTERNAL_AUDIT", "External Audit"),
    ("TRAINING", "Training"),
    ("COMPETENCE_APPRAISAL", "Competence/Appraisal"),
    ("SUPPLIER_EVALUATION", "Supplier Evaluation"),
    ("RISK_REVIEW", "Risk Review"),
    ("KPI_OBJECTIVES", "KPI/Objectives"),
    ("DOCUMENT_CONTROL", "Document Control"),
    ("QUALITY_RECORDS", "Quality Records"),
    ("NCR_CORRECTIVE_ACTION", "NCR/Corrective Action"),
    ("BRANCH_ENTITY_REPORT", "Branch/Entity Report"),
    ("CERTIFICATE_LICENSE", "Certificate/License"),
    ("CLIENT_PROJECT_REVIEW", "Client/Project Review"),
    ("GENERAL_QMS_TASK", "General QMS Task"),
]

# Stored statuses only. "Due Soon" and "Overdue" are computed at read time
# from due_date (see QMSTask.display_status) so they can never go stale —
# there's no scheduled job in this deployment to keep a stored value fresh.
QMS_STATUS_CHOICES = [
    ("planned", "Planned"),
    ("in_progress", "In Progress"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
    ("needs_review", "Needs Review"),
]
QMS_COMPUTED_STATUS_LABELS = {
    "planned": "Planned", "in_progress": "In Progress", "completed": "Completed",
    "cancelled": "Cancelled", "needs_review": "Needs Review",
    "overdue": "Overdue", "due_soon": "Due Soon",
}
# Dashboard status color per the requested scheme.
QMS_STATUS_COLORS = {
    "completed": "green", "due_soon": "yellow", "overdue": "red",
    "planned": "grey", "in_progress": "blue", "needs_review": "orange",
    "cancelled": "grey",
}

QMS_PRIORITY_CHOICES = [
    ("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical"),
]

QMS_RECURRENCE_CHOICES = [
    ("none", "None"), ("daily", "Daily"), ("weekly", "Weekly"),
    ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("annually", "Annually"),
]

QMS_RECURRENCE_RULE_CHOICES = [
    ("", "— (use recurrence interval as-is)"),
    ("first_monday", "First Monday of the month"),
]


def _add_months(d, months):
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _first_monday_on_or_after(d):
    return d + timedelta(days=(0 - d.weekday()) % 7)  # Monday == 0


def next_occurrence(base_date, recurrence_type, recurrence_rule=""):
    """The next due date after base_date for a given recurrence rule, or
    None for a one-off (non-recurring) task."""
    if not base_date or recurrence_type == "none":
        return None
    if recurrence_rule == "first_monday":
        year, month = base_date.year, base_date.month + 1
        if month > 12:
            month, year = 1, year + 1
        return _first_monday_on_or_after(date(year, month, 1))
    if recurrence_type == "daily":
        return base_date + timedelta(days=1)
    if recurrence_type == "weekly":
        return base_date + timedelta(weeks=1)
    if recurrence_type == "monthly":
        return _add_months(base_date, 1)
    if recurrence_type == "quarterly":
        return _add_months(base_date, 3)
    if recurrence_type == "annually":
        return _add_months(base_date, 12)
    return None


QMS_ENTITY_TYPE_CHOICES = [
    ("company", "Company"),
    ("branch", "Branch"),
    ("liaison_office", "Liaison Office"),
    ("partner", "Partner"),
    ("project", "Project"),
    ("other", "Other"),
]


class QmsEntity(models.Model):
    """A branch, company, liaison office, or partner that a QMS task/template
    can be scoped to — the master list behind the Entity dropdown, replacing
    free-typed entity names on QMSTask/QMSTaskTemplate."""
    name = models.CharField(max_length=150, unique=True)
    short_name = models.CharField(max_length=50, blank=True)
    entity_type = models.CharField(max_length=20, choices=QMS_ENTITY_TYPE_CHOICES, default="branch")
    country = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True, help_text="Untick to hide from new task/template dropdowns without deleting history.")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "QMS entity"
        verbose_name_plural = "QMS entities"

    def __str__(self):
        return self.name


class QMSTaskTemplate(models.Model):
    """A recurring QMS activity rule (e.g. 'Quarterly Board Meeting'). Holds
    no historical data — it only generates forward-looking QMSTask
    occurrences, either via the create_qms_default_tasks command or the
    'Generate next task now' admin action."""
    name = models.CharField(max_length=200, unique=True)
    category = models.CharField(max_length=30, choices=QMS_CATEGORY_CHOICES)
    description = models.TextField(blank=True)
    process = models.CharField(max_length=200, blank=True,
        help_text="QMS process this belongs to, e.g. 'Internal Audit Programme'")
    iso_clause = models.CharField(max_length=40, blank=True, help_text="e.g. 9.2, 9.3, 7.2")
    related_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+", help_text="The QMS procedure/form/register this activity follows")
    default_entity_text = models.CharField(max_length=100, blank=True,
        help_text="Legacy free-text value, kept for reference only — use default_entity below.")
    default_entity = models.ForeignKey(QmsEntity, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="templates", help_text="Blank means Group-wide")
    default_responsible = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    recurrence_type = models.CharField(max_length=12, choices=QMS_RECURRENCE_CHOICES, default="none")
    recurrence_rule = models.CharField(max_length=20, choices=QMS_RECURRENCE_RULE_CHOICES, blank=True)
    reminder_days_before = models.PositiveIntegerField(default=7)
    default_priority = models.CharField(max_length=10, choices=QMS_PRIORITY_CHOICES, default="medium")
    evidence_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True,
        help_text="Untick to stop generating new occurrences without deleting the template.")

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name

    def next_due_date(self, after=None):
        after = after or date.today()
        return next_occurrence(after, self.recurrence_type, self.recurrence_rule) or after

    def generate_task(self, due_date=None):
        """Create the next Planned QMSTask occurrence for this template."""
        task = QMSTask.objects.create(
            template=self, title=self.name, description=self.description,
            category=self.category, process=self.process, iso_clause=self.iso_clause,
            related_document=self.related_document, entity=self.default_entity,
            responsible_person=self.default_responsible, due_date=due_date or self.next_due_date(),
            recurrence_type=self.recurrence_type, reminder_days_before=self.reminder_days_before,
            priority=self.default_priority, status="planned", evidence_required=self.evidence_required,
        )
        return task


class QMSTask(models.Model):
    """A single QMS activity/reminder occurrence — recurring (via `template`)
    or one-off. 'Due Soon'/'Overdue' are never stored; see display_status."""
    template = models.ForeignKey(QMSTaskTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=30, choices=QMS_CATEGORY_CHOICES)
    process = models.CharField(max_length=200, blank=True)
    iso_clause = models.CharField(max_length=40, blank=True)
    related_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    entity_text = models.CharField(max_length=100, blank=True,
        help_text="Legacy free-text value, kept for reference only — use entity below.")
    entity = models.ForeignKey(QmsEntity, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    responsible_person = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="qms_tasks_responsible")
    assigned_users = models.ManyToManyField(User, blank=True, related_name="qms_tasks_assigned")
    due_date = models.DateField()
    start_date = models.DateField(null=True, blank=True)
    completion_date = models.DateField(null=True, blank=True)
    recurrence_type = models.CharField(max_length=12, choices=QMS_RECURRENCE_CHOICES, default="none")
    reminder_days_before = models.PositiveIntegerField(default=7)
    priority = models.CharField(max_length=10, choices=QMS_PRIORITY_CHOICES, default="medium")
    status = models.CharField(max_length=15, choices=QMS_STATUS_CHOICES, default="planned")
    evidence_required = models.BooleanField(default=False)
    evidence_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    completion_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_date", "title"]

    def __str__(self):
        return f"{self.title} ({self.due_date})"

    @property
    def display_status(self):
        """The status label actually shown in the UI — overrides the stored
        status with computed 'Overdue'/'Due Soon' when it applies."""
        if self.status in ("completed", "cancelled", "needs_review"):
            return self.status
        if self.due_date < date.today():
            return "overdue"
        if self.due_date <= date.today() + timedelta(days=self.reminder_days_before):
            return "due_soon"
        return self.status

    @property
    def display_status_label(self):
        return QMS_COMPUTED_STATUS_LABELS.get(self.display_status, self.display_status)

    @property
    def status_color(self):
        return QMS_STATUS_COLORS.get(self.display_status, "grey")

    def spawn_next(self, due_date):
        next_task = QMSTask.objects.create(
            template=self.template, title=self.title, description=self.description,
            category=self.category, process=self.process, iso_clause=self.iso_clause,
            related_document=self.related_document, entity=self.entity,
            responsible_person=self.responsible_person, due_date=due_date,
            recurrence_type=self.recurrence_type, reminder_days_before=self.reminder_days_before,
            priority=self.priority, status="planned", evidence_required=self.evidence_required,
            created_by=self.created_by,
        )
        next_task.assigned_users.set(self.assigned_users.all())
        return next_task

    def mark_completed(self, completion_date=None, notes=""):
        """Mark completed and, if recurring, create the next Planned
        occurrence. The new occurrence is always Planned — never Completed."""
        self.status = "completed"
        self.completion_date = completion_date or date.today()
        if notes:
            self.completion_notes = notes
        self.save()
        if self.recurrence_type != "none":
            rule = self.template.recurrence_rule if self.template else ""
            next_due = next_occurrence(self.due_date, self.recurrence_type, rule)
            if next_due:
                return self.spawn_next(next_due)
        return None
