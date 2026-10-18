from django.db import migrations
from django.db.models import F


def backfill_completed_at(apps, schema_editor):
    """Purane Completed tasks ke paas completed_at nahi tha. Asli date maloom nahi,
    isliye created_at rakhte hain, taaki analytics mein ye tasks 'unknown' na rahen."""
    Task = apps.get_model('core', 'Task')
    Task.objects.filter(status='Completed', completed_at__isnull=True).update(completed_at=F('created_at'))


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_projects_labels_comments_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_completed_at, migrations.RunPython.noop),
    ]
