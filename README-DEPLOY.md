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
