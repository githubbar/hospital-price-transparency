from django.db import models

class HospitalPrices(models.Model):
    id = models.AutoField(primary_key=True)
    description = models.CharField(max_length=500, blank=True, null=True, db_index=True)
    code_1 = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    code_1_type = models.TextField(blank=True, null=True)
    code_2 = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    code_2_type = models.TextField(blank=True, null=True)
    code_3 = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    code_3_type = models.TextField(blank=True, null=True)
    code_4 = models.TextField(blank=True, null=True)
    code_4_type = models.TextField(blank=True, null=True)
    code_5 = models.TextField(blank=True, null=True)
    code_5_type = models.TextField(blank=True, null=True)
    code_6 = models.TextField(blank=True, null=True)
    code_6_type = models.TextField(blank=True, null=True)
    setting = models.TextField(blank=True, null=True)
    drug_unit_of_measurement = models.TextField(blank=True, null=True)
    drug_type_of_measurement = models.TextField(blank=True, null=True)
    standard_charge_gross = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_discounted_cash = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    payer_name = models.TextField(blank=True, null=True)
    plan_name = models.TextField(blank=True, null=True)
    modifiers = models.TextField(blank=True, null=True)
    standard_charge_negotiated_dollar = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_negotiated_percentage = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_negotiated_algorithm = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    estimated_amount = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_min = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_max = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    standard_charge_methodology = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    additional_generic_notes = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'hospital_prices'
