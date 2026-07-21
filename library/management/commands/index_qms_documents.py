from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import OperationalError
from django.utils import timezone

from library.content import SUPPORTED_CONTENT_EXTS, extract_document_text
from library.models import Document


def _with_reconnect(fn, *args, **kwargs):
    """Run a DB operation, and if the connection went stale (common on a
    long-running command where each document's file fetch/extract can take
    several seconds — plenty of idle time for a remote Postgres connection
    to drop), close it and retry once so the next query opens a fresh one."""
    try:
        return fn(*args, **kwargs)
    except OperationalError:
        connection.close()
        return fn(*args, **kwargs)


class Command(BaseCommand):
    help = ("Extract and store searchable text content for QMS library documents, "
            "powering 'Search inside file content' on the Browse Documents page.")

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                             help="Re-extract every document, ignoring the skip-unchanged check.")

    def handle(self, *args, **options):
        # Some document titles contain characters the Windows console's default
        # codepage can't display (e.g. non-Latin scripts); degrade to a safe
        # escaped form there instead of crashing the whole run over a print.
        try:
            self.stdout._out.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

        force = options["force"]
        # Resolve the pk list upfront rather than keeping a server-side
        # cursor open for the whole run (see _with_reconnect above).
        pks = list(Document.objects.order_by("pk").values_list("pk", flat=True))
        total = len(pks)
        indexed = skipped = unsupported = failed = empty = 0

        for pk in pks:
            document = _with_reconnect(Document.objects.get, pk=pk)

            # Skip-unchanged: content_indexed_at is stamped every run (even
            # for unsupported/failed files, see below), and updated_at bumps
            # whenever the Document row (incl. a replaced file) is saved —
            # so "already indexed since the last change" is a safe skip.
            if not force and document.content_indexed_at and document.content_indexed_at >= document.updated_at:
                skipped += 1
                continue

            name = document.file.name.lower()
            if not name.endswith(SUPPORTED_CONTENT_EXTS):
                unsupported += 1
                self.stdout.write(f"  unsupported format, skipping content: {document.pk} {document.title}")
                document.content_indexed_at = timezone.now()
                _with_reconnect(document.save, update_fields=["content_indexed_at"])
                continue

            text = extract_document_text(document)
            if text is None:
                failed += 1
                self.stdout.write(self.style.WARNING(
                    f"  could not read file, skipping content: {document.pk} {document.title}"))
                document.content_indexed_at = timezone.now()
                _with_reconnect(document.save, update_fields=["content_indexed_at"])
                continue

            if text == "":
                empty += 1

            document.content_text = text
            document.content_indexed_at = timezone.now()
            _with_reconnect(document.save, update_fields=["content_text", "content_indexed_at"])
            indexed += 1

        self.stdout.write(self.style.SUCCESS(
            f"Indexed {indexed} document(s) ({empty} had no extractable text) — "
            f"{skipped} unchanged/skipped, {unsupported} unsupported format, "
            f"{failed} unreadable — out of {total} total."
        ))
