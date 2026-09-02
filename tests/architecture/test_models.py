from django.test import TestCase

from net import models as net_models


Computer = net_models.Computer
ComputerAnalysis = getattr(net_models, 'ComputerAnalysis', None)
Error_Computer = getattr(net_models, 'Error_Computer', None)
Error_Monitor = getattr(net_models, 'Error_Monitor', None)
Error_Network_Device = getattr(net_models, 'Error_Network_Device', None)
Error_Server = getattr(net_models, 'Error_Server', None)
Monitor_Inspection = net_models.Monitor_Inspection
Network_Device_Inspection = net_models.Network_Device_Inspection
Server_Inspection = net_models.Server_Inspection


class StaticAndDynamicModelBoundaryTests(TestCase):
    def test_computer_static_model_has_no_live_resource_fields(self):
        names = {field.name for field in Computer._meta.fields}
        self.assertFalse({'cpu_usage', 'memory_usage', 'cpu_temperature'} & names)

    def test_each_dynamic_record_has_a_target_and_timestamp(self):
        for model, target_name in (
            (ComputerAnalysis, 'computer'),
            (Network_Device_Inspection, 'device'),
            (Server_Inspection, 'server'),
            (Monitor_Inspection, 'monitor'),
        ):
            self.assertIsNotNone(model, 'dynamic record model is not publicly exported')
            self.assertIsNotNone(model._meta.get_field(target_name))
            self.assertIsNotNone(model._meta.get_field('created_at'))

    def test_each_dynamic_record_has_shared_status_timing_and_result_fields(self):
        for model in (
            ComputerAnalysis,
            Network_Device_Inspection,
            Server_Inspection,
            Monitor_Inspection,
        ):
            self.assertIsNotNone(model, 'dynamic record model is not publicly exported')
            names = {field.name for field in model._meta.fields}
            self.assertTrue(
                {'status', 'started_at', 'finished_at', 'summary', 'details'} <= names,
                model.__name__,
            )

    def test_each_error_model_links_to_its_dynamic_record(self):
        for error_model, record_model in (
            (Error_Computer, ComputerAnalysis),
            (Error_Network_Device, Network_Device_Inspection),
            (Error_Server, Server_Inspection),
            (Error_Monitor, Monitor_Inspection),
        ):
            self.assertIsNotNone(error_model, 'error model is not publicly exported')
            inspection_field = error_model._meta.get_field('inspection')
            self.assertIs(inspection_field.remote_field.model, record_model)
