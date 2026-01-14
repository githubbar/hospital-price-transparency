from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('prices', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql='CREATE FULLTEXT INDEX idx_fulltext_search ON hospital_prices (description, code_1, code_2, code_3)',
            reverse_sql='DROP INDEX idx_fulltext_search ON hospital_prices'
        ),
    ]
