from django.db import migrations

def create_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='HR Manager')

def remove_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='HR Manager').delete()

class Migration(migrations.Migration):
    dependencies = [('dashboard', '0007_add_room_manager_group')]  # adjust to your latest dashboard migration
    operations = [migrations.RunPython(create_group, remove_group)]