"""Read one computer's AD recovery records without persisting secret material."""
import re
from uuid import UUID

from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables
from ldap3 import LEVEL

from net.domain.client import DomainClient
from net.domain.validation import validate_dn_within_base


def _scalar(attributes, name):
    value = next((v for k, v in attributes.items() if k.casefold() == name.casefold()), "")
    if isinstance(value, (tuple, list)):
        return value[0] if len(value) == 1 else ""
    return value


def _guid(value):
    try:
        return str(UUID(bytes_le=value) if isinstance(value, bytes) else UUID(str(value).strip("{}")))
    except (ValueError, TypeError, AttributeError):
        return ""


@sensitive_variables()
def read_bitlocker_keys(config, computer):
    dn = validate_dn_within_base(computer.distinguished_name, config.base_dn)
    if not config.use_ssl:
        raise ValidationError("获取恢复密钥需在域控连接设置中启用 LDAPS，并配置可信的域控证书。")
    connection = None
    try:
        connection = DomainClient(config).connect()
        connection.search(
            dn, "(objectClass=msFVE-RecoveryInformation)", search_scope=LEVEL,
            attributes=["msFVE-RecoveryPassword", "msFVE-RecoveryGuid", "whenCreated"],
            size_limit=200, time_limit=15,
        )
        code = (connection.result or {}).get("result")
        if code != 0:
            if code in {3, 4, 11}:
                raise ValidationError("域控查询超时或恢复记录超过查询限制，未返回不完整结果。")
            raise ValidationError(DomainClient._error_result(code).error_message)
        records = []
        for entry in connection.response or []:
            if entry.get("type") != "searchResEntry":
                continue
            attributes = entry.get("attributes", {})
            password = _scalar(attributes, "msFVE-RecoveryPassword")
            password = password if isinstance(password, str) and re.fullmatch(r"[0-9]{6}(?:-[0-9]{6}){7}", password) else ""
            records.append({
                "key_id": _guid(_scalar(attributes, "msFVE-RecoveryGuid")),
                "password": password,
                "created_at": str(_scalar(attributes, "whenCreated") or ""),
            })
        return sorted(records, key=lambda row: row["created_at"], reverse=True)
    except ValidationError:
        raise
    except Exception:
        raise ValidationError("读取 BitLocker 恢复信息失败，请检查域控连接、证书和恢复信息读取权限。") from None
    finally:
        if connection is not None:
            try:
                connection.unbind()
            except Exception:
                pass
