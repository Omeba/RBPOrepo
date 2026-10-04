from django.db import migrations


def initialize_lock(apps, schema_editor):
    apps.get_model('forum', 'ContentLock').objects.create(pk=1)


class Migration(migrations.Migration):
    dependencies = [('forum', '0001_initial')]
    operations = [migrations.RunPython(initialize_lock, migrations.RunPython.noop)]
