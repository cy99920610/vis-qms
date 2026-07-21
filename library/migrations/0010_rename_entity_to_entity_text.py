# Pure column rename — preserves every existing value. The old free-text
# entity fields are kept (not dropped) as a safety net; 0011/0012 add the
# new FK field and populate it from these renamed columns.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0009_qmsentity'),
    ]

    operations = [
        migrations.RenameField(
            model_name='qmstask',
            old_name='entity',
            new_name='entity_text',
        ),
        migrations.RenameField(
            model_name='qmstasktemplate',
            old_name='default_entity',
            new_name='default_entity_text',
        ),
        migrations.AlterField(
            model_name='qmstask',
            name='entity_text',
            field=models.CharField(
                blank=True, help_text='Legacy free-text value, kept for reference only — use entity below.', max_length=100),
        ),
        migrations.AlterField(
            model_name='qmstasktemplate',
            name='default_entity_text',
            field=models.CharField(
                blank=True, help_text='Legacy free-text value, kept for reference only — use default_entity below.', max_length=100),
        ),
    ]
