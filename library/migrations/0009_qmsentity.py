from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0008_seed_role_access_profiles'),
    ]

    operations = [
        migrations.CreateModel(
            name='QmsEntity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, unique=True)),
                ('short_name', models.CharField(blank=True, max_length=50)),
                ('entity_type', models.CharField(choices=[('company', 'Company'), ('branch', 'Branch'), ('liaison_office', 'Liaison Office'), ('partner', 'Partner'), ('project', 'Project'), ('other', 'Other')], default='branch', max_length=20)),
                ('country', models.CharField(blank=True, max_length=80)),
                ('active', models.BooleanField(default=True, help_text='Untick to hide from new task/template dropdowns without deleting history.')),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'QMS entity',
                'verbose_name_plural': 'QMS entities',
                'ordering': ['name'],
            },
        ),
    ]
