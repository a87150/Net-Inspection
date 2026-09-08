from django.db import models
from django.core.exceptions import ValidationError


class IssueSeverityPolicy(models.Model):
    id = models.BigAutoField(primary_key=True)
    project = models.CharField(max_length=16, unique=True, default='computers', choices=(
        ('computers', 'PC 日志分析'), ('networks', '网络设备巡检'), ('servers', '服务器巡检'), ('monitors', '安防设备巡检')))
    overrides = models.JSONField(default=dict, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '问题严重等级设置'
        verbose_name_plural = verbose_name

    def clean(self):
        from net.inspections.issues import PROJECT_RULES, METRIC_DEFAULTS
        rules = PROJECT_RULES.get(self.project, {})
        allowed = set(rules) | {f'missing.{key}' for key in rules}
        if not isinstance(self.overrides, dict) or any(
            key not in allowed or value not in {'info', 'warning', 'critical'}
            for key, value in self.overrides.items()
        ):
            raise ValidationError({'overrides': '问题规则或等级无效。'})
        import math
        if not isinstance(self.thresholds, dict) or any(
            key not in METRIC_DEFAULTS.get(self.project, {}) or isinstance(value, bool)
            or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 0 < value <= (200 if key == 'temperature' else 100)
            for key, value in self.thresholds.items()
        ):
            raise ValidationError({'thresholds': '指标阈值无效。'})
