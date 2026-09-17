# C:\Users\JOEMAN\OneDrive\Documents\Restaurant Management System\hotel-management-system\backend\dashboard\migrations\0007_add_room_manager_group.py

from django.db import migrations

def create_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.get_or_create(name='Room Manager')

def remove_group(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name='Room Manager').delete()

class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0006_payment_amount_tendered'),  # <-- Match the exact file name of your 0006 migration (without .py)
    ]

    operations = [
        migrations.RunPython(create_group, remove_group),
    ]