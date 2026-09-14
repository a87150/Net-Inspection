from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('net', '0038_people_phone')]

    operations = [
        migrations.AddField(
            model_name='domain_controller_config', name='inactive_days',
            field=models.PositiveIntegerField('未登录天数', default=60,
                                              validators=[MinValueValidator(1), MaxValueValidator(36500)]),
        ),
    ]
