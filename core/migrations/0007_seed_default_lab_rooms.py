import json
from django.db import migrations


def seed_default_labs(apps, schema_editor):
    LabRoomLayout = apps.get_model('core', 'LabRoomLayout')
    Classroom = apps.get_model('core', 'Classroom')

    machine_labels = {}
    num = 1
    for c in range(1, 8):
        for r in range(1, 7):
            machine_labels[f'{r}_{c}'] = f'{num:02d}'
            num += 1

    default_cfg = json.dumps({
        'machine_labels': machine_labels,
        'disabled_seats': [],
        'row_order': 'bottom_to_top',
        'auto_direction': 'col_front_to_back',
    })

    labs = []
    for name in ('ห้อง 134', 'ห้อง 135', 'ห้อง 136'):
        lab, created = LabRoomLayout.objects.get_or_create(
            name=name,
            defaults={
                'seat_rows': 6,
                'seat_cols': 7,
                'board_position': 'bottom',
                'layout_config': default_cfg,
            },
        )
        if not created and not (lab.layout_config or '').strip().startswith('{'):
            lab.layout_config = default_cfg
            lab.save(update_fields=['layout_config'])
        labs.append(lab)

    default_lab = labs[0]
    Classroom.objects.filter(lab_room__isnull=True).update(
        lab_room=default_lab,
        seat_rows=default_lab.seat_rows,
        seat_cols=default_lab.seat_cols,
        board_position=default_lab.board_position,
    )


def unseed_default_labs(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_labroomlayout_classroom_lab_room'),
    ]

    operations = [
        migrations.RunPython(seed_default_labs, reverse_code=unseed_default_labs),
    ]
