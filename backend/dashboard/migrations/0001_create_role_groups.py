from django.db import migrations

ROLES = [
    'Owner', 'General Manager', 'Bar Manager', 'Front Office Manager',
    'Chef', 'Receptionist', 'Housekeeping', 'Security', 'Kitchen Staff',
]


def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for role in ROLES:
        Group.objects.get_or_create(name=role)


def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('auth', '__first__'),
    ]
    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]