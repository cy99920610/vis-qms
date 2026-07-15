"""Import the local controlled library into the web database.

Usage (from django-visqms/, with the repo laid out as in the VIS-QMS project):
    python manage.py import_library --source "../02_CONTROLLED-LIBRARY" [--sections 01_ISO-9001-QMS 05_CERTIFICATES]

Copies files into MEDIA storage (local or S3) and creates Document rows.
Drafts / editable sources / obsolete areas are skipped or marked non-final,
matching the VIS-QMS access rules.
"""
import os, re, datetime
from pathlib import Path
from django.core.files import File
from django.core.management.base import BaseCommand
from library.models import Document

EXCLUDE_DIRS = {"90_OBSOLETE", "91_DUPLICATE-REVIEW", "99_UNSORTED", "99_UNSORTED-RAW-EXTRA",
                "99_WORKING-DRAFTS", "_source-editable", "for-distribution", "for-distribution-MC",
                "mc-masters", "answer-keys", "multiple-choice", "study-guides", "personalized", "superseded"}
EXCLUDE_EXT = {".tmp", ".db", ".ini", ".zip", ".lnk"}


def doc_code(name):
    m = re.match(r"\s*((?:QM|QP|OP|JI)\s?-?\s?\d+[\w\u0400-\u04FF]*)", name)
    return re.sub(r"\s", "", m.group(1)) if m else ""


def revision(name):
    m = re.search(r"(?:\bR|Rev\.?\s?|Revision\s?)(\d+)", name)
    if m:
        return m.group(1)
    return "DRAFT" if "DRAFT" in name.upper() else ""


class Command(BaseCommand):
    help = "Import controlled-library files as Documents"

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)
        parser.add_argument("--sections", nargs="*", default=None)

    def handle(self, *args, **o):
        src = Path(o["source"]).resolve()
        created = skipped = 0
        for base, dirs, files in os.walk(src):
            dirs[:] = [d for d in sorted(dirs) if d not in EXCLUDE_DIRS]
            for fn in sorted(files):
                if fn.startswith(("~$", ".~")) or Path(fn).suffix.lower() in EXCLUDE_EXT:
                    continue
                p = Path(base) / fn
                rel = p.relative_to(src)
                section = rel.parts[0]
                if o["sections"] and section not in o["sections"]:
                    continue
                folder = str(rel.parent)
                if Document.objects.filter(title=fn, folder=folder).exists():
                    skipped += 1
                    continue
                is_final = "DRAFT" not in fn.upper()
                d = Document(title=fn, code=doc_code(fn), revision=revision(fn),
                             section=section if section in dict(Document._meta.get_field("section").choices) else "01_ISO-9001-QMS",
                             folder=folder, is_final=is_final,
                             issue_date=datetime.date.fromtimestamp(p.stat().st_mtime))
                with p.open("rb") as fh:
                    d.file.save(fn, File(fh), save=True)
                created += 1
                if created % 50 == 0:
                    self.stdout.write(f"  {created} imported...")
        self.stdout.write(self.style.SUCCESS(f"Imported {created}, skipped existing {skipped}"))
