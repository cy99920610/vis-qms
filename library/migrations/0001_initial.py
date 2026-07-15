# Initial migration — Document + DownloadLog
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("code", models.CharField(blank=True, help_text="e.g. QP-03, OP-01, QM-01", max_length=40)),
                ("revision", models.CharField(blank=True, max_length=20)),
                ("section", models.CharField(choices=[("00_REGISTER", "00 Register"), ("01_ISO-9001-QMS", "01 ISO 9001 QMS"), ("02_ISM-SMS", "02 ISM SMS"), ("03_RECORDS", "03 Records"), ("04_ENTITY-EVIDENCE", "04 Entity Evidence"), ("05_CERTIFICATES", "05 Certificates")], default="01_ISO-9001-QMS", max_length=40)),
                ("folder", models.CharField(blank=True, help_text="Location within the library structure", max_length=300)),
                ("issue_date", models.DateField(blank=True, null=True)),
                ("file", models.FileField(upload_to="library/%Y/")),
                ("is_final", models.BooleanField(default=True, help_text="Final approved document (visible to auditor/employees). Untick for drafts/working versions.")),
                ("notes", models.CharField(blank=True, max_length=400)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["section", "folder", "title"]},
        ),
        migrations.CreateModel(
            name="DownloadLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("at", models.DateTimeField(auto_now_add=True)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="downloads", to="library.document")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-at"]},
        ),
        migrations.AddIndex(model_name="document", index=models.Index(fields=["code"], name="library_doc_code_idx")),
        migrations.AddIndex(model_name="document", index=models.Index(fields=["section"], name="library_doc_section_idx")),
    ]
