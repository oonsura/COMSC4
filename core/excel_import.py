"""
ระบบ Import / Preview / Export ข้อมูลนักเรียน ตามกระบวนการของโปรเจกต์ DSCOMPOSN (อ้างอิง/DSCOMPOSN/dscomposn/composn/excel.py)
รองรับ:
1. ไฟล์ CSV / Excel รูปแบบมาตรฐาน `students_data.csv` (คอลัมน์: ชั้น, ห้อง, เลขที่, เลขประจำตัว, ชื่อ-นามสกุล)
2. ไฟล์ที่มีคอลัมน์สลับลำดับกัน หรือแยกคอลัมน์ คำนำหน้า / ชื่อ / นามสกุล / กลุ่ม / อีเมล
3. การตรวจสอบข้อมูลล่วงหน้า (Preview: แสดงการจับคู่คอลัมน์ แถวที่ถูกต้อง/สร้างใหม่/ทับข้อมูลเดิม/ข้อผิดพลาด) ก่อนยืนยันนำเข้าจริง
4. การดาวน์โหลดไฟล์ตัวอย่าง (Template ภาษาไทย / อังกฤษ) และการ Export รายชื่อนักเรียนเป็นไฟล์ Excel (.xlsx)
"""
import csv
import datetime as dt
import re
from io import BytesIO
from urllib.parse import quote

from django.db import transaction
from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .models import Classroom, User

STUDENT_COLUMNS = [
    'grade_level', 'room_code', 'class_number', 'student_id', 'full_name', 'group_name', 'email',
]
STUDENT_COLUMNS_TH = [
    'ชั้น', 'ห้อง', 'เลขที่', 'เลขประจำตัว', 'ชื่อ-นามสกุล', 'กลุ่ม', 'อีเมล',
]
STUDENT_REQUIRED_COLUMNS = ['student_id', 'full_name']

STUDENT_FIELD_LABELS_TH = {
    'grade_level': 'ชั้น',
    'room_code': 'ห้อง',
    'class_number': 'เลขที่',
    'student_id': 'เลขประจำตัว',
    'full_name': 'ชื่อ-นามสกุล',
    'prefix': 'คำนำหน้า',
    'first_name': 'ชื่อ',
    'last_name': 'นามสกุล',
    'group_name': 'กลุ่ม',
    'email': 'อีเมล',
}

HEADER_ALIASES = {
    'ชั้น': 'grade_level',
    'ระดับชั้น': 'grade_level',
    'ชั้นปี': 'grade_level',
    'grade': 'grade_level',
    'grade_level': 'grade_level',
    'level': 'grade_level',
    'ห้อง': 'room_code',
    'ห้องเรียน': 'room_code',
    'ชั้น/ห้อง': 'room_code',
    'room': 'room_code',
    'room_code': 'room_code',
    'classroom': 'room_code',
    'เลขที่': 'class_number',
    'ลำดับ': 'class_number',
    'no': 'class_number',
    'no.': 'class_number',
    'number': 'class_number',
    'class_number': 'class_number',
    'เลขประจำตัว': 'student_id',
    'เลขประจำตัวนักเรียน': 'student_id',
    'รหัสประจำตัว': 'student_id',
    'รหัสนักเรียน': 'student_id',
    'รหัส': 'student_id',
    'student_id': 'student_id',
    'studentid': 'student_id',
    'id': 'student_id',
    'code': 'student_id',
    'ชื่อ-นามสกุล': 'full_name',
    'ชื่อ - นามสกุล': 'full_name',
    'ชื่อ นามสกุล': 'full_name',
    'ชื่อนามสกุล': 'full_name',
    'ชื่อ-สกุล': 'full_name',
    'ชื่อสกุล': 'full_name',
    'full_name': 'full_name',
    'fullname': 'full_name',
    'name': 'full_name',
    'คำนำหน้า': 'prefix',
    'คำนำหน้าชื่อ': 'prefix',
    'prefix': 'prefix',
    'title': 'prefix',
    'ชื่อ': 'first_name',
    'ชื่อจริง': 'first_name',
    'first_name': 'first_name',
    'firstname': 'first_name',
    'นามสกุล': 'last_name',
    'สกุล': 'last_name',
    'last_name': 'last_name',
    'lastname': 'last_name',
    'กลุ่ม': 'group_name',
    'กลุ่มเรียน': 'group_name',
    'group': 'group_name',
    'group_name': 'group_name',
    'อีเมล': 'email',
    'อีเมล์': 'email',
    'อีเมลโรงเรียน': 'email',
    'email': 'email',
}
_COMPACT_HEADER_ALIASES = {
    re.sub(r'[\s_\-]+', '', k.lower()): v for k, v in HEADER_ALIASES.items()
}

HEADER_FILL = PatternFill('solid', fgColor='14532D')
HEADER_FONT = Font(bold=True, color='FFFFFF')


def _clean(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _resolve_header(raw_header):
    norm = ' '.join(_clean(raw_header).lower().split())
    if not norm:
        return None
    if norm in HEADER_ALIASES:
        return HEADER_ALIASES[norm]
    stripped = re.sub(r'\s*[\(\[].*?[\)\]]', '', norm).strip()
    if stripped in HEADER_ALIASES:
        return HEADER_ALIASES[stripped]
    compact = re.sub(r'[\s_\-]+', '', stripped)
    return _COMPACT_HEADER_ALIASES.get(compact)


def build_workbook(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows in sheets:
        safe_title = re.sub(r'[:\\/?*\[\]]', '_', str(title or 'Sheet')).strip()[:31] or 'Sheet'
        ws = wb.create_sheet(title=safe_title)
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal='center', vertical='center')
        for row in rows:
            ws.append(list(row))
        for i, h in enumerate(headers, start=1):
            width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:300] if i - 1 < len(r)]) + 4
            ws.column_dimensions[get_column_letter(i)].width = min(max(width, 10), 48)
        ws.freeze_panes = 'A2'
    if not wb.worksheets:
        wb.create_sheet('Sheet1')
    return wb


def workbook_response(wb, filename):
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return resp


def students_template_workbook(lang='th'):
    headers = ['ชั้น', 'ห้อง', 'เลขที่', 'เลขประจำตัว', 'ชื่อ-นามสกุล'] if lang == 'th' else [
        'grade_level', 'room_code', 'class_number', 'student_id', 'full_name'
    ]
    sample_rows = [
        ['4', '1', '1', '51443', 'นายสรวิชญ์   ชนะชาญชัย'],
        ['4', '1', '2', '51456', 'นายรามิล   เฉลิมวณิชย์'],
        ['4', '2', '1', '51396', 'นายพัทธดนย์   ศัทรากูล'],
    ]
    guide_headers = ['ลำดับคอลัมน์', 'ชื่อหัวตาราง (ภาษาไทย)', 'ชื่อหัวตาราง (ภาษาอังกฤษ)', 'คำอธิบาย', 'ตัวอย่าง']
    guide_rows = [
        ['A', 'ชั้น', 'grade_level', 'ระดับชั้น เช่น 4 หรือ ม.4', '4'],
        ['B', 'ห้อง', 'room_code', 'หมายเลขห้อง เช่น 1 ถึง 14 (ระบบแปลงเป็นรหัสห้อง 01-14 อัตโนมัติ)', '1'],
        ['C', 'เลขที่', 'class_number', 'เลขที่ในห้องเรียน', '1'],
        ['D', 'เลขประจำตัว', 'student_id', 'เลขประจำตัวนักเรียน 5 หลัก (ระบบสร้างอีเมล s[รหัส]@debsirin.ac.th อัตโนมัติ)', '51443'],
        ['E', 'ชื่อ-นามสกุล', 'full_name', 'คำนำหน้า ชื่อ และนามสกุล (หรือแยกคอลัมน์ ชื่อ / นามสกุล ก็ได้)', 'นายสรวิชญ์ ชนะชาญชัย'],
    ]
    return build_workbook([
        ('รายชื่อนักเรียน', headers, sample_rows),
        ('คำอธิบายการกรอกข้อมูล', guide_headers, guide_rows),
    ])


def students_export_workbook(qs):
    headers = ['ชั้น', 'ห้อง', 'เลขที่', 'เลขประจำตัว', 'ชื่อ-นามสกุล', 'อีเมลโรงเรียน', 'กลุ่ม', 'คะแนนรวม']
    rows = []
    for u in qs:
        room_num = int(u.room_code) if (u.room_code and u.room_code.isdigit()) else (u.room_code or '')
        grade = u.classroom.grade_level if u.classroom else '4'
        rows.append([
            grade,
            room_num,
            u.class_number,
            u.student_id,
            u.get_full_name(),
            u.email,
            u.group_name,
            u.total_score,
        ])
    return build_workbook([('รายชื่อนักเรียน', headers, rows)])


def _load_sheet_rows(uploaded_file):
    fname = getattr(uploaded_file, 'name', '') or ''
    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)
    raw = uploaded_file.read()
    if fname.lower().endswith('.xlsx'):
        try:
            wb = load_workbook(BytesIO(raw), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if not rows:
                raise ValueError('ไฟล์ว่างเปล่า')
            return rows
        except Exception:
            raise ValueError('ไม่สามารถอ่านไฟล์ Excel ได้ กรุณาใช้ไฟล์ .xlsx หรือ .csv ที่ถูกต้อง')

    if isinstance(raw, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'cp874', 'tis-620'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode('utf-8', errors='replace')
    else:
        text = str(raw)
    rows = [tuple(r) for r in csv.reader(text.splitlines())]
    if not rows:
        raise ValueError('ไฟล์ว่างเปล่า')
    return rows


def _split_prefix_first_last(full_name_str, explicit_prefix='', explicit_first='', explicit_last=''):
    prefix = _clean(explicit_prefix)
    first_name = _clean(explicit_first)
    last_name = _clean(explicit_last)
    full = _clean(full_name_str)

    if full and not first_name:
        parts = full.split()
        first_token = parts[0] if parts else ''
        last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''
        first_name = first_token

    for pf in ('เด็กชาย', 'เด็กหญิง', 'นางสาว', 'นาย', 'นาง', 'ด.ช.', 'ด.ญ.', 'น.ส.'):
        if first_name.startswith(pf) and len(first_name) > len(pf):
            prefix = pf
            first_name = first_name[len(pf):].strip()
            break

    return prefix, first_name, last_name


def read_student_rows_with_meta(uploaded_file):
    rows = _load_sheet_rows(uploaded_file)
    non_empty = [
        (idx, row)
        for idx, row in enumerate(rows, start=1)
        if row is not None and any(_clean(v) != '' for v in row)
    ]
    if not non_empty:
        raise ValueError('ไฟล์ว่างเปล่า')

    header_pos = None
    header_resolved = []
    for pos, (_, row) in enumerate(non_empty[:5]):
        resolved = [_resolve_header(c) for c in row]
        canon_set = {k for k in resolved if k}
        has_id = 'student_id' in canon_set
        has_name = 'full_name' in canon_set or 'first_name' in canon_set
        if has_id and has_name:
            header_pos = pos
            header_resolved = resolved
            break

    if header_pos is None:
        # กรณีไม่มีหัวตาราง (รองรับรูปแบบ 5 คอลัมน์: ชั้น, ห้อง, เลขที่, เลขประจำตัว, ชื่อ-นามสกุล)
        result = []
        for idx, row in non_empty:
            vals = [_clean(c) for c in row]
            if len(vals) >= 5 and vals[3].isdigit():
                result.append((idx, {
                    'grade_level': vals[0],
                    'room_code': vals[1],
                    'class_number': vals[2],
                    'student_id': vals[3],
                    'full_name': ' '.join(vals[4:]),
                }))
            elif len(vals) >= 4 and vals[2].isdigit():
                result.append((idx, {
                    'grade_level': '4',
                    'room_code': vals[0],
                    'class_number': vals[1],
                    'student_id': vals[2],
                    'full_name': ' '.join(vals[3:]),
                }))
        if not result:
            raise ValueError('ไฟล์ขาดคอลัมน์บังคับ: เลขประจำตัว, ชื่อ-นามสกุล')
        return result, {
            'has_header': False,
            'matched_columns': [],
            'ignored_columns': [],
            'present_fields': ['grade_level', 'room_code', 'class_number', 'student_id', 'full_name'],
        }

    header_row = non_empty[header_pos][1]
    col_mapping = {}
    matched_columns = []
    ignored_columns = []
    seen_canon = set()
    for i, raw_h in enumerate(header_row):
        orig_h = _clean(raw_h)
        canon = header_resolved[i] if i < len(header_resolved) else None
        if canon and canon not in seen_canon:
            seen_canon.add(canon)
            col_mapping[i] = canon
            matched_columns.append({
                'col_letter': get_column_letter(i + 1),
                'header': orig_h,
                'field': canon,
                'label': STUDENT_FIELD_LABELS_TH.get(canon, canon),
            })
        elif orig_h:
            ignored_columns.append(orig_h)

    result = []
    for rownum, row in non_empty[header_pos + 1:]:
        data = {canon: (row[i] if i < len(row) else None) for i, canon in col_mapping.items()}
        if all(_clean(v) == '' for v in data.values()):
            continue
        result.append((rownum, data))

    return result, {
        'has_header': True,
        'matched_columns': matched_columns,
        'ignored_columns': ignored_columns,
        'present_fields': list(seen_canon),
    }


def _parse_student_row(rownum, d, seen):
    sid = _clean(d.get('student_id')).replace(' ', '')
    if sid.lower().startswith('s') and sid[1:].isdigit():
        sid = sid[1:]

    grade_raw = _clean(d.get('grade_level')) or '4'
    grade = re.sub(r'[^0-9]', '', grade_raw) or '4'

    room_raw = _clean(d.get('room_code')) or '1'
    if '/' in room_raw:
        room_raw = room_raw.split('/')[-1].strip()
    room_digits = re.sub(r'[^0-9]', '', room_raw)
    rcode = f'{int(room_digits):02d}' if room_digits else '01'

    cno_raw = _clean(d.get('class_number')) or '0'
    try:
        cno = int(float(cno_raw))
    except ValueError:
        cno = 0

    prefix, fname, lname = _split_prefix_first_last(
        d.get('full_name'),
        explicit_prefix=d.get('prefix'),
        explicit_first=d.get('first_name'),
        explicit_last=d.get('last_name'),
    )

    group_name = _clean(d.get('group_name')) or ('กลุ่ม A' if cno % 2 == 1 else 'กลุ่ม B')
    email = _clean(d.get('email')).lower() or (f's{sid}@debsirin.ac.th' if sid else '')

    errors = []
    warnings = []
    if not re.match(r'^\d{5}$', sid):
        errors.append(f'เลขประจำตัว "{sid}" ต้องเป็นตัวเลข 5 หลัก')
    if not fname:
        errors.append('ไม่พบชื่อนักเรียน')
    if sid in seen:
        warnings.append(f'เลขประจำตัวซ้ำกับแถว {seen[sid]} (จะใช้แถวล่าสุด)')
    if sid:
        seen[sid] = rownum

    return {
        'rownum': rownum,
        'grade_level': grade,
        'room_code': rcode,
        'room_display': f'ม.{grade}/{int(rcode)}' if rcode.isdigit() else rcode,
        'class_number': cno,
        'student_id': sid,
        'title_prefix': prefix,
        'first_name': fname,
        'last_name': lname,
        'full_name_display': ' '.join(p for p in (prefix, fname, lname) if p),
        'group_name': group_name,
        'email': email,
        'valid': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
    }


def preview_students(uploaded_file, academic_year=None):
    """ตรวจสอบข้อมูลนักเรียนในไฟล์ CSV/Excel ก่อนนำเข้าจริง (แยกตามปีการศึกษา รองรับเลขประจำตัวซ้ำกับปีเก่า)"""
    from .services import get_current_academic_year

    target_year = (academic_year or get_current_academic_year()).strip()
    rows, meta = read_student_rows_with_meta(uploaded_file)
    preview_list = []
    valid_count = 0
    invalid_count = 0
    seen = {}
    existing_ids = set(
        User.objects.filter(role=User.ROLE_STUDENT, academic_year=target_year).values_list('student_id', flat=True)
    )
    prev_year_ids = set(
        User.objects.filter(role=User.ROLE_STUDENT).exclude(academic_year=target_year).values_list('student_id', flat=True)
    )

    for rownum, d in rows:
        parsed = _parse_student_row(rownum, d, seen)
        parsed['is_update'] = parsed['student_id'] in existing_ids
        parsed['from_prev_year'] = (parsed['student_id'] in prev_year_ids and not parsed['is_update'])
        if parsed['valid']:
            valid_count += 1
        else:
            invalid_count += 1
        preview_list.append(parsed)

    return {
        'academic_year': target_year,
        'rows': preview_list,
        'total': len(rows),
        'valid_count': valid_count,
        'invalid_count': invalid_count,
        'matched_columns': meta['matched_columns'],
        'ignored_columns': meta['ignored_columns'],
        'present_fields': meta['present_fields'],
    }


def _save_parsed_student_row(r, rooms_cache, academic_year=None):
    from .services import generate_unique_student_username, get_current_academic_year

    target_year = (academic_year or get_current_academic_year()).strip()
    sid = r['student_id']
    rcode = r['room_code']
    grade = r.get('grade_level', '4') or '4'
    if rcode not in rooms_cache:
        room_num = int(rcode) if rcode.isdigit() else rcode
        room_obj, _ = Classroom.objects.get_or_create(
            code=rcode,
            defaults={'name': f'ม.{grade}/{room_num}', 'grade_level': grade}
        )
        rooms_cache[rcode] = room_obj
    room_obj = rooms_cache[rcode]

    user = User.objects.filter(role=User.ROLE_STUDENT, academic_year=target_year, student_id=sid).first()
    if user:
        user.student_id = sid
        user.academic_year = target_year
        if not user.username:
            user.username = generate_unique_student_username(sid, target_year)
        user.email = r['email'] or f's{sid}@debsirin.ac.th'
        user.title_prefix = r['title_prefix']
        user.first_name = r['first_name']
        user.last_name = r['last_name']
        user.classroom = room_obj
        user.room_code = rcode
        user.class_number = r['class_number']
        user.group_name = r['group_name']
        user.is_active = True
        user.save()
        return 'updated'

    user = User(
        username=generate_unique_student_username(sid, target_year),
        student_id=sid,
        academic_year=target_year,
        email=r['email'] or f's{sid}@debsirin.ac.th',
        title_prefix=r['title_prefix'],
        first_name=r['first_name'],
        last_name=r['last_name'],
        classroom=room_obj,
        room_code=rcode,
        class_number=r['class_number'],
        group_name=r['group_name'],
        role=User.ROLE_STUDENT,
        is_active=True,
        total_score=0.0,
    )
    user.set_password(sid)
    user.save()
    return 'created'


def Q_student_lookup(sid):
    from django.db.models import Q
    return Q(student_id=sid) | Q(username=f's{sid}')


def import_students_from_preview(pdata, academic_year=None):
    from .services import get_current_academic_year

    target_year = (academic_year or pdata.get('academic_year') or get_current_academic_year()).strip()
    rows = pdata.get('rows', [])
    created, updated, errors = 0, 0, []
    rooms_cache = {c.code: c for c in Classroom.objects.all()}
    with transaction.atomic():
        for r in rows:
            if not r.get('valid'):
                errors.append((r.get('rownum'), '; '.join(r.get('errors', []))))
                continue
            try:
                status = _save_parsed_student_row(r, rooms_cache, academic_year=target_year)
                if status == 'created':
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append((r.get('rownum'), str(e)))
    return {'created': created, 'updated': updated, 'errors': errors, 'total': len(rows), 'academic_year': target_year}


def import_students_file(uploaded_file, academic_year=None):
    pdata = preview_students(uploaded_file, academic_year=academic_year)
    return import_students_from_preview(pdata, academic_year=pdata.get('academic_year'))
