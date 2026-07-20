# QMS Activities Calendar & Reminder System — Plan

## 1. What the historical records actually show

Queried the live document library (672 documents, `03_RECORDS\BY-YEAR\<year>\<category>`) rather than guessing. `issue_date` on most rows is the *bulk-import* date, not the real event date, so the evidence below comes from folder taxonomy, filenames, and document codes, which are reliable.

**Confirmed recurring activities, with real evidence:**

| Activity | Evidence | Real cadence found |
|---|---|---|
| Board of Directors meeting | `QP-01MBM- Minutes of Board of Directors Meeting 2025-Q1/Q2/Q3/Q4.pdf`, `2026-Q1 03.03.2026.pdf`; `QP-01BMP` = "Quarterly Meeting Plan Template" | **Quarterly**, confirmed by filename |
| Board resolutions | `QP1-RES_BOARD OF DIRECTORS RESOLUTION -RES-2025-01/02.pdf` | Tied to board meetings |
| Internal audit | `2026-01_QP-04A3_Control_Sheet_MG_by_DD_QMS_QM.pdf`, `2026-02_..._QP06_Accounting.pdf`, `2026-03_..._QP01_Crewing.pdf` | **Monthly**, rotating across a different process/department each month |
| Training & competence | Subfolders `01_JANUARY_QMS-OVERVIEW-UPDATES`, `04_JULY_QMS-AWARENESS-DOC-CONTROL`, `07_DECEMBER_ANNUAL-REFRESHER`; `pdf-final\Completed\<Name>` evidence per employee | Recurs several times/year, tied to specific named modules |
| Management review, external audit, NCR/CAR, KPI, supplier eval, risk register, client feedback | Present as a folder category every year 2022–2026 (`01_MANAGEMENT-REVIEW`, `03_EXTERNAL-AUDIT`, `05_NCR-CAR`, `07_KPI-FORECAST`, `08_SUPPLIERS`, `12_RISK-MITIGATION`, `10_CLIENTS-FEEDBACK`) | Confirmed as real, ongoing categories; cadence not filename-explicit, so I use the cadence you specified in the request |
| Document control | `00_DOCUMENT-CONTROL` (40 docs in 2026), `QP-05A4_Master_Document_Revision_Control_Sheet.xlsx`, `QP-03A1/A2/A3` document-control forms | Ongoing/quarterly |

**Real document codes to wire up as links** (corrects a couple of assumptions in the request — e.g. the AI procedure's real code is `QP-01G11`, not `AI-GL-01`):

- Board meeting → `QP-01MBM` (minutes), `QP-01BMP` (plan template), `QP1-RES` (resolutions)
- Internal audit → `QP-04A3` (control sheet)
- NCR / corrective action → `QP-04A5`
- Document control / register → `QP-03`, `QP-05A4`
- KPI → `QP-10`
- Client feedback → `QP-12`
- AI usage acknowledgement → `QP-01G11` ("Artificial Intelligence (AI) Usage Procedure")
- Training policy/schedule → `QP-01G6`, `QP-01G8`

**Real entities found** (as actual folders/header text): VIS-Recruit **Cyprus**, **Ukraine**, **Asia**, **Nepal**. Kyrgyzstan/Indonesia appear only inside training-material filenames, not as distinct entity folders — I'm keeping the entity field open text-with-suggestions rather than a hard-coded closed list, so new entities don't require a code change.

## 2. Design decisions (kept deliberately simple)

- **Two models, not a heavier scheduling system**: `QMSTaskTemplate` (the recurring *rule*) and `QMSTask` (a concrete occurrence). This mirrors the existing `Section`/`Document` pattern already in the codebase and keeps recurrence logic in one place (`QMSTaskTemplate.next_due_date()`).
- **"Due Soon" / "Overdue" are computed, not stored.** Storing them would need a cron/scheduled job to keep them fresh, which the deploy doesn't currently have. Instead `QMSTask.display_status` computes them live from `due_date` vs. today whenever a task is Planned/In Progress. `Completed`, `Cancelled`, `Needs Review` remain real stored statuses set by a person. This satisfies the "Due Soon/Overdue" requirement with zero moving parts and no risk of stale data.
- **Recurrence**: `none / daily / weekly / monthly / quarterly / annually`, plus one named rule `first_monday_of_month` for the monthly branch report. Next-occurrence math is plain `datetime` arithmetic (no new dependency). When a recurring task is marked Completed, the system auto-creates the next occurrence as **Planned** — never Completed (explicit safety rule honored).
- **Evidence and related documents reuse the existing `Document` model** (nullable FKs) instead of a new upload pipeline — consistent with how the library already stores files in R2, and lets a task point at a real Board Minutes PDF or Control Sheet already in the library.
- **Permissions reuse the existing role system** (`user_role()`, `management`/`employee`/`auditor` groups): management sees/edits everything; a task's `responsible_person`/`assigned_users` can update their own tasks; auditor is always read-only, matching the existing document-access rule.
- **No fabricated history.** The management command only creates `QMSTaskTemplate` rows plus the *next upcoming* `QMSTask` per template, always `Planned`, due dates computed from today forward — never backfilling past "completed" instances.

## 3. Data model

```
QMSTaskTemplate
  name, category, description, process, iso_clause,
  related_document (FK Document, null), default_entity, default_responsible (FK User, null),
  recurrence_type, recurrence_rule (e.g. "first_monday"), reminder_days_before,
  default_priority, evidence_required (bool), is_active

QMSTask
  template (FK QMSTaskTemplate, null — manual tasks have no template)
  title, description, category, process, iso_clause,
  related_document (FK Document, null), entity, responsible_person (FK User, null),
  assigned_users (M2M User), due_date, start_date, completion_date,
  recurrence_type, reminder_days_before, priority, status,
  evidence_required (bool), evidence_document (FK Document, null), completion_notes,
  notes, created_by (FK User), created_at, updated_at
```

Categories, statuses, priorities: exactly the lists given in the request (16 categories / 5 stored statuses + 2 computed / 4 priorities).

## 4. Pages & URLs

- Dashboard widget (extends existing `dashboard()` view): today's tasks, upcoming (14 days), overdue, this month's count, next deadline, color chips.
- `/qms-calendar/` — month view (default), week view, list view (`?view=month|week|list`), filters: category, responsible, status, entity, ISO clause.
- `/qms-tasks/` — filterable report list with summary counts (open, overdue, completed this period, by category, by responsible).
- `/qms-tasks/<id>/` — task detail: update status, add completion notes, link/upload evidence, mark complete (auto-regenerates next occurrence if recurring). Auditor sees this read-only.

## 5. Admin

- `QMSTaskAdmin`: list/filter by category, status, priority, entity, responsible; bulk action "Mark selected as completed" (regenerates recurring next-occurrences); `filter_horizontal` for assigned users.
- `QMSTaskTemplateAdmin`: manage recurrence rules; action "Generate next task now".

## 6. Management command

`python manage.py create_qms_default_tasks` — idempotent (`get_or_create` by template name). Creates the ~18 templates from the request (Board Meeting, Monthly Branch Report, Internal Audit, Management Review, GDPR/QMS Awareness/AI-usage training, Supplier Evaluation, Risk Register Review, Document/Quality Records Register Review, NCR follow-up, etc.), wired to the real document codes found above, and generates one upcoming `Planned` task per template.

## 7. What's simplified vs. the full request (and why)

- Week view: implemented (reuses the month view's day-cell rendering), but it's intentionally plain — no drag/drop.
- Email reminders: not implemented (no email backend configured in settings currently); the model fields (`reminder_days_before`) are in place so this is a follow-up, not a redesign, when email is wanted.
- "Needs Review" is a manual status a reviewer sets, not an automated trigger — automating it would need business rules not evidenced in the records.

## 8. Safety

No existing model, migration, or document is touched. This is purely additive: two new models, one new migration, new views/URLs/templates, one new admin registration, one new management command. Nothing deletes or overwrites existing QMS files or documents. No historical "completed" task instances are invented — only forward-looking `Planned` tasks.
