"""Small XLSX reader/writer for tabular import templates."""

from io import BytesIO
from xml.etree import ElementTree
from xml.sax.saxutils import escape
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


SPREADSHEET_NAMESPACE = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def build_xlsx(rows, *, sheet_name='导入模板'):
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for column_number, value in enumerate(row, start=1):
            column = _column_name(column_number)
            text = escape(str(value if value is not None else ''))
            cells.append(
                f'<c r="{column}{row_number}" t="inlineStr"><is><t>{text}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{SPREADSHEET_NAMESPACE}">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            '_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            'xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<workbook xmlns="{SPREADSHEET_NAMESPACE}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>',
        )
        archive.writestr(
            'xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr('xl/worksheets/sheet1.xml', sheet)
    return output.getvalue()


def read_xlsx_rows(upload, *, max_rows, max_columns, max_cell_chars):
    namespace = {'x': SPREADSHEET_NAMESPACE}
    try:
        with ZipFile(upload) as archive:
            if sum(item.file_size for item in archive.infolist()) > 20 * 1024 * 1024:
                raise ValueError('Excel 文件解压后不能超过 20 MB')
            shared_strings = _shared_strings(archive, namespace)
            worksheets = sorted(
                name for name in archive.namelist()
                if name.startswith('xl/worksheets/sheet') and name.endswith('.xml')
            )
            if not worksheets:
                raise ValueError('Excel 文件中没有工作表')
            sheet_root = ElementTree.fromstring(archive.read(worksheets[0]))
            rows = []
            for row_node in sheet_root.findall('.//x:sheetData/x:row', namespace):
                if len(rows) >= max_rows:
                    raise ValueError(f'Excel 最多允许 {max_rows - 1} 行数据')
                row = []
                for cell in row_node.findall('x:c', namespace):
                    index = _column_index(cell.get('r'))
                    if index >= max_columns:
                        raise ValueError(f'Excel 最多允许 {max_columns} 列')
                    while len(row) <= index:
                        row.append('')
                    value = _cell_value(cell, shared_strings, namespace)
                    if len(value) > max_cell_chars:
                        raise ValueError(
                            f'Excel 单元格内容不能超过 {max_cell_chars} 个字符'
                        )
                    row[index] = value
                rows.append(row)
            return rows
    except ValueError:
        raise
    except (BadZipFile, ElementTree.ParseError, IndexError, KeyError, TypeError):
        raise ValueError('Excel 文件无法解析') from None


def _column_name(number):
    name = ''
    while number:
        number, remainder = divmod(number - 1, 26)
        name = chr(ord('A') + remainder) + name
    return name


def _column_index(reference):
    letters = ''.join(character for character in str(reference) if character.isalpha())
    index = 0
    for character in letters.upper():
        index = index * 26 + ord(character) - ord('A') + 1
    return index - 1


def _shared_strings(archive, namespace):
    if 'xl/sharedStrings.xml' not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
    return [
        ''.join(node.text or '' for node in item.findall('.//x:t', namespace))
        for item in root.findall('x:si', namespace)
    ]


def _cell_value(cell, shared_strings, namespace):
    if cell.get('t') == 'inlineStr':
        return ''.join(node.text or '' for node in cell.findall('.//x:t', namespace))
    value_node = cell.find('x:v', namespace)
    value = value_node.text if value_node is not None else ''
    if cell.get('t') == 's' and value != '':
        return shared_strings[int(value)]
    return str(value or '')
