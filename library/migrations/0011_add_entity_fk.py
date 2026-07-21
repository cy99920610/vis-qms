import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0010_rename_entity_to_entity_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='qmstask',
            name='entity',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='tasks', to='library.qmsentity'),
        ),
        migrations.AddField(
            model_name='qmstasktemplate',
            name='default_entity',
            field=models.ForeignKey(blank=True, help_text='Blank means Group-wide', null=True,
                                     on_delete=django.db.models.deletion.SET_NULL,
                                     related_name='templates', to='library.qmsentity'),
        ),
    ]
