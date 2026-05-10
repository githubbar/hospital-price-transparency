from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('prices', '0002_add_fulltext_index'),
    ]

    operations = [
        migrations.DeleteModel(
            name='HospitalPrices',
        ),
    ]
