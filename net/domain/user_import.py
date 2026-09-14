"""Parse and queue secret-safe bulk Active Directory account creation."""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path
from xml.sax.saxutils import escape
from xml.etree import ElementTree
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from django.core.exceptions import ValidationError
from django.db import transaction

from net.domain.tasks import enqueue_domain_operation
from net.domain.validation import _parse_rdn_components, validate_dn_within_base
from net.models import DomainOperation, Domain_Account, Domain_Controller_Config


MAX_IMPORT_ROWS = 500
REQUIRED_HEADERS = ('用户DN', '登录名', '显示名称')


def csv_template_bytes():
    output = StringIO()
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(REQUIRED_HEADERS)
    writer.writerow(('CN=Alice,OU=Users,DC=example,DC=com', 'alice', '爱丽丝'))
    return ('\ufeff' + output.getvalue()).encode('utf-8')


def xlsx_template_bytes():
    rows = (REQUIRED_HEADERS, ('CN=Alice,OU=Users,DC=example,DC=com', 'alice', '爱丽丝'))
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = ''.join(
            f'<c r="{column}{row_number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for column, value in zip(('A', 'B', 'C'), row)
        )
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="域账号" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return output.getvalue()


def parse_csv_accounts(upload) -> list[dict]:
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValidationError('请使用 UTF-8 编码的 CSV 文件。') from None
    try:
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames or not set(REQUIRED_HEADERS) <= {
            str(name).strip() for name in reader.fieldnames if name is not None
        }:
            raise ValidationError('表格缺少必填列：用户DN、登录名、显示名称。')
        rows = [
            {header: str(row.get(header) or '').strip() for header in REQUIRED_HEADERS}
            for row in reader
            if any(str(value or '').strip() for value in row.values())
        ]
    except csv.Error:
        raise ValidationError('CSV 文件无法解析。') from None
    return rows


def _column_index(reference):
    letters = ''.join(character for character in str(reference) if character.isalpha())
    index = 0
    for character in letters.upper():
        index = index * 26 + ord(character) - ord('A') + 1
    return index - 1


def _rows_from_values(values):
    if not values:
        return []
    headers = [str(value or '').strip() for value in values[0]]
    positions = {header: headers.index(header) for header in REQUIRED_HEADERS if header in headers}
    if len(positions) != len(REQUIRED_HEADERS):
        raise ValidationError('表格缺少必填列：用户DN、登录名、显示名称。')
    return [
        {
            header: str(row[position] if position < len(row) else '').strip()
            for header, position in positions.items()
        }
        for row in values[1:]
        if any(str(value or '').strip() for value in row)
    ]


def parse_xlsx_accounts(upload) -> list[dict]:
    namespace = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with ZipFile(upload) as archive:
            if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                raise ValidationError('Excel 文件解压后过大。')
            shared_strings = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared_root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
                shared_strings = [
                    ''.join(node.text or '' for node in item.findall('.//x:t', namespace))
                    for item in shared_root.findall('x:si', namespace)
                ]
            worksheets = sorted(
                name for name in archive.namelist()
                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml')
            )
            if not worksheets:
                raise ValidationError('Excel 文件中没有工作表。')
            sheet_root = ElementTree.fromstring(archive.read(worksheets[0]))
            values = []
            for row_node in sheet_root.findall('.//x:sheetData/x:row', namespace):
                row = []
                for cell in row_node.findall('x:c', namespace):
                    index = _column_index(cell.get('r'))
                    while len(row) <= index:
                        row.append('')
                    if cell.get('t') == 'inlineStr':
                        value = ''.join(
                            node.text or '' for node in cell.findall('.//x:t', namespace)
                        )
                    else:
                        value_node = cell.find('x:v', namespace)
                        value = value_node.text if value_node is not None else ''
                        if cell.get('t') == 's' and value != '':
                            value = shared_strings[int(value)]
                    row[index] = value
                values.append(row)
    except ValidationError:
        raise
    except (BadZipFile, ElementTree.ParseError, IndexError, KeyError, TypeError, ValueError):
        raise ValidationError('Excel 文件无法解析。') from None
    return _rows_from_values(values)


def validate_account_rows(rows, config) -> list[dict]:
    if not rows:
        raise ValidationError('导入表格不能为空。')
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValidationError(f'一次最多导入 {MAX_IMPORT_ROWS} 个域账号。')
    normalized = []
    login_keys = set()
    dn_keys = set()
    for number, row in enumerate(rows, start=2):
        user_dn = str(row.get('用户DN') or '').strip()
        login_name = str(row.get('登录名') or '').strip()
        display_name = str(row.get('显示名称') or '').strip()
        if not all((user_dn, login_name, display_name)):
            raise ValidationError(f'第 {number} 行存在空的必填字段。')
        try:
            user_dn = validate_dn_within_base(user_dn, config.base_dn)
            dn_key = _parse_rdn_components(user_dn)
        except ValidationError:
            raise ValidationError(f'第 {number} 行的用户 DN 无效或不在配置的域范围内。') from None
        login_key = login_name.casefold()
        if login_key in login_keys or dn_key in dn_keys:
            raise ValidationError(f'第 {number} 行包含重复的登录名或用户 DN。')
        if Domain_Account.objects.filter(login_name__iexact=login_name).exists():
            raise ValidationError(f'第 {number} 行的登录名已存在。')
        if Domain_Account.objects.filter(distinguished_name__iexact=user_dn).exists():
            raise ValidationError(f'第 {number} 行的用户 DN 已存在。')
        login_keys.add(login_key)
        dn_keys.add(dn_key)
        normalized.append({
            'user_dn': user_dn,
            'login_name': login_name,
            'display_name': display_name,
        })
    return normalized


def enqueue_imported_accounts(*, requested_by, rows, password):
    config = Domain_Controller_Config.objects.filter(pk=1).first()
    if config is None or not config.base_dn:
        raise ValidationError('未配置域控搜索根目录。')
    validated = validate_account_rows(rows, config)
    operations = []
    with transaction.atomic():
        for parameters in validated:
            operations.append(enqueue_domain_operation(
                requested_by=requested_by,
                object_type=DomainOperation.ObjectType.ACCOUNT,
                action=DomainOperation.Action.CREATE_USER,
                target_ids=[],
                parameters=parameters,
                password=password,
            ))
    return operations


def parse_account_upload(upload):
    suffix = Path(upload.name or '').suffix.casefold()
    if suffix == '.csv':
        return parse_csv_accounts(upload)
    if suffix == '.xlsx':
        return parse_xlsx_accounts(upload)
    raise ValidationError('仅支持 CSV 或 Excel (.xlsx) 文件。')
