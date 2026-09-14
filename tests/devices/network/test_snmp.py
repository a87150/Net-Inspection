import asyncio
import json
import socket
from unittest.mock import AsyncMock, patch, sentinel

from django.test import SimpleTestCase
from pysnmp.proto import errind, rfc1905
from pysnmp.proto.rfc1902 import Integer32, ObjectIdentifier, OctetString

import net.devices.network.snmp as snmp_module

from net.devices.network.snmp import (
    ENT_SENSOR_PRECISION,
    ENT_SENSOR_SCALE,
    ENT_SENSOR_STATUS,
    ENT_SENSOR_TYPE,
    ENT_SENSOR_VALUE,
    HR_MEMORY_SIZE,
    HR_STORAGE_ALLOCATION_UNITS,
    HR_STORAGE_SIZE,
    HR_STORAGE_TYPE,
    HR_STORAGE_USED,
    HR_PROCESSOR_LOAD,
    IF_ADMIN_STATUS,
    IF_DESCR,
    IF_HC_IN,
    IF_HC_OUT,
    IF_HIGH_SPEED,
    IF_IN_DISCARDS,
    IF_IN_ERRORS,
    IF_MAC,
    IF_NAME,
    IF_OPER_STATUS,
    IF_OUT_DISCARDS,
    IF_OUT_ERRORS,
    QBRIDGE_VLAN_NAME,
    SNMP_ITEMS,
    SYS_DESCR,
    SYS_NAME,
    SYS_UPTIME,
    VENDOR_OIDS,
    PySnmpSession,
    SnmpQueryError,
    collect_network_snmp,
    parse_snmp_snapshot,
)
from net.models import Network_Device


PHYSICAL_MEMORY = "1.3.6.1.2.1.25.2.1.2"


class MemorySession:
    def __init__(self, scalars=None, tables=None, error=None):
        self.scalars = scalars or {}
        self.tables = tables or {}
        self.error = error
        self.get_queries = []
        self.walk_queries = []

    async def get(self, oid):
        self.get_queries.append(oid)
        if self.error:
            raise self.error
        return self.scalars.get(oid)

    async def walk(self, oid):
        self.walk_queries.append(oid)
        if self.error:
            raise self.error
        return self.tables.get(oid, [])

    async def close(self):
        return None


class MemoryEngine:
    def __init__(self):
        self.closed = False

    def close_dispatcher(self):
        self.closed = True


class PrettyStatus:
    def __init__(self, text):
        self.text = text

    def __bool__(self):
        return True

    def prettyPrint(self):
        return self.text


class NetworkSnmpCollectionTests(SimpleTestCase):
    def setUp(self):
        self.device = Network_Device(
            ip="192.0.2.20",
            vendor="generic",
            connection_type="snmp",
            snmp_version="v2c",
            snmp_community="private-community",
            snmp_auth_password="auth-secret",
            snmp_priv_password="priv-secret",
        )

    def collect(self, session, selected_items):
        return collect_network_snmp(
            self.device,
            selected_items=selected_items,
            session_factory=lambda *_: session,
        )

    def test_standard_mibs_produce_device_and_interface_details(self):
        session = MemorySession(
            scalars={
                SYS_NAME: "core-sw-1",
                SYS_DESCR: "Example Switch",
                "1.3.6.1.2.1.1.2.0": "1.3.6.1.4.1.9.1.1208",
                SYS_UPTIME: 12345,
            },
            tables={
                IF_NAME: [("1", "Gi0/1")],
                IF_DESCR: [("1", "uplink")],
                IF_MAC: [("1", bytes.fromhex("001122334455"))],
                IF_ADMIN_STATUS: [("1", 1)],
                IF_OPER_STATUS: [("1", 1)],
                IF_HIGH_SPEED: [("1", 1000)],
                IF_HC_IN: [("1", 1024)],
                IF_HC_OUT: [("1", 2048)],
                IF_IN_ERRORS: [("1", 2)],
                IF_OUT_ERRORS: [("1", 3)],
                IF_IN_DISCARDS: [("1", 4)],
                IF_OUT_DISCARDS: [("1", 5)],
            },
        )

        result = self.collect(session, ["device_info", "interface_status"])

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["device_info"]["system_name"], "core-sw-1")
        self.assertEqual(result.data["device_info"]["description"], "Example Switch")
        self.assertEqual(
            result.data["device_info"].get("object_id"),
            "1.3.6.1.4.1.9.1.1208",
        )
        self.assertEqual(result.data["device_info"]["uptime_ticks"], 12345)
        self.assertEqual(
            result.data["interface_status"]["interfaces"][0],
            {
                "index": "1",
                "name": "Gi0/1",
                "description": "uplink",
                "mac": "00:11:22:33:44:55",
                "admin_status": "up",
                "oper_status": "up",
                "speed_mbps": 1000,
                "in_octets": 1024,
                "out_octets": 2048,
                "in_errors": 2,
                "out_errors": 3,
                "in_discards": 4,
                "out_discards": 5,
            },
        )
        self.assertIn(IF_HC_IN, result.raw["interface_status"])
        json.dumps(result.raw)

    def test_missing_oid_marks_only_that_item_missing(self):
        result = self.collect(
            MemorySession(scalars={SYS_NAME: "sw"}),
            ["device_info", "temperature"],
        )

        self.assertEqual(result.status, "partial")
        self.assertIn("device_info", result.data)
        self.assertNotIn("temperature", result.data)

    def test_host_resources_cpu_load_is_averaged(self):
        result = self.collect(
            MemorySession(tables={HR_PROCESSOR_LOAD: [("1", 10), ("2", 30)]}),
            ["cpu"],
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["cpu"], {"usage_percent": 20.0})

    def test_host_resources_memory_uses_physical_storage_allocation_units(self):
        result = self.collect(
            MemorySession(
                scalars={HR_MEMORY_SIZE: 2048},
                tables={
                    HR_STORAGE_TYPE: [("7", PHYSICAL_MEMORY)],
                    HR_STORAGE_ALLOCATION_UNITS: [("7", 1024)],
                    HR_STORAGE_SIZE: [("7", 2048)],
                    HR_STORAGE_USED: [("7", 512)],
                },
            ),
            ["memory"],
        )

        self.assertEqual(
            result.data["memory"],
            {
                "total_bytes": 2097152,
                "used_bytes": 524288,
                "usage_percent": 25.0,
            },
        )

    def test_host_resources_memory_aggregates_all_valid_physical_rows(self):
        result = self.collect(
            MemorySession(
                tables={
                    HR_STORAGE_TYPE: [
                        ("7", PHYSICAL_MEMORY),
                        ("8", PHYSICAL_MEMORY),
                        ("9", "1.3.6.1.2.1.25.2.1.4"),
                    ],
                    HR_STORAGE_ALLOCATION_UNITS: [("7", 1024), ("8", 2048), ("9", 1)],
                    HR_STORAGE_SIZE: [("7", 1000), ("8", 500), ("9", 9999)],
                    HR_STORAGE_USED: [("7", 250), ("8", 100), ("9", 9999)],
                },
            ),
            ["memory"],
        )

        self.assertEqual(result.data["memory"], {
            "total_bytes": 2048000,
            "used_bytes": 460800,
            "usage_percent": 22.5,
        })

    def test_host_resources_memory_rejects_rows_with_used_above_total(self):
        result = self.collect(
            MemorySession(
                tables={
                    HR_STORAGE_TYPE: [("7", PHYSICAL_MEMORY)],
                    HR_STORAGE_ALLOCATION_UNITS: [("7", 1024)],
                    HR_STORAGE_SIZE: [("7", 100)],
                    HR_STORAGE_USED: [("7", 101)],
                },
            ),
            ["memory"],
        )

        self.assertEqual(result.status, "failed")
        self.assertNotIn("memory", result.data)

    def test_entity_sensor_temperature_applies_scale_and_precision(self):
        result = self.collect(
            MemorySession(
                tables={
                    ENT_SENSOR_TYPE: [("10", 8)],
                    ENT_SENSOR_SCALE: [("10", 9)],
                    ENT_SENSOR_PRECISION: [("10", 1)],
                    ENT_SENSOR_VALUE: [("10", 425)],
                    ENT_SENSOR_STATUS: [("10", 1)],
                }
            ),
            ["temperature"],
        )

        self.assertEqual(result.data["temperature"], {"values_celsius": [42.5]})

    def test_invalid_entity_sensor_numbers_only_leave_temperature_missing(self):
        for scale, precision, raw_value in (
            (10**6, 0, 42),
            (9, 0, float("inf")),
            (9, float("nan"), 42),
            (9, 0, 10**100),
        ):
            with self.subTest(scale=scale, precision=precision, raw_value=raw_value):
                result = self.collect(
                    MemorySession(
                        tables={
                            HR_PROCESSOR_LOAD: [("1", 25)],
                            ENT_SENSOR_TYPE: [("10", 8)],
                            ENT_SENSOR_SCALE: [("10", scale)],
                            ENT_SENSOR_PRECISION: [("10", precision)],
                            ENT_SENSOR_VALUE: [("10", raw_value)],
                            ENT_SENSOR_STATUS: [("10", 1)],
                        },
                    ),
                    ["cpu", "temperature"],
                )

                self.assertEqual(result.status, "partial")
                self.assertEqual(result.data["cpu"], {"usage_percent": 25.0})
                self.assertNotIn("temperature", result.data)

    def test_q_bridge_vlan_names_are_normalized_by_vlan_id(self):
        result = self.collect(
            MemorySession(tables={QBRIDGE_VLAN_NAME: [("10", "users"), ("20", "voice")]}),
            ["vlan_status"],
        )

        self.assertEqual(
            result.data["vlan_status"],
            {"vlans": [{"vlan_id": 10, "name": "users"}, {"vlan_id": 20, "name": "voice"}]},
        )

    def test_vendor_registry_is_complete_and_first_valid_candidate_wins(self):
        self.assertEqual(set(VENDOR_OIDS), {"cisco", "huawei", "h3c", "ruijie"})
        first, second = VENDOR_OIDS["cisco"]["cpu"][:2]
        self.device.vendor = "Cisco Systems"
        session = MemorySession(scalars={first: 17, second: 99})

        result = self.collect(session, ["cpu"])

        self.assertEqual(result.data["cpu"], {"usage_percent": 17.0})
        self.assertIn(first, session.get_queries)
        self.assertNotIn(second, session.get_queries)

    def test_invalid_cpu_candidate_does_not_block_next_candidate(self):
        first, second = VENDOR_OIDS["cisco"]["cpu"][:2]
        self.device.vendor = "cisco"
        session = MemorySession(scalars={first: 150, second: 23})

        result = self.collect(session, ["cpu"])

        self.assertEqual(result.data["cpu"], {"usage_percent": 23.0})
        self.assertEqual(session.get_queries, [first, second])
        self.assertNotIn(HR_PROCESSOR_LOAD, session.walk_queries)

    def test_invalid_vendor_memory_values_use_host_resources_fallback(self):
        total_oid = VENDOR_OIDS["cisco"]["memory_total"][0]
        used_oid = VENDOR_OIDS["cisco"]["memory_used"][0]
        self.device.vendor = "cisco"
        session = MemorySession(
            scalars={total_oid: 0, used_oid: -1},
            tables={
                HR_STORAGE_TYPE: [("7", PHYSICAL_MEMORY)],
                HR_STORAGE_ALLOCATION_UNITS: [("7", 1024)],
                HR_STORAGE_SIZE: [("7", 1000)],
                HR_STORAGE_USED: [("7", 250)],
            },
        )

        result = self.collect(session, ["memory"])

        self.assertEqual(result.data["memory"]["usage_percent"], 25.0)
        self.assertIn(HR_STORAGE_TYPE, session.walk_queries)

    def test_vendor_memory_used_above_total_uses_host_resources_fallback(self):
        total_oid = VENDOR_OIDS["cisco"]["memory_total"][0]
        used_oid = VENDOR_OIDS["cisco"]["memory_used"][0]
        self.device.vendor = "cisco"
        session = MemorySession(
            scalars={total_oid: 100, used_oid: 200},
            tables={
                HR_STORAGE_TYPE: [("7", PHYSICAL_MEMORY)],
                HR_STORAGE_ALLOCATION_UNITS: [("7", 1)],
                HR_STORAGE_SIZE: [("7", 100)],
                HR_STORAGE_USED: [("7", 20)],
            },
        )

        result = self.collect(session, ["memory"])

        self.assertEqual(result.data["memory"]["usage_percent"], 20.0)
        self.assertIn(HR_STORAGE_USED, session.walk_queries)

    def test_parser_uses_later_vendor_used_candidate_when_first_exceeds_total(self):
        total_oid = "1.3.6.1.4.1.9.99.1.0"
        invalid_used_oid = "1.3.6.1.4.1.9.99.2.0"
        valid_used_oid = "1.3.6.1.4.1.9.99.3.0"
        snapshot = {
            "scalars": {
                total_oid: 100,
                invalid_used_oid: 150,
                valid_used_oid: 40,
            },
            "tables": {},
        }

        with patch.dict(
            VENDOR_OIDS["cisco"],
            {
                "memory_total": (total_oid,),
                "memory_used": (invalid_used_oid, valid_used_oid),
            },
        ):
            data, _, completed = parse_snmp_snapshot(snapshot, ["memory"], "cisco")

        self.assertEqual(
            data["memory"],
            {"total_bytes": 100, "used_bytes": 40, "usage_percent": 40.0},
        )
        self.assertEqual(completed, {"memory"})

    def test_invalid_vendor_temperature_uses_entity_sensor_fallback(self):
        temperature_oid = VENDOR_OIDS["cisco"]["temperature"][0]
        self.device.vendor = "cisco"
        session = MemorySession(
            scalars={temperature_oid: "not-a-temperature"},
            tables={
                ENT_SENSOR_TYPE: [("10", 8)],
                ENT_SENSOR_SCALE: [("10", 9)],
                ENT_SENSOR_PRECISION: [("10", 0)],
                ENT_SENSOR_VALUE: [("10", 41)],
                ENT_SENSOR_STATUS: [("10", 1)],
            },
        )

        result = self.collect(session, ["temperature"])

        self.assertEqual(result.data["temperature"], {"values_celsius": [41]})
        self.assertIn(ENT_SENSOR_VALUE, session.walk_queries)

    def test_interface_counters_without_identity_or_valid_status_are_incomplete(self):
        result = self.collect(
            MemorySession(
                tables={
                    IF_HC_IN: [("9", 1234)],
                    IF_OPER_STATUS: [("9", 99)],
                }
            ),
            ["interface_status"],
        )

        self.assertEqual(result.status, "failed")
        self.assertNotIn("interface_status", result.data)

    def test_blank_interface_identity_with_only_counters_is_incomplete(self):
        result = self.collect(
            MemorySession(tables={IF_NAME: [("9", "   ")], IF_HC_IN: [("9", 1234)]}),
            ["interface_status"],
        )

        self.assertEqual(result.status, "failed")
        self.assertNotIn("interface_status", result.data)

    def test_query_failures_have_fixed_categories_and_never_expose_secrets(self):
        expected = {
            "authentication": (True, "SNMP authentication failed"),
            "timeout": (False, "SNMP request timed out"),
            "unreachable": (False, "SNMP target unreachable"),
            "dependency": (True, "SNMP dependency unavailable"),
            "invalid_configuration": (True, "SNMP invalid configuration"),
            "response": (True, "SNMP response error"),
        }
        for category, (reachable, message) in expected.items():
            with self.subTest(category=category):
                result = self.collect(
                    MemorySession(error=SnmpQueryError(category)),
                    ["device_info"],
                )
                serialized = json.dumps(
                    {"message": result.message, "data": result.data, "raw": result.raw}
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.reachable, reachable)
                self.assertEqual(result.message, message)
                self.assertNotIn("private-community", serialized)
                self.assertNotIn("auth-secret", serialized)
                self.assertNotIn("priv-secret", serialized)

    def test_socket_and_dependency_exceptions_have_stable_categories(self):
        cases = (
            (socket.gaierror("private-community could not resolve"), "unreachable"),
            (OSError("no route for auth-secret"), "unreachable"),
            (ImportError("missing priv-secret dependency"), "dependency"),
            (RuntimeError("device rejected private-community"), "response"),
        )
        for error, category in cases:
            with self.subTest(error=type(error).__name__):
                session = PySnmpSession(self.device, timeout=1)
                session.target = object()
                with patch(
                    "net.devices.network.snmp.get_cmd",
                    new=AsyncMock(side_effect=error),
                ):
                    with self.assertRaises(SnmpQueryError) as raised:
                        asyncio.run(session.get(SYS_NAME))
                asyncio.run(session.close())

                self.assertEqual(raised.exception.category, category)
                self.assertNotIn("private-community", str(raised.exception))
                self.assertNotIn("auth-secret", str(raised.exception))
                self.assertNotIn("priv-secret", str(raised.exception))

    def test_unknown_snmp_configuration_fails_closed_without_secrets(self):
        base = {
            "snmp_version": "v3",
            "snmp_security_level": "authPriv",
            "snmp_auth_protocol": "sha256",
            "snmp_priv_protocol": "aes128",
        }
        cases = (
            ("snmp_version", "v1"),
            ("snmp_security_level", "private-community"),
            ("snmp_auth_protocol", "auth-secret"),
            ("snmp_priv_protocol", "priv-secret"),
        )
        for field, invalid_value in cases:
            with self.subTest(field=field):
                for name, value in base.items():
                    setattr(self.device, name, value)
                self.device.snmp_username = "inspector"
                setattr(self.device, field, invalid_value)
                engine = MemoryEngine()
                with patch("net.devices.network.snmp.SnmpEngine", return_value=engine):
                    result = collect_network_snmp(
                        self.device,
                        selected_items=["device_info"],
                        session_factory=PySnmpSession,
                    )

                serialized = json.dumps({
                    "message": result.message,
                    "data": result.data,
                    "raw": result.raw,
                })
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.message, "SNMP invalid configuration")
                self.assertTrue(engine.closed)
                self.assertNotIn(invalid_value, serialized)

    def test_item_registry_contains_only_supported_public_items(self):
        self.assertEqual(
            SNMP_ITEMS,
            frozenset(
                {
                    "device_info",
                    "cpu",
                    "memory",
                    "temperature",
                    "interface_status",
                    "vlan_status",
                    "traffic",
                }
            ),
        )

    def test_empty_selection_performs_no_queries(self):
        session = MemorySession(scalars={SYS_NAME: "must-not-be-read"})

        result = self.collect(session, [])

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data, {})
        self.assertEqual(session.get_queries, [])
        self.assertEqual(session.walk_queries, [])

    def test_pysnmp_adapter_preserves_normal_integer_values(self):
        session = PySnmpSession(self.device, timeout=1)
        session.target = object()
        response = (None, 0, 0, ((SYS_UPTIME, Integer32(7)),))

        with patch("net.devices.network.snmp.get_cmd", new=AsyncMock(return_value=response)):
            value = asyncio.run(session.get(SYS_UPTIME))
        asyncio.run(session.close())

        self.assertEqual(value, 7)

    def test_pysnmp_error_indication_and_error_status_categories(self):
        cases = (
            (errind.requestTimedOut, 0, "timeout"),
            (errind.authenticationFailure, 0, "authentication"),
            (None, rfc1905.errorStatus.clone(5), "response"),
        )
        for indication, status, category in cases:
            with self.subTest(category=category):
                session = PySnmpSession(self.device, timeout=1)
                session.target = object()
                response = (indication, status, 1, ())
                with patch(
                    "net.devices.network.snmp.get_cmd",
                    new=AsyncMock(return_value=response),
                ):
                    with self.assertRaises(SnmpQueryError) as raised:
                        asyncio.run(session.get(SYS_NAME))
                asyncio.run(session.close())
                self.assertEqual(raised.exception.category, category)

    def test_pysnmp_unsupported_error_status_formats_are_classified(self):
        statuses = (
            2,
            rfc1905.errorStatus.clone(2),
            "noSuchInstance",
            PrettyStatus("endOfMibView"),
        )
        for status in statuses:
            with self.subTest(status=str(status)):
                session = PySnmpSession(self.device, timeout=1)
                session.target = object()
                response = (None, status, 1, ((ObjectIdentifier(SYS_NAME), OctetString("ignored")),))
                with patch(
                    "net.devices.network.snmp.get_cmd",
                    new=AsyncMock(return_value=response),
                ):
                    with self.assertRaises(SnmpQueryError) as raised:
                        asyncio.run(session.get(SYS_NAME))
                asyncio.run(session.close())
                self.assertEqual(raised.exception.category, "unsupported")

    def test_pysnmp_exception_values_are_classified_as_unsupported(self):
        for value in (rfc1905.NoSuchObject(), rfc1905.NoSuchInstance(), rfc1905.EndOfMibView()):
            with self.subTest(value=type(value).__name__):
                session = PySnmpSession(self.device, timeout=1)
                session.target = object()
                response = (None, 0, 0, ((ObjectIdentifier(SYS_NAME), value),))
                with patch(
                    "net.devices.network.snmp.get_cmd",
                    new=AsyncMock(return_value=response),
                ):
                    with self.assertRaises(SnmpQueryError) as raised:
                        asyncio.run(session.get(SYS_NAME))
                asyncio.run(session.close())
                self.assertEqual(raised.exception.category, "unsupported")

    def test_bulk_walk_stops_before_oid_outside_requested_subtree(self):
        session = PySnmpSession(self.device, timeout=1)
        session.target = object()
        response = (
            None,
            0,
            0,
            (
                (ObjectIdentifier(IF_NAME + ".1"), OctetString("Gi0/1")),
                (ObjectIdentifier(IF_DESCR + ".1"), OctetString("outside")),
            ),
        )
        command = AsyncMock(return_value=response)

        with patch("net.devices.network.snmp.bulk_cmd", new=command):
            rows = asyncio.run(session.walk(IF_NAME))
        asyncio.run(session.close())

        self.assertEqual(rows, [("1", "Gi0/1")])
        self.assertEqual(command.await_count, 1)

    def test_v3_credentials_and_transport_target_use_device_settings(self):
        self.device.snmp_version = "v3"
        self.device.snmp_security_level = "authPriv"
        self.device.snmp_username = "inspector"
        self.device.snmp_auth_protocol = "sha256"
        self.device.snmp_auth_password = "auth-secret"
        self.device.snmp_priv_protocol = "aes128"
        self.device.snmp_priv_password = "priv-secret"
        self.device.snmp_context_name = "tenant-a"
        self.device.snmp_port = 1161
        self.device.snmp_retries = 3
        session = PySnmpSession(self.device, timeout=4)
        create_target = AsyncMock(return_value=sentinel.target)

        with patch.object(snmp_module.UdpTransportTarget, "create", new=create_target):
            target = asyncio.run(session._target())
        asyncio.run(session.close())

        self.assertIs(target, sentinel.target)
        self.assertEqual(session.credentials.userName, "inspector")
        self.assertEqual(session.credentials.authentication_key, "auth-secret")
        self.assertEqual(
            session.credentials.authentication_protocol,
            snmp_module.usmHMAC192SHA256AuthProtocol,
        )
        self.assertEqual(session.credentials.privacy_key, "priv-secret")
        self.assertEqual(
            session.credentials.privacy_protocol,
            snmp_module.usmAesCfb128Protocol,
        )
        self.assertEqual(str(session.context.contextName), "tenant-a")
        create_target.assert_awaited_once_with(
            ("192.0.2.20", 1161), timeout=4, retries=3
        )

    def test_constructor_failure_closes_dispatcher_through_factory_boundary(self):
        for failing_dependency, version in (("ContextData", "v2c"), ("UsmUserData", "v3")):
            with self.subTest(failing_dependency=failing_dependency):
                self.device.snmp_version = version
                if version == "v3":
                    self.device.snmp_username = "inspector"
                engine = MemoryEngine()
                with patch("net.devices.network.snmp.SnmpEngine", return_value=engine), patch(
                    f"net.devices.network.snmp.{failing_dependency}",
                    side_effect=RuntimeError("constructor failed"),
                ):
                    result = collect_network_snmp(
                        self.device,
                        selected_items=["device_info"],
                        session_factory=PySnmpSession,
                    )

                self.assertEqual(result.status, "failed")
                self.assertEqual(result.message, "SNMP response error")
                self.assertTrue(engine.closed)

    def test_dispatcher_closes_in_finally_when_query_fails(self):
        engine = MemoryEngine()
        with patch("net.devices.network.snmp.SnmpEngine", return_value=engine):
            session = PySnmpSession(self.device, timeout=1)
        session.target = object()
        response = (errind.requestTimedOut, 0, 0, ())

        with patch(
            "net.devices.network.snmp.get_cmd",
            new=AsyncMock(return_value=response),
        ):
            result = self.collect(session, ["device_info"])

        self.assertEqual(result.message, "SNMP request timed out")
        self.assertTrue(engine.closed)

    def test_unsupported_oid_does_not_abort_other_oids_in_same_item(self):
        engine = MemoryEngine()
        with patch("net.devices.network.snmp.SnmpEngine", return_value=engine):
            session = PySnmpSession(self.device, timeout=1)
        session.target = object()
        responses = (
            (None, rfc1905.errorStatus.clone(2), 1, ()),
            (
                None,
                0,
                0,
                ((ObjectIdentifier(SYS_DESCR), OctetString("Example Switch")),),
            ),
            (None, rfc1905.errorStatus.clone(2), 1, ()),
            (
                None,
                0,
                0,
                ((ObjectIdentifier(SYS_UPTIME), rfc1905.EndOfMibView()),),
            ),
        )

        with patch(
            "net.devices.network.snmp.get_cmd",
            new=AsyncMock(side_effect=responses),
        ):
            result = self.collect(session, ["device_info"])

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["device_info"]["description"], "Example Switch")
        self.assertTrue(engine.closed)
