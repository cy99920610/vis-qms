from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0012_populate_entity_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='content_text',
            field=models.TextField(blank=True, help_text='Extracted searchable text, populated by `manage.py index_qms_documents`. Not editable here.'),
        ),
        migrations.AddField(
            model_name='document',
            name='content_indexed_at',
            field=models.DateTimeField(blank=True, help_text='When content_text was last (re)extracted. Blank means never indexed.', null=True),
        ),
    ]
