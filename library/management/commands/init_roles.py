import os
from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, User, Permission


class Command(BaseCommand):
    help = "Create the VIS-QMS groups (management, employee, auditor, internal_auditor) and the superuser from env vars."

    def handle(self, *args, **options):
        mgmt, _ = Group.objects.get_or_create(name="management")
        Group.objects.get_or_create(name="employee")
        Group.objects.get_or_create(name="auditor")
        Group.objects.get_or_create(name="internal_auditor")
        # management may maintain documents via the admin
        perms = Permission.objects.filter(content_type__app_label="library")
        mgmt.permissions.set(perms)
        self.stdout.write("Groups ready: management (admin doc rights), employee, auditor, internal_auditor")

        u = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        p = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        e = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
        if u and p and not User.objects.filter(username=u).exists():
            User.objects.create_superuser(u, e, p)
            self.stdout.write(f"Superuser '{u}' created")
