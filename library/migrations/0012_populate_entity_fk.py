# Data migration: seeds the suggested master entity list, then links every
# existing QMSTask/QMSTaskTemplate to a QmsEntity based on its old free-text
# value. A legacy text value that doesn't exactly match one of the seeded
# names gets its OWN QmsEntity created with that exact original text as the
# name — never silently merged/guessed — so no historical data is lost or
# altered; duplicates/typos surfaced this way can be cleaned up deliberately
# afterward in the QMS Entities admin.
from django.db import migrations

SEED_ENTITIES = [
    dict(name="VIS-Recruit Cyprus", entity_type="branch"),
    dict(name="VIS-Recruit Ukraine", entity_type="branch"),
    dict(name="VIS-Recruit Asia", entity_type="branch"),
    dict(name="VIS-Recruit.com", entity_type="company"),
    dict(name="Nepal Office", entity_type="liaison_office"),
    dict(name="Kyrgyzstan Office", entity_type="liaison_office"),
    dict(name="Indonesia Partner", entity_type="partner"),
    dict(name="Amatus Crew e.K.", entity_type="partner"),
    dict(name="Quantihouse Technologies Ltd", entity_type="partner"),
    dict(name="VIS Rising Stars", entity_type="project"),
]


def populate(apps, schema_editor):
    QmsEntity = apps.get_model("library", "QmsEntity")
    QMSTask = apps.get_model("library", "QMSTask")
    QMSTaskTemplate = apps.get_model("library", "QMSTaskTemplate")

    for spec in SEED_ENTITIES:
        QmsEntity.objects.get_or_create(name=spec["name"], defaults={"entity_type": spec["entity_type"]})

    def entity_for(text):
        text = (text or "").strip()
        if not text:
            return None
        entity, _ = QmsEntity.objects.get_or_create(name=text, defaults={"entity_type": "other"})
        return entity

    for task in QMSTask.objects.exclude(entity_text="").iterator():
        task.entity = entity_for(task.entity_text)
        task.save(update_fields=["entity"])

    for template in QMSTaskTemplate.objects.exclude(default_entity_text="").iterator():
        template.default_entity = entity_for(template.default_entity_text)
        template.save(update_fields=["default_entity"])


def unpopulate(apps, schema_editor):
    QMSTask = apps.get_model("library", "QMSTask")
    QMSTaskTemplate = apps.get_model("library", "QMSTaskTemplate")
    QMSTask.objects.update(entity=None)
    QMSTaskTemplate.objects.update(default_entity=None)
    # Seeded/derived QmsEntity rows are left in place on reverse — deleting
    # them could remove entities an admin has since started using elsewhere.


class Migration(migrations.Migration):

    dependencies = [
        ('library', '0011_add_entity_fk'),
    ]

    operations = [
        migrations.RunPython(populate, unpopulate),
    ]
