from datetime import date, timedelta

from django.core.management.base import BaseCommand

from library.models import Document, QMSTaskTemplate


def doc(code):
    """Best-effort lookup of a real library document by code — used to wire
    templates to the real procedures found during the historical review in
    QMS_ACTIVITY_CALENDAR_PLAN.md. Some records (e.g. QP-04A3 control sheets)
    only carry the code in the filename/title, not the dedicated `code`
    field, so fall back to a title search. Returns None if not found (never
    blocks template creation)."""
    return (
        Document.objects.filter(code__iexact=code).first()
        or Document.objects.filter(code__istartswith=code).first()
        or Document.objects.filter(title__icontains=code).order_by("-issue_date").first()
    )


# Real cadence/document evidence is documented in QMS_ACTIVITY_CALENDAR_PLAN.md.
# due_in_days is only used to compute the *first* generated task's due date —
# always forward-looking from today, never a fabricated past date.
TEMPLATES = [
    dict(name="Quarterly Board Meeting", category="BOARD_MEETING",
         description="Board of Directors quarterly meeting, minutes (QP-01MBM) and resolutions (QP1-RES).",
         process="Corporate Governance", iso_clause="5.1", doc_code="QP-01MBM",
         recurrence_type="quarterly", reminder_days_before=14, priority="high", due_in_days=30),
    dict(name="Monthly Branch/Entity Report", category="BRANCH_ENTITY_REPORT",
         description="Monthly QMS activity report for each branch/entity (Cyprus, Ukraine, Asia, Nepal).",
         process="Management Reporting", iso_clause="9.1", doc_code=None,
         recurrence_type="monthly", recurrence_rule="first_monday", reminder_days_before=3,
         priority="medium", due_in_days=14),
    dict(name="Internal Audit — Process Rotation", category="INTERNAL_AUDIT",
         description="Monthly internal audit control sheet (QP-04A3), rotating across departments/processes.",
         process="Internal Audit Programme", iso_clause="9.2", doc_code="QP-04A3",
         recurrence_type="monthly", reminder_days_before=7, priority="high", due_in_days=21),
    dict(name="External Audit Preparation", category="EXTERNAL_AUDIT",
         description="Prepare evidence and readiness ahead of the external/certification audit.",
         process="External Audit", iso_clause="9.2", doc_code=None,
         recurrence_type="annually", reminder_days_before=30, priority="critical", due_in_days=180),
    dict(name="Management Review Meeting", category="MANAGEMENT_REVIEW",
         description="Annual management review of the QMS per ISO 9001 clause 9.3.",
         process="Management Review", iso_clause="9.3", doc_code=None,
         recurrence_type="annually", reminder_days_before=21, priority="high", due_in_days=120),
    dict(name="QMS Awareness Training Refresher", category="TRAINING",
         description="Annual QMS overview/awareness refresher for all staff.",
         process="Training & Competence", iso_clause="7.2", doc_code="QP-01G6",
         recurrence_type="annually", reminder_days_before=14, priority="medium", due_in_days=90),
    dict(name="GDPR / Data Protection Training Refresher", category="TRAINING",
         description="Annual GDPR / data protection awareness refresher.",
         process="Training & Competence", iso_clause="7.2", doc_code=None,
         recurrence_type="annually", reminder_days_before=14, priority="medium", due_in_days=90),
    dict(name="AI Usage Guideline Acknowledgement", category="TRAINING",
         description="Staff acknowledgement of the Artificial Intelligence (AI) Usage Procedure.",
         process="Training & Competence", iso_clause="7.2", doc_code="QP-01G11",
         recurrence_type="annually", reminder_days_before=14, priority="medium", due_in_days=90),
    dict(name="Training Quiz Refresher", category="TRAINING",
         description="Periodic QMS/competence quiz refresher for staff.",
         process="Training & Competence", iso_clause="7.2", doc_code="QP-01G8",
         recurrence_type="annually", reminder_days_before=14, priority="low", due_in_days=150),
    dict(name="Competence / Appraisal Review", category="COMPETENCE_APPRAISAL",
         description="Annual staff competence and appraisal review.",
         process="Training & Competence", iso_clause="7.2", doc_code=None,
         recurrence_type="annually", reminder_days_before=14, priority="medium", due_in_days=150),
    dict(name="Supplier Evaluation Review", category="SUPPLIER_EVALUATION",
         description="Review and re-evaluate approved suppliers.",
         process="Supplier Management", iso_clause="8.4", doc_code=None,
         recurrence_type="annually", reminder_days_before=14, priority="medium", due_in_days=120),
    dict(name="Risk Register Review", category="RISK_REVIEW",
         description="Quarterly review of the QMS risk register and mitigation actions.",
         process="Risk Management", iso_clause="6.1", doc_code=None,
         recurrence_type="quarterly", reminder_days_before=7, priority="high", due_in_days=45),
    dict(name="KPI / Quality Objectives Review", category="KPI_OBJECTIVES",
         description="Quarterly review of KPIs and quality objectives (QP-10).",
         process="Quality Objectives", iso_clause="6.2", doc_code="QP-10",
         recurrence_type="quarterly", reminder_days_before=7, priority="medium", due_in_days=45),
    dict(name="Document Register Review", category="DOCUMENT_CONTROL",
         description="Quarterly review of the master document register (QP-05A4) for accuracy.",
         process="Document Control", iso_clause="7.5", doc_code="QP-05A4",
         recurrence_type="quarterly", reminder_days_before=7, priority="medium", due_in_days=45),
    dict(name="Quality Records Register Review", category="QUALITY_RECORDS",
         description="Quarterly review of the quality records register for completeness.",
         process="Document Control", iso_clause="7.5", doc_code="QP-03",
         recurrence_type="quarterly", reminder_days_before=7, priority="medium", due_in_days=45),
    dict(name="Obsolete Documents Review", category="DOCUMENT_CONTROL",
         description="Quarterly review to identify and correctly file/mark obsolete documents.",
         process="Document Control", iso_clause="7.5", doc_code=None,
         recurrence_type="quarterly", reminder_days_before=7, priority="low", due_in_days=45),
    dict(name="Duplicate / Unsorted Documents Review", category="DOCUMENT_CONTROL",
         description="Quarterly review to resolve duplicate and unsorted documents in the library.",
         process="Document Control", iso_clause="7.5", doc_code=None,
         recurrence_type="quarterly", reminder_days_before=7, priority="low", due_in_days=45),
    dict(name="NCR / Corrective Action Follow-up", category="NCR_CORRECTIVE_ACTION",
         description="Follow up on open nonconformities and corrective actions (QP-04A5).",
         process="Nonconformity Management", iso_clause="10.2", doc_code="QP-04A5",
         recurrence_type="monthly", reminder_days_before=5, priority="high", due_in_days=21),
    dict(name="Client / Project Review", category="CLIENT_PROJECT_REVIEW",
         description="Review client feedback and project cooperation (e.g. Matrix ferry cooperation), QP-12.",
         process="Customer Satisfaction", iso_clause="9.1.2", doc_code="QP-12",
         recurrence_type="quarterly", reminder_days_before=7, priority="medium", due_in_days=45),
    dict(name="Audit Evidence Preparation", category="GENERAL_QMS_TASK",
         description="Prepare and organize evidence ahead of internal/external audits.",
         process="Internal Audit Programme", iso_clause="9.2", doc_code=None,
         recurrence_type="quarterly", reminder_days_before=10, priority="medium", due_in_days=40),
]


class Command(BaseCommand):
    help = ("Create the standing QMS activity templates (idempotent) and generate one upcoming, "
            "Planned task per template. Never creates or backfills 'completed' history — see "
            "QMS_ACTIVITY_CALENDAR_PLAN.md for the historical evidence behind these defaults.")

    def handle(self, *args, **options):
        created_templates = 0
        created_tasks = 0
        today = date.today()

        for spec in TEMPLATES:
            related_document = doc(spec["doc_code"]) if spec.get("doc_code") else None
            template, made = QMSTaskTemplate.objects.get_or_create(
                name=spec["name"],
                defaults=dict(
                    category=spec["category"], description=spec["description"],
                    process=spec.get("process", ""), iso_clause=spec.get("iso_clause", ""),
                    related_document=related_document,
                    recurrence_type=spec["recurrence_type"], recurrence_rule=spec.get("recurrence_rule", ""),
                    reminder_days_before=spec.get("reminder_days_before", 7),
                    default_priority=spec.get("priority", "medium"),
                    evidence_required=True, is_active=True,
                ),
            )
            if made:
                created_templates += 1
                self.stdout.write(f"  + template: {template.name}")

            # Only generate a task if this template has no upcoming Planned/In-Progress occurrence yet.
            has_open_task = template.tasks.filter(status__in=["planned", "in_progress"]).exists()
            if not has_open_task:
                due_date = today + timedelta(days=spec.get("due_in_days", 30))
                template.generate_task(due_date=due_date)
                created_tasks += 1
                self.stdout.write(f"    -> task due {due_date}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. {created_templates} new template(s), {len(TEMPLATES)} total. "
            f"{created_tasks} new upcoming task(s) generated (all Planned)."
        ))
