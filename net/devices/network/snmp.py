"""Read-only SNMP collection and standard-MIB normalization for network devices."""

import asyncio
import inspect
import math

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    UsmUserData,
    bulk_cmd,
    get_cmd,
    usmAesCfb128Protocol,
    usmAesCfb192Protocol,
    usmAesCfb256Protocol,
    usmDESPrivProtocol,
    usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol,
    usmHMAC256SHA384AuthProtocol,
    usmHMAC384SHA512AuthProtocol,
    usmHMACMD5AuthProtocol,
    usmHMACSHAAuthProtocol,
)
from pysnmp.proto.rfc1905 import EndOfMibView, NoSuchInstance, NoSuchObject

from net.infrastructure.collection import CollectionResult, Timer


SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
IF_MAC = "1.3.6.1.2.1.2.2.1.6"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"

HR_MEMORY_SIZE = "1.3.6.1.2.1.25.2.2.0"
HR_STORAGE_TYPE = "1.3.6.1.2.1.25.2.3.1.2"
HR_STORAGE_ALLOCATION_UNITS = "1.3.6.1.2.1.25.2.3.1.4"
HR_STORAGE_SIZE = "1.3.6.1.2.1.25.2.3.1.5"
HR_STORAGE_USED = "1.3.6.1.2.1.25.2.3.1.6"
HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"

ENT_SENSOR_TYPE = "1.3.6.1.2.1.99.1.1.1.1"
ENT_SENSOR_SCALE = "1.3.6.1.2.1.99.1.1.1.2"
ENT_SENSOR_PRECISION = "1.3.6.1.2.1.99.1.1.1.3"
ENT_SENSOR_VALUE = "1.3.6.1.2.1.99.1.1.1.4"
ENT_SENSOR_STATUS = "1.3.6.1.2.1.99.1.1.1.5"

QBRIDGE_VLAN_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"

SNMP_ITEMS = frozenset(
    {"device_info", "cpu", "memory", "temperature", "interface_status", "vlan_status"}
)
_ITEM_ORDER = (
    "device_info",
    "cpu",
    "memory",
    "temperature",
    "interface_status",
    "vlan_status",
)

# Ordered scalar candidates. Standard MIBs remain the portable fallback.
VENDOR_OIDS = {
    "cisco": {
        "cpu": ("1.3.6.1.4.1.9.2.1.56.0", "1.3.6.1.4.1.9.2.1.57.0"),
        "memory_total": ("1.3.6.1.4.1.9.2.1.8.0",),
        "memory_used": ("1.3.6.1.4.1.9.2.1.9.0",),
        "temperature": ("1.3.6.1.4.1.9.2.1.58.0",),
    },
    "huawei": {
        "cpu": ("1.3.6.1.4.1.2011.6.3.4.1.3.0", "1.3.6.1.4.1.2011.2.235.1.1.1.23.0"),
        "memory_total": ("1.3.6.1.4.1.2011.6.3.5.1.2.0",),
        "memory_used": ("1.3.6.1.4.1.2011.6.3.5.1.3.0",),
        "temperature": ("1.3.6.1.4.1.2011.6.3.6.1.3.0",),
    },
    "h3c": {
        "cpu": ("1.3.6.1.4.1.25506.2.6.1.1.1.1.6.0", "1.3.6.1.4.1.25506.2.6.1.1.1.1.8.0"),
        "memory_total": ("1.3.6.1.4.1.25506.2.6.1.1.1.1.10.0",),
        "memory_used": ("1.3.6.1.4.1.25506.2.6.1.1.1.1.11.0",),
        "temperature": ("1.3.6.1.4.1.25506.2.6.1.1.1.1.12.0",),
    },
    "ruijie": {
        "cpu": ("1.3.6.1.4.1.4881.1.1.10.2.36.1.1.3.0", "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.4.0"),
        "memory_total": ("1.3.6.1.4.1.4881.1.1.10.2.36.1.1.6.0",),
        "memory_used": ("1.3.6.1.4.1.4881.1.1.10.2.36.1.1.7.0",),
        "temperature": ("1.3.6.1.4.1.4881.1.1.10.2.36.1.1.8.0",),
    },
}

_INTERFACE_TABLES = (
    IF_NAME,
    IF_DESCR,
    IF_MAC,
    IF_ADMIN_STATUS,
    IF_OPER_STATUS,
    IF_HIGH_SPEED,
    IF_SPEED,
    IF_HC_IN,
    IF_IN_OCTETS,
    IF_HC_OUT,
    IF_OUT_OCTETS,
    IF_IN_ERRORS,
    IF_OUT_ERRORS,
    IF_IN_DISCARDS,
    IF_OUT_DISCARDS,
)
_MEMORY_TABLES = (
    HR_STORAGE_TYPE,
    HR_STORAGE_ALLOCATION_UNITS,
    HR_STORAGE_SIZE,
    HR_STORAGE_USED,
)
_TEMPERATURE_TABLES = (
    ENT_SENSOR_TYPE,
    ENT_SENSOR_SCALE,
    ENT_SENSOR_PRECISION,
    ENT_SENSOR_VALUE,
    ENT_SENSOR_STATUS,
)


class SnmpQueryError(Exception):
    """A credential-safe SNMP failure with a stable public category."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _vendor_key(vendor):
    normalized = (vendor or "").strip().lower()
    return next((name for name in VENDOR_OIDS if name in normalized), None)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _text(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _suffix(base_oid, instance_oid):
    oid = str(instance_oid).lstrip(".")
    base = base_oid.lstrip(".")
    return oid[len(base) + 1 :] if oid.startswith(base + ".") else oid


def _table(snapshot, oid):
    return {_suffix(oid, instance): value for instance, value in snapshot["tables"].get(oid, [])}


def _sort_index(value):
    parts = str(value).split(".")
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


def _raw_for(snapshot, scalar_oids=(), table_oids=()):
    raw = {}
    for oid in scalar_oids:
        if oid in snapshot["scalars"] and snapshot["scalars"][oid] is not None:
            value = snapshot["scalars"][oid]
            raw[oid] = value.hex() if isinstance(value, bytes) else value
    for oid in table_oids:
        rows = snapshot["tables"].get(oid, [])
        if rows:
            raw[oid] = [
                (instance, value.hex() if isinstance(value, bytes) else value)
                for instance, value in rows
            ]
    return raw


def _valid_number(value, predicate=lambda value: True):
    number = _number(value)
    return number if number is not None and math.isfinite(number) and predicate(number) else None


def _first_numeric(scalars, candidates, predicate=lambda value: True):
    for oid in candidates:
        value = _valid_number(scalars.get(oid), predicate)
        if value is not None:
            return value
    return None


def _parse_device_info(snapshot, vendor):
    scalars = snapshot["scalars"]
    values = {
        "system_name": _text(scalars.get(SYS_NAME)),
        "description": _text(scalars.get(SYS_DESCR)),
        "uptime_ticks": _number(scalars.get(SYS_UPTIME)),
        "vendor": (vendor or "generic").strip().lower() or "generic",
    }
    return values if any(values[key] is not None for key in ("system_name", "description", "uptime_ticks")) else None


def _parse_cpu(snapshot, vendor):
    vendor_oids = VENDOR_OIDS.get(_vendor_key(vendor), {}).get("cpu", ())
    usage = _first_numeric(snapshot["scalars"], vendor_oids, lambda value: 0 <= value <= 100)
    if usage is None:
        loads = [_number(value) for value in _table(snapshot, HR_PROCESSOR_LOAD).values()]
        loads = [value for value in loads if value is not None and 0 <= value <= 100]
        usage = sum(loads) / len(loads) if loads else None
    return {"usage_percent": float(usage)} if usage is not None and 0 <= usage <= 100 else None


def _parse_memory(snapshot, vendor):
    registry = VENDOR_OIDS.get(_vendor_key(vendor), {})
    total = _first_numeric(snapshot["scalars"], registry.get("memory_total", ()), lambda value: value > 0)
    used = (
        _first_numeric(
            snapshot["scalars"],
            registry.get("memory_used", ()),
            lambda value: 0 <= value <= total,
        )
        if total is not None
        else None
    )
    if total is None or used is None:
        types = _table(snapshot, HR_STORAGE_TYPE)
        units = _table(snapshot, HR_STORAGE_ALLOCATION_UNITS)
        sizes = _table(snapshot, HR_STORAGE_SIZE)
        used_rows = _table(snapshot, HR_STORAGE_USED)
        physical = next(
            (index for index, value in types.items() if str(value).lstrip(".") == "1.3.6.1.2.1.25.2.1.2"),
            None,
        )
        unit = _number(units.get(physical)) if physical is not None else None
        size = _number(sizes.get(physical)) if physical is not None else None
        used_size = _number(used_rows.get(physical)) if physical is not None else None
        if unit is not None and size is not None and used_size is not None:
            total, used = unit * size, unit * used_size
        elif used_size is not None:
            total_kb = _number(snapshot["scalars"].get(HR_MEMORY_SIZE))
            total = total_kb * 1024 if total_kb is not None else None
            used = used_size * (unit or 1)
    if total is None or used is None or total <= 0 or used < 0:
        return None
    return {
        "total_bytes": int(total),
        "used_bytes": int(used),
        "usage_percent": round(used * 100 / total, 2),
    }


def _parse_temperature(snapshot, vendor):
    vendor_oids = VENDOR_OIDS.get(_vendor_key(vendor), {}).get("temperature", ())
    value = _first_numeric(snapshot["scalars"], vendor_oids)
    if value is not None:
        return {"values_celsius": [float(value)]}
    types = _table(snapshot, ENT_SENSOR_TYPE)
    scales = _table(snapshot, ENT_SENSOR_SCALE)
    precisions = _table(snapshot, ENT_SENSOR_PRECISION)
    values = _table(snapshot, ENT_SENSOR_VALUE)
    statuses = _table(snapshot, ENT_SENSOR_STATUS)
    temperatures = []
    for index in sorted(values, key=_sort_index):
        raw_value = _number(values[index])
        scale = _number(scales.get(index))
        precision = _number(precisions.get(index))
        status = _number(statuses.get(index))
        if _number(types.get(index)) != 8 or raw_value is None or scale is None or precision is None:
            continue
        if status is not None and status != 1:
            continue
        temperatures.append(raw_value * (10 ** (int(scale) - 9 - int(precision))))
    return {"values_celsius": temperatures} if temperatures else None


def _mac(value):
    if value is None:
        return None
    if isinstance(value, bytes):
        octets = value
    else:
        text = str(value).strip()
        compact = text.replace(":", "").replace("-", "").replace(" ", "")
        try:
            octets = bytes.fromhex(compact)
        except ValueError:
            return text
    return ":".join(f"{octet:02x}" for octet in octets) if octets else None


def _status(value, operational=False):
    statuses = {1: "up", 2: "down", 3: "testing"}
    if operational:
        statuses.update({4: "unknown", 5: "dormant", 6: "not_present", 7: "lower_layer_down"})
    return statuses.get(_number(value))


def _parse_interfaces(snapshot):
    tables = {oid: _table(snapshot, oid) for oid in _INTERFACE_TABLES}
    indexes = sorted({index for table in tables.values() for index in table}, key=_sort_index)
    interfaces = []
    for index in indexes:
        high_speed = _number(tables[IF_HIGH_SPEED].get(index))
        speed = _number(tables[IF_SPEED].get(index))
        name = (_text(tables[IF_NAME].get(index)) or "").strip() or None
        description = (_text(tables[IF_DESCR].get(index)) or "").strip() or None
        interface = {
                "index": index,
                "name": name,
                "description": description,
                "mac": _mac(tables[IF_MAC].get(index)),
                "admin_status": _status(tables[IF_ADMIN_STATUS].get(index)),
                "oper_status": _status(tables[IF_OPER_STATUS].get(index), operational=True),
                "speed_mbps": high_speed if high_speed is not None else (speed / 1_000_000 if speed is not None else None),
                "in_octets": _number(tables[IF_HC_IN].get(index, tables[IF_IN_OCTETS].get(index))),
                "out_octets": _number(tables[IF_HC_OUT].get(index, tables[IF_OUT_OCTETS].get(index))),
                "in_errors": _number(tables[IF_IN_ERRORS].get(index)),
                "out_errors": _number(tables[IF_OUT_ERRORS].get(index)),
                "in_discards": _number(tables[IF_IN_DISCARDS].get(index)),
                "out_discards": _number(tables[IF_OUT_DISCARDS].get(index)),
            }
        if not any(
            (
                interface["name"],
                interface["description"],
                interface["admin_status"],
                interface["oper_status"],
            )
        ):
            continue
        interfaces.append(interface)
    return {"interfaces": interfaces} if interfaces else None


def _parse_vlans(snapshot):
    names = _table(snapshot, QBRIDGE_VLAN_NAME)
    vlans = []
    for index in sorted(names, key=_sort_index):
        vlan_id = _number(index.split(".")[-1])
        name = _text(names[index])
        if vlan_id is not None and name:
            vlans.append({"vlan_id": int(vlan_id), "name": name})
    return {"vlans": vlans} if vlans else None


def parse_snmp_snapshot(snapshot, selected_items, vendor):
    """Normalize collected OID evidence without performing any I/O."""
    snapshot = {
        "scalars": dict(snapshot.get("scalars", {})),
        "tables": dict(snapshot.get("tables", {})),
    }
    selection = _ITEM_ORDER if selected_items is None else selected_items
    requested = [item for item in selection if item in SNMP_ITEMS]
    data, raw, completed = {}, {}, set()
    registry = VENDOR_OIDS.get(_vendor_key(vendor), {})
    definitions = {
        "device_info": (_parse_device_info, (SYS_NAME, SYS_DESCR, SYS_UPTIME), ()),
        "cpu": (_parse_cpu, registry.get("cpu", ()), (HR_PROCESSOR_LOAD,)),
        "memory": (
            _parse_memory,
            registry.get("memory_total", ()) + registry.get("memory_used", ()) + (HR_MEMORY_SIZE,),
            _MEMORY_TABLES,
        ),
        "temperature": (_parse_temperature, registry.get("temperature", ()), _TEMPERATURE_TABLES),
        "interface_status": (_parse_interfaces, (), _INTERFACE_TABLES),
        "vlan_status": (_parse_vlans, (), (QBRIDGE_VLAN_NAME,)),
    }
    for item in requested:
        parser, scalar_oids, table_oids = definitions[item]
        parsed = parser(snapshot, vendor) if item in {"device_info", "cpu", "memory", "temperature"} else parser(snapshot)
        if parsed is None:
            continue
        data[item] = parsed
        raw[item] = _raw_for(snapshot, scalar_oids, table_oids)
        completed.add(item)
    return data, raw, completed


def _plain_value(value):
    if value is None:
        return None
    if hasattr(value, "isValue") and not value.isValue:
        return None
    if hasattr(value, "asOctets"):
        octets = value.asOctets()
        try:
            text = octets.decode("utf-8")
        except UnicodeDecodeError:
            return bytes(octets)
        return text if text.isprintable() else bytes(octets)
    number = _number(value)
    return number if number is not None else _text(value)


def _is_unsupported_value(value):
    return isinstance(value, (NoSuchObject, NoSuchInstance, EndOfMibView))


class PySnmpSession:
    """Small PySNMP 7 asyncio boundary restricted to GET and BULK WALK."""

    _AUTH_PROTOCOLS = {
        "md5": usmHMACMD5AuthProtocol,
        "sha1": usmHMACSHAAuthProtocol,
        "sha224": usmHMAC128SHA224AuthProtocol,
        "sha256": usmHMAC192SHA256AuthProtocol,
        "sha384": usmHMAC256SHA384AuthProtocol,
        "sha512": usmHMAC384SHA512AuthProtocol,
    }
    _PRIV_PROTOCOLS = {
        "des": usmDESPrivProtocol,
        "aes128": usmAesCfb128Protocol,
        "aes192": usmAesCfb192Protocol,
        "aes256": usmAesCfb256Protocol,
    }

    def __init__(self, device, timeout):
        self.device = device
        self.timeout = timeout
        self.engine = SnmpEngine()
        try:
            self.context = ContextData(contextName=device.snmp_context_name or "")
            if device.snmp_version == "v3":
                uses_auth = device.snmp_security_level in {"authNoPriv", "authPriv"}
                uses_priv = device.snmp_security_level == "authPriv"
                self.credentials = UsmUserData(
                    device.snmp_username,
                    authKey=device.snmp_auth_password if uses_auth else None,
                    privKey=device.snmp_priv_password if uses_priv else None,
                    authProtocol=self._AUTH_PROTOCOLS.get(device.snmp_auth_protocol) if uses_auth else None,
                    privProtocol=self._PRIV_PROTOCOLS.get(device.snmp_priv_protocol) if uses_priv else None,
                )
            else:
                self.credentials = CommunityData(device.snmp_community, mpModel=1)
            self.target = None
        except Exception:
            self.engine.close_dispatcher()
            raise

    async def _target(self):
        if self.target is None:
            self.target = await UdpTransportTarget.create(
                (self.device.ip, self.device.snmp_port),
                timeout=self.timeout,
                retries=self.device.snmp_retries,
            )
        return self.target

    @staticmethod
    def _category(error):
        text = str(error).lower()
        identity = f"{type(error).__name__} {text}".lower()
        compact = "".join(character for character in identity if character.isalnum())
        try:
            numeric = int(error)
        except (TypeError, ValueError):
            numeric = None
        if numeric == 2 or any(
            marker in compact
            for marker in ("nosuchname", "nosuchobject", "nosuchinstance", "endofmibview")
        ):
            return "unsupported"
        if any(word in identity for word in ("auth", "digest", "unknown usm user", "decrypt", "cipher")):
            return "authentication"
        if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or any(
            word in identity for word in ("timeout", "timed out", "no snmp response")
        ):
            return "timeout"
        return "response"

    @classmethod
    def _check_response(cls, error_indication, error_status):
        if error_indication:
            category = cls._category(error_indication)
            raise SnmpQueryError(category)
        if error_status:
            value = error_status.prettyPrint() if hasattr(error_status, "prettyPrint") else error_status
            category = cls._category(value)
            raise SnmpQueryError(category)
        return True

    async def get(self, oid):
        try:
            error_indication, error_status, _, var_binds = await get_cmd(
                self.engine,
                self.credentials,
                await self._target(),
                self.context,
                ObjectType(ObjectIdentity(oid)),
            )
        except SnmpQueryError:
            raise
        except Exception as exc:
            raise SnmpQueryError(self._category(exc)) from None
        if not self._check_response(error_indication, error_status) or not var_binds:
            return None
        value = var_binds[0][1]
        if _is_unsupported_value(value):
            raise SnmpQueryError("unsupported")
        return _plain_value(value)

    async def walk(self, oid):
        rows = []
        cursor = oid
        try:
            while True:
                error_indication, error_status, _, var_binds = await bulk_cmd(
                    self.engine,
                    self.credentials,
                    await self._target(),
                    self.context,
                    0,
                    25,
                    ObjectType(ObjectIdentity(cursor)),
                    lexicographicMode=False,
                )
                if not self._check_response(error_indication, error_status) or not var_binds:
                    break
                advanced = False
                for var_bind in var_binds:
                    instance_oid = str(var_bind[0])
                    if not instance_oid.startswith(oid + "."):
                        return rows
                    raw_value = var_bind[1]
                    if _is_unsupported_value(raw_value):
                        if rows:
                            return rows
                        raise SnmpQueryError("unsupported")
                    value = _plain_value(raw_value)
                    if value is None:
                        return rows
                    rows.append((_suffix(oid, instance_oid), value))
                    if instance_oid != cursor:
                        cursor, advanced = instance_oid, True
                if not advanced:
                    break
        except SnmpQueryError:
            raise
        except Exception as exc:
            raise SnmpQueryError(self._category(exc)) from None
        return rows

    async def close(self):
        self.engine.close_dispatcher()


async def _safe_get(session, oid):
    try:
        return await session.get(oid)
    except SnmpQueryError as exc:
        if exc.category == "unsupported":
            return None
        raise


async def _safe_walk(session, oid):
    try:
        return await session.walk(oid)
    except SnmpQueryError as exc:
        if exc.category == "unsupported":
            return []
        raise


async def _first_available(session, candidates, scalars, predicate=lambda value: True):
    for oid in candidates:
        value = await _safe_get(session, oid)
        normalized = _valid_number(value, predicate)
        if normalized is not None:
            scalars[oid] = normalized
            return normalized
    return None


async def _collect_snapshot(device, timeout, selected_items, session_factory):
    session = None
    snapshot = {"scalars": {}, "tables": {}}
    vendor_registry = VENDOR_OIDS.get(_vendor_key(device.vendor), {})
    selection = _ITEM_ORDER if selected_items is None else selected_items
    requested = [item for item in selection if item in SNMP_ITEMS]
    try:
        session = session_factory(device, timeout)
        if "device_info" in requested:
            for oid in (SYS_NAME, SYS_DESCR, SYS_UPTIME):
                snapshot["scalars"][oid] = await _safe_get(session, oid)
        if "cpu" in requested:
            found = await _first_available(
                session,
                vendor_registry.get("cpu", ()),
                snapshot["scalars"],
                lambda value: 0 <= value <= 100,
            )
            if found is None:
                snapshot["tables"][HR_PROCESSOR_LOAD] = await _safe_walk(session, HR_PROCESSOR_LOAD)
        if "memory" in requested:
            total = await _first_available(
                session,
                vendor_registry.get("memory_total", ()),
                snapshot["scalars"],
                lambda value: value > 0,
            )
            used = await _first_available(
                session,
                vendor_registry.get("memory_used", ()),
                snapshot["scalars"],
                lambda value: value >= 0 and (total is None or value <= total),
            )
            if total is None or used is None:
                snapshot["scalars"][HR_MEMORY_SIZE] = await _safe_get(session, HR_MEMORY_SIZE)
                for oid in _MEMORY_TABLES:
                    snapshot["tables"][oid] = await _safe_walk(session, oid)
        if "temperature" in requested:
            found = await _first_available(session, vendor_registry.get("temperature", ()), snapshot["scalars"])
            if found is None:
                for oid in _TEMPERATURE_TABLES:
                    snapshot["tables"][oid] = await _safe_walk(session, oid)
        if "interface_status" in requested:
            for oid in _INTERFACE_TABLES:
                snapshot["tables"][oid] = await _safe_walk(session, oid)
        if "vlan_status" in requested:
            snapshot["tables"][QBRIDGE_VLAN_NAME] = await _safe_walk(session, QBRIDGE_VLAN_NAME)
        return snapshot
    finally:
        close = getattr(session, "close", None) if session is not None else None
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result


_ERROR_MESSAGES = {
    "authentication": "SNMP authentication failed",
    "timeout": "SNMP request timed out",
    "response": "SNMP response error",
}


def collect_network_snmp(device, timeout=12, selected_items=None, session_factory=PySnmpSession):
    """Collect selected read-only SNMP items through one asyncio boundary."""
    timer = Timer()
    selection = _ITEM_ORDER if selected_items is None else selected_items
    requested = {item for item in selection if item in SNMP_ITEMS}
    try:
        with timer:
            snapshot = asyncio.run(_collect_snapshot(device, timeout, selected_items, session_factory))
        data, raw, completed = parse_snmp_snapshot(snapshot, selected_items, device.vendor)
        missing = requested - completed
        status = "partial" if missing and completed else "failed" if missing else "success"
        message = "缺少有效采集证据：" + ", ".join(sorted(missing)) if missing else ""
        return CollectionResult(True, status, message, data, raw, timer.duration_ms)
    except SnmpQueryError as exc:
        category = exc.category if exc.category in _ERROR_MESSAGES else "response"
        return CollectionResult(
            category != "timeout",
            "failed",
            _ERROR_MESSAGES[category],
            duration_ms=getattr(timer, "duration_ms", 0),
        )
    except (TimeoutError, asyncio.TimeoutError):
        return CollectionResult(False, "failed", _ERROR_MESSAGES["timeout"], duration_ms=getattr(timer, "duration_ms", 0))
    except Exception:
        return CollectionResult(True, "failed", _ERROR_MESSAGES["response"], duration_ms=getattr(timer, "duration_ms", 0))


__all__ = [
    "SNMP_ITEMS",
    "VENDOR_OIDS",
    "PySnmpSession",
    "SnmpQueryError",
    "collect_network_snmp",
    "parse_snmp_snapshot",
]
