from rest_framework import serializers
from .models import ComputerAnalysis, People


class ComputerInspectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComputerAnalysis
        fields = [
            'computer', 'log_file', 'status', 'started_at', 'finished_at',
            'summary', 'details', 'analysis_items', 'exceptions', 'created_at',
        ]
        read_only_fields = ['created_at']


class PeopleSerializer(serializers.ModelSerializer):
    class Meta:
        model = People
        fields = ['name', 'employee_id', 'email', 'department', 'leader', 'is_active']
