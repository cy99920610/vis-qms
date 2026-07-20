# VIS-QMS Web Portal — Deployment Guide (GitHub → Render → vis-qms.com)

Django portal for the VIS-Recruit controlled library: admin panel, user accounts with
roles (**management** = everything incl. drafts; **employee** and **auditor** = final
approved documents only), search, download with access logging (DownloadLog — audit
trail evidence for BV), cloud database and cloud document storage.

Your workflow stays as it is today: work locally with Claude/Cursor → `git push` →
Render redeploys automatically → changes are live on vis-qms.com.

---

## 0. Local test first (5 minutes, in Cursor terminal)

```bash
cd django-visqms
python -m venv .venv && .venv\Scripts\activate        # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py init_roles
python manage.py createsuperuser
python manage.py runserver
```
Open http://127.0.0.1:8000 → login → http://127.0.0.1:8000/admin.
Import the library locally to try it with real documents:
```bash
python manage.py import_library --source "../02_CONTROLLED-LIBRARY" --sections 01_ISO-9001-QMS 05_CERTIFICATES
```

## 1. GitHub

```bash
cd django-visqms
git init && git add . && git commit -m "VIS-QMS portal initial"
```
Create a **private** repository (e.g. `vis-qms`) on github.com and push.
> Never commit `.env`, `db.sqlite3`, or `media/` — already excluded via .gitignore.

## 2. Render

1. render.com → New → **Blueprint** → connect the GitHub repo. `render.yaml` creates:
   - Web service **vis-qms** (Starter plan, $7/mo — always-on; the free tier sleeps,
     which looks bad during an audit)
   - PostgreSQL **visqms-db** (Basic, ~$6/mo) — your cloud database
2. In the service → Environment: set **DJANGO_SUPERUSER_PASSWORD** (strong).
   Everything else is preconfigured; SECRET_KEY is auto-generated.
3. First deploy runs `build.sh`: installs, collects static, migrates, creates
   groups + superuser. Site is live at `https://vis-qms.onrender.com`.

## 3. Document storage (choose one)

- **Simplest — Render Disk:** service → Disks → add 1–5 GB mounted at `/var/data`,
  then env var `MEDIA_ROOT=/var/data/media`. Documents survive deploys. (~$0.25/GB/mo)
- **S3-compatible (recommended long-term):** create a bucket (Cloudflare R2 has a free
  tier, or AWS S3 / Backblaze B2) and set env vars:
  `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  and for R2/B2 also `AWS_S3_ENDPOINT_URL`. Downloads then use 5-minute signed URLs.

## 4. Domain — Namecheap → vis-qms.com

1. Buy `vis-qms.com` on Namecheap.
2. Render service → Settings → Custom Domains → add `vis-qms.com` and `www.vis-qms.com`.
   Render shows the DNS targets.
3. Namecheap → Domain → Advanced DNS:
   - `CNAME` record: host `www` → `vis-qms.onrender.com`
   - `ALIAS`/`ANAME` record: host `@` → `vis-qms.onrender.com`
     (Namecheap supports ALIAS; if not offered, use URL Redirect @ → https://www.vis-qms.com)
4. Wait for DNS (minutes–hours). Render issues the HTTPS certificate automatically.
   `ALLOWED_HOSTS` already includes vis-qms.com.

## 5. Load the documents

Option A (bulk, from your PC): run the import locally against the **production DB**:
set `DATABASE_URL` (Render → visqms-db → External Connection String) and the S3 vars
in a local `.env`, then `python manage.py import_library --source "../02_CONTROLLED-LIBRARY"`.
Option B (ongoing): upload/maintain documents one-by-one in the **Admin panel** —
this is also how you publish updates day-to-day (new revision → upload file, bump
Revision field, untick/tick *is_final*).

## 6. Users

Admin → Users → Add: username + password, assign a **group**:
- `management` — full visibility + staff access to the admin document section
  (also tick *Staff status* for them)
- `employee` — final documents only
- `auditor` — final documents only; create shortly before the audit, **deactivate after**
  (Users → untick *Active*). Every download is logged under Download logs.

## 7. Ongoing updates (your normal workflow)

Code/feature changes: edit locally with Claude/Cursor → commit → push → Render
auto-deploys. Document changes: Admin panel upload (or re-run import). Database and
files live in the cloud — nothing depends on your laptop being on.

---

## Security & compliance checklist (before giving the auditor the URL)

- [ ] DEBUG=False (default in render.yaml), HTTPS enforced (automatic)
- [ ] Strong superuser password; management users have Staff status, others do not
- [ ] **Do NOT upload seafarer personal files / GDPR data** — the portal is for QMS
      documents and records; HR Master data stays out (matches QP-04 Annex 4 GDPR policy)
- [ ] Auditor account created with expiry habit (deactivate after Surveillance 2)
- [ ] QP-03G1 (app control guideline) extended to cover the web portal at next revision
- [ ] Log the go-live in the Change Management Log (CML-2026-xx) and Risk Register
      (extends R-2026-010 IT risk)

## Costs (approx.)
Render Starter $7/mo + Postgres Basic ~$6/mo + domain ~$12/yr + storage pennies
≈ **$13–15/month**.

---

## 8. QMS Activities Calendar & Reminder System

A company-wide activity calendar sits alongside the document library — board meetings,
audits, training, reviews, and other recurring QMS activities, with due-date reminders.
Full design rationale (including the historical evidence behind the default cadences) is
in `QMS_ACTIVITY_CALENDAR_PLAN.md`.

**Where to find it:**
- Dashboard → "QMS Activities Calendar" widget (today / upcoming / overdue / this month / next deadline)
- Top nav → **QMS Calendar** (`/qms-calendar/`) — month, week, and list views, with filters
  for category, responsible person, status, entity, and ISO clause
- Top nav → **QMS Tasks** (`/qms-tasks/`) — filterable report list with summary counts
- Click any task to open its detail page (`/qms-tasks/<id>/`)

**Setting it up the first time:**
```bash
python manage.py create_qms_default_tasks
```
This is safe to re-run any time — it's idempotent (won't duplicate existing templates)
and only ever creates **Planned** tasks with forward-looking due dates; it never
fabricates completed history.

**Managing activities (Admin → QMS Document Library):**
- **Qms task templates** — the recurring rules (e.g. "Quarterly Board Meeting"). Edit
  recurrence, reminder days, responsible person, linked procedure, or untick *Is active*
  to stop generating new occurrences. Action: *Generate next task now*.
- **Qms tasks** — individual occurrences. Edit status/priority/assignees, link evidence,
  or bulk-select and *Mark selected as completed* (auto-creates the next occurrence for
  recurring tasks).

**Permissions:** management sees and edits everything. A task's responsible person or
assigned users can update their own tasks (status, notes, evidence, mark complete).
**Auditor accounts are always read-only** on QMS tasks, same as documents.

**Recurrence:** none / daily / weekly / monthly / quarterly / annually, plus a
"first Monday of the month" rule (used for the monthly branch report). "Due Soon" and
"Overdue" are computed live from the due date — there's nothing to keep in sync.

**Not yet implemented:** email reminders. The `reminder_days_before` field is in place
on every task so this is a follow-up, not a redesign, once an email backend is
configured in `settings.py`.

---

## 9. Role-based document format access control

Beyond the final/draft split above, each role also has fine-grained control over which
**file formats** it may preview or download, and whether it can see drafts,
source-editable files, obsolete documents, and internal notes. This is enforced in the
**backend** (the `download()` view and `visible_documents()`), not just hidden in the UI —
a denied request gets an HTTP 403 with "You do not have permission to access this
document format." even if someone guesses the direct URL.

**Where to configure it:** Admin → QMS Document Library → **Role access profiles**.
One row per role (`employee`, `auditor`, `internal_auditor`, `management`) — rows are
seeded automatically by migration and cannot be added/deleted, only edited.

- **Format access** — tick the file formats (PDF/DOCX/XLSX/DOC/XLS/TXT/MD/CSV) that role
  may **preview** (open in-browser/new tab) and **download** separately.
- **Visibility** — toggle whether the role can see draft documents, source-editable
  files/folders, obsolete documents, internal notes, and whether it's restricted to an
  "external auditor package" view (hides anything in an unsorted/duplicate folder or title).

**Defaults out of the box:**
| Role | Preview/Download formats | Drafts | Source-editable | Obsolete | Internal notes |
|---|---|---|---|---|---|
| Employee | PDF only | No | No | No | Yes |
| External Auditor (`auditor` group) | PDF only | No | No | No | No |
| Internal Auditor (`internal_auditor` group) | PDF, XLSX | No | No | No | Yes |
| QMS Manager / Admin (`management` group, or superuser) | All 8 formats | Yes | Yes | Yes | Yes |

**To make an employee PDF-only** (already the default): Admin → Role access profiles →
**Employee** → confirm only *PDF* is ticked under both Format access fields → Save.

**To give auditors PDF + Excel access:** decide which group they should sit in —
`auditor` (external, package-only view) or `internal_auditor` (full document set, no
package restriction) — then Admin → Role access profiles → open that role's row → tick
*XLSX* in both "Allowed preview formats" and "Allowed download formats" → Save. Takes
effect immediately for every user in that group, no redeploy needed.

**To test that drafts/source-editable files are hidden:** log in as (or create a test
account in) the `employee` or `auditor` group and browse the library — draft documents
and anything under a `source-editable`/`editable` folder should not appear in the list at
all (not just be greyed out), and non-PDF rows show a grey "Restricted format" badge
instead of preview/download icons. Superusers and the `management` group always see and
open everything, regardless of these settings — this cannot be restricted, by design, so
the admin account is never locked out.

Assigning a user to the `internal_auditor` group (Admin → Users → edit user → Groups) is
how you distinguish an internal QMS auditor (broader access) from an external
certification-body auditor (`auditor` group, package-only). Run `python manage.py
init_roles` once after upgrading to create the `internal_auditor` group if it doesn't
exist yet — safe to re-run, it's idempotent.

The AI Assistant respects the same rules: if a user asks it to open a document whose
format they're not allowed to access, it tells them to contact a QMS Manager instead of
showing the content — the assistant's document-reading tool is gated by the same
permission check as the browse/download views.
