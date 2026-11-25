"""
บริการหลักของระบบ COMSC4 (Services & Game Engines)
1. Image Rotate & Crop Engine (Pillow + Base64 / File Upload)
2. DSBasicSorting Engine (Bubble, Selection, Insertion Sort step-by-step + Formula + Top X Auto-Reward)
3. Bingo Engine (สร้างตารางพร้อมเงื่อนไขมุม/กลาง, Guard ตรวจสอบการเลือกเลขของนักเรียนไม่ให้มุม/กลาง/Bingo ทันที, ตรวจผู้ชนะ, สถิติ)
4. Debsirin Lotto Engine (สุ่มเลขประจำตัวนักเรียน 5 หลัก + ให้คะแนนบวกอัตโนมัติ)
5. AI Quiz Generator (รองรับ Gemini API / Claude API และคลังข้อสอบวิทยาการคำนวณอัจฉริยะ)
6. Initial Data Seeder (นำเข้ารายชื่อนักเรียนโรงเรียนเทพศิรินทร์จาก data.txt + สร้างกฎคะแนนมาตรฐาน)
"""
import base64
import io
import json
import math
import os
import random
import re
import time as py_time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, time, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.utils.html import strip_tags

from .models import (
    AcademicYear,
    User, UserProfile, UserSession, LabRoomLayout, Classroom, ScorePeriod, ScoreRule, ScoreLog,
    SortingActivity, SortingExamRecord, SortingScore,
    BingoRoom, BingoCard, BingoDrawnNumber, BingoWinner,
    LottoSession, LottoResult,
    LessonContent, AIQuiz, AIQuizQuestion, LearningActivity,
    ActivityAnswerKeyItem, ActivityExtensionRequest, ActivityGradingTier,
    ActivityGroup, ActivityGroupMember,
    ActivitySubmission, ActivityPresentationDraw, ActivityBulkGradeBatch,
    AttendanceRecord, AttendanceSession, StudentMaterialCheck, SystemSetting, ScoreRankTier,
)


# =============================================================================
# 1. ระบบหมุนและ Crop รูปประจำตัว (A.4 Image Rotate & Crop)
# =============================================================================
def process_cropped_or_rotated_avatar(user, uploaded_file=None, cropped_base64=None, rotate_deg=0):
    """
    รองรับทั้ง:
    1) ภาพที่ผ่านการหมุนและ Crop 1:1 จากหน้าเว็บ (Canvas Data URL Base64)
    2) ไฟล์รูปภาพอัปโหลดปกติ + ระบุองศาการหมุน (0, 90, 180, 270 หรือองศาใดๆ) แล้ว Center-Crop 1:1
    """
    raw_bytes = None
    if cropped_base64 and ',' in cropped_base64:
        header, b64_data = cropped_base64.split(',', 1)
        raw_bytes = base64.b64decode(b64_data)
    elif uploaded_file:
        uploaded_file.seek(0)
        raw_bytes = uploaded_file.read()

    if not raw_bytes:
        raise ValueError('ไม่พบข้อมูลรูปภาพที่อัปโหลด')

    pil_img = Image.open(io.BytesIO(raw_bytes))
    pil_img = ImageOps.exif_transpose(pil_img)
    if pil_img.mode != 'RGB':
        pil_img = pil_img.convert('RGB')

    if rotate_deg and not cropped_base64:
        pil_img = pil_img.rotate(-float(rotate_deg), expand=True, resample=Image.Resampling.BICUBIC)

    # บังคับสัดส่วน 1:1 (Square Center Crop)
    w, h = pil_img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    pil_img = pil_img.crop((left, top, left + side, top + side))
    pil_img = pil_img.resize((480, 480), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    pil_img.save(buf, format='JPEG', quality=88, optimize=True)
    out_bytes = buf.getvalue()

    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(user.student_id or user.username))
    filename = f'avatar_{safe_id}_{int(random.random() * 10000)}.jpg'
    user.profile_photo.save(filename, ContentFile(out_bytes), save=True)

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.profile_image_url = user.profile_photo.url
    profile.save()
    return user.profile_photo.url


# =============================================================================
# 2. ระบบช่วงคะแนน ระบบให้คะแนนตามกฎคะแนนอัตโนมัติ และเช็คหนังสือ/สมุด
# =============================================================================
def get_active_score_period(academic_year=None):
    """คืนช่วงคะแนนที่ยังเปิดใช้งานอยู่ (ยังไม่สรุปคะแนน) ในปีการศึกษาปัจจุบัน หากไม่มีให้สร้างช่วงถัดไปอัตโนมัติ"""
    yr = (academic_year or get_current_academic_year()).strip()
    period = ScorePeriod.objects.filter(academic_year=yr, is_finalized=False).order_by('order_no', 'id').first()
    if period:
        return period
    max_ord = ScorePeriod.objects.filter(academic_year=yr).aggregate(m=Max('order_no'))['m'] or 0
    return ScorePeriod.objects.create(
        name=f'ช่วงคะแนนที่ {max_ord + 1}',
        academic_year=yr,
        order_no=max_ord + 1,
        max_score=20.0,
        is_finalized=False,
    )


def finalize_score_period(period, teacher_user=None):
    """สรุปคะแนนและล็อกช่วงคะแนนปัจจุบัน แล้วเปิดเตรียมช่วงคะแนนถัดไปอัตโนมัติ"""
    if not period.is_finalized:
        period.is_finalized = True
        period.finalized_at = timezone.now()
        period.finalized_by = teacher_user
        period.save(update_fields=['is_finalized', 'finalized_at', 'finalized_by'])
    next_period = get_active_score_period()
    return period, next_period


def award_points_by_rule(student, rule_code, default_name, default_points, source_module, custom_reason='', awarded_by=None, score_period=None):
    target_period = score_period or get_active_score_period()
    if target_period.is_finalized:
        raise ValueError(f'ช่วงคะแนน "{target_period.name}" สรุปคะแนนและล็อกไว้แล้ว ไม่สามารถบวกหรือลบคะแนนได้')

    rule, _ = ScoreRule.objects.get_or_create(
        code=rule_code,
        defaults={
            'name': default_name,
            'rule_type': ScoreRule.TYPE_PLUS if default_points >= 0 else ScoreRule.TYPE_MINUS,
            'points': abs(float(default_points)),
            'is_auto': True,
            'note': f'ระบบให้อัตโนมัติจากกิจกรรม {source_module}',
        }
    )
    pts = rule.signed_points if rule.is_active else float(default_points)
    reason = custom_reason or rule.name
    log = ScoreLog.objects.create(
        student=student,
        rule=rule,
        score_period=target_period,
        academic_year=getattr(target_period, 'academic_year', '') or get_current_academic_year(),
        points=pts,
        reason=reason,
        source_module=source_module,
        awarded_by=awarded_by,
    )
    return log


def toggle_student_material_check(
    classroom,
    student,
    check_type,
    teacher_user=None,
    check_date=None,
    seat_row=0,
    seat_col=0,
    session=None,
    has_book=None,
    has_notebook=None,
    note=None,
):
    """เช็คหรือยกเลิกการนำหนังสือ/สมุดของนักเรียนในแผนผัง พร้อมเพิ่ม/ลบคะแนนจากกฎคะแนนอัตโนมัติ"""
    cdate = check_date or timezone.localdate()
    cur_yr = getattr(session, 'academic_year', '') or get_current_academic_year()
    chk = None
    if session is not None:
        chk = StudentMaterialCheck.objects.filter(
            classroom=classroom, student=student, session=session
        ).order_by('-id').first()
        if chk is None and not session.is_summarized:
            chk = StudentMaterialCheck.objects.filter(
                classroom=classroom, student=student, check_date=cdate,
                session__isnull=True, is_summarized=False,
            ).order_by('-id').first()
            if chk is not None:
                chk.session = session
        if chk is None:
            chk = StudentMaterialCheck.objects.create(
                classroom=classroom,
                student=student,
                check_date=cdate,
                session=session,
                is_summarized=bool(session.is_summarized),
                academic_year=cur_yr,
                seat_row=seat_row or 0,
                seat_col=seat_col or 0,
                checked_by=teacher_user,
            )
    else:
        chk = StudentMaterialCheck.objects.filter(
            classroom=classroom, student=student, check_date=cdate,
            session__isnull=True, is_summarized=False,
        ).order_by('-id').first()
        if chk is None:
            chk = StudentMaterialCheck.objects.create(
                classroom=classroom,
                student=student,
                check_date=cdate,
                session=None,
                is_summarized=False,
                academic_year=cur_yr,
                seat_row=seat_row or 0,
                seat_col=seat_col or 0,
                checked_by=teacher_user,
            )

    if check_type == 'toggle_book':
        new_book, new_notebook = (not chk.has_book), chk.has_notebook
    elif check_type == 'toggle_notebook':
        new_book, new_notebook = chk.has_book, (not chk.has_notebook)
    elif check_type == 'toggle_both':
        desired_both = not (chk.has_book and chk.has_notebook)
        new_book, new_notebook = desired_both, desired_both
    elif check_type == 'both_on':
        new_book, new_notebook = True, True
    elif check_type == 'both_off':
        new_book, new_notebook = False, False
    else:
        new_book = chk.has_book if has_book is None else bool(has_book)
        new_notebook = chk.has_notebook if has_notebook is None else bool(has_notebook)

    for kind, desired, rule_code, rule_name in [
        ('book', new_book, 'bring_book', 'นำหนังสือเรียนมาเรียน'),
        ('notebook', new_notebook, 'bring_notebook', 'นำสมุดมาเรียน'),
    ]:
        log_field = f'{kind}_score_log'
        flag_field = f'has_{kind}'
        cur_log = getattr(chk, log_field)
        if desired and not cur_log:
            log = award_points_by_rule(
                student=student,
                rule_code=rule_code,
                default_name=rule_name,
                default_points=1.0,
                source_module='material',
                custom_reason=f'{rule_name} ({classroom.name} วันที่ {cdate.strftime("%d/%m/%Y")})',
                awarded_by=teacher_user,
            )
            setattr(chk, log_field, log)
        elif not desired and cur_log:
            cur_log.delete()
            setattr(chk, log_field, None)
        setattr(chk, flag_field, desired)

    if seat_row:
        chk.seat_row = int(seat_row)
    if seat_col:
        chk.seat_col = int(seat_col)
    if session is not None:
        chk.session = session
    if note is not None:
        chk.note = str(note).strip()[:200]
    if teacher_user is not None:
        chk.checked_by = teacher_user
    chk.save()
    student.recalculate_total_score()
    return chk


VALID_AUTO_SEAT_DIRECTIONS = (
    'col_front_to_back',
    'col_back_to_front',
    'col_front_to_back_rtl',
    'col_back_to_front_rtl',
    'col_snake',
    'row_front_to_back',
)


def build_auto_seat_numbers(
    seat_rows,
    seat_cols,
    student_numbers,
    disabled_seats=None,
    direction='col_front_to_back',
    per_col=None,
):
    """
    จัดเลขที่นั่งอัตโนมัติลงในผังห้องเรียน โดยมีแนวตั้ง (Column-first) เป็นค่าเริ่มต้น
    รองรับทิศทาง: col_front_to_back, col_back_to_front, col_front_to_back_rtl,
    col_back_to_front_rtl, col_snake, row_front_to_back
    และ per_col: None/'' (เต็มคอลัมน์), 'even' (แบ่งเท่ากัน), หรือจำนวนเต็ม k
    """
    seat_rows = max(1, int(seat_rows or 1))
    seat_cols = max(1, int(seat_cols or 1))
    disabled = {str(x) for x in (disabled_seats or [])}
    nums = []
    for n in (student_numbers or []):
        try:
            val = int(n)
            if val > 0:
                nums.append(val)
        except (TypeError, ValueError):
            continue

    if not nums:
        return {}

    if direction == 'row_front_to_back':
        seat_map = {}
        idx = 0
        for r in range(1, seat_rows + 1):
            for c in range(1, seat_cols + 1):
                key = f'{r}_{c}'
                if key in disabled:
                    continue
                if idx < len(nums):
                    seat_map[key] = nums[idx]
                    idx += 1
        return seat_map

    col_seq = (
        list(range(seat_cols, 0, -1))
        if direction in ('col_front_to_back_rtl', 'col_back_to_front_rtl')
        else list(range(1, seat_cols + 1))
    )
    back_to_front = direction in ('col_back_to_front', 'col_back_to_front_rtl')

    active_cols_data = []
    for col_idx, c in enumerate(col_seq):
        if direction == 'col_snake':
            seats_in_col = [f'{r}_{c}' for r in range(1, seat_rows + 1) if f'{r}_{c}' not in disabled]
        else:
            row_seq = range(seat_rows, 0, -1) if back_to_front else range(1, seat_rows + 1)
            seats_in_col = [f'{r}_{c}' for r in row_seq if f'{r}_{c}' not in disabled]
        if seats_in_col:
            active_cols_data.append((col_idx, seats_in_col))

    if not active_cols_data:
        return {}

    per_col_str = str(per_col).strip().lower() if per_col is not None else ''
    if per_col_str == 'even':
        caps = [len(seats) for _, seats in active_cols_data]
        if sum(caps) <= len(nums):
            quotas = caps[:]
        else:
            quotas = [0] * len(caps)
            remaining = len(nums)
            while remaining > 0:
                avail = [i for i in range(len(caps)) if quotas[i] < caps[i]]
                if not avail:
                    break
                q, rem = divmod(remaining, len(avail))
                if q == 0:
                    for pos, i in enumerate(avail[:rem]):
                        quotas[i] += 1
                    remaining = 0
                else:
                    added = 0
                    for pos, i in enumerate(avail):
                        want = q + (1 if pos < rem else 0)
                        give = min(want, caps[i] - quotas[i])
                        quotas[i] += give
                        added += give
                    remaining -= added
    else:
        fixed_limit = None
        if per_col_str and per_col_str.isdigit() and int(per_col_str) > 0:
            fixed_limit = int(per_col_str)
        quotas = [
            min(fixed_limit, len(seats)) if fixed_limit is not None else len(seats)
            for _, seats in active_cols_data
        ]

    seat_map = {}
    idx = 0
    for (col_idx, seats_in_col), limit in zip(active_cols_data, quotas):
        if idx >= len(nums) or limit <= 0:
            break
        chosen = seats_in_col[:limit]
        if direction == 'col_snake' and (col_idx % 2 == 1):
            chosen = list(reversed(chosen))
        for key in chosen:
            if idx >= len(nums):
                break
            seat_map[key] = nums[idx]
            idx += 1

    return seat_map


DEFAULT_LAB_ROOM_NAMES = ('ห้อง 134', 'ห้อง 135', 'ห้อง 136')


def ensure_default_lab_rooms():
    """สร้างแผนผังห้องเริ่มต้น (ห้อง 134, 135, 136) พร้อมเลขเครื่องแนวตั้ง และผูกห้องเรียนที่ยังไม่เลือกให้ใช้ห้อง 134"""
    labs = []
    for name in DEFAULT_LAB_ROOM_NAMES:
        lab, created = LabRoomLayout.objects.get_or_create(
            name=name,
            defaults={
                'seat_rows': 6,
                'seat_cols': 7,
                'board_position': LabRoomLayout.BOARD_BOTTOM,
            },
        )
        ld = lab.get_layout_dict()
        if created or ('machine_labels' not in ld and 'custom_labels' not in ld):
            num_map = build_auto_seat_numbers(
                lab.seat_rows,
                lab.seat_cols,
                list(range(1, lab.seat_rows * lab.seat_cols + 1)),
                direction='col_front_to_back',
            )
            ld['machine_labels'] = {k: f'{v:02d}' for k, v in num_map.items()}
            ld.setdefault('row_order', 'bottom_to_top')
            ld.setdefault('auto_direction', 'col_front_to_back')
            ld.setdefault('disabled_seats', [])
            lab.layout_config = json.dumps(ld)
            lab.save(update_fields=['layout_config'])
        labs.append(lab)

    default_lab = labs[0]
    unassigned = Classroom.objects.filter(lab_room__isnull=True)
    if unassigned.exists():
        unassigned.update(
            lab_room=default_lab,
            seat_rows=default_lab.seat_rows,
            seat_cols=default_lab.seat_cols,
            board_position=default_lab.board_position,
        )
    return labs


# =============================================================================
# 3. ระบบกิจกรรมเรียงลำดับ (DSBasicSorting Engine)
# ถอดแบบอัลกอริทึมจาก DSbasicsorting/Form1.cs 100%
# =============================================================================
def build_sorting_steps(initial_numbers, mode):
    """
    จำลองขั้นตอนการเรียงลำดับทีละรอบ (Round 1..N) เหมือนใน DSbasicsorting/Form1.cs
    และรองรับโหมดลากเรียงครั้งเดียว (asc / desc)
    คืนค่า list ของแต่ละรอบ: steps[0] = รอบที่ 1, steps[1] = รอบที่ 2 ...
    """
    arr = [int(x) for x in initial_numbers]
    n = len(arr)
    is_asc = 'desc' not in mode

    if mode in ('asc', 'desc'):
        target = sorted(arr) if is_asc else sorted(arr, reverse=True)
        return [target]

    steps = []
    cur = list(arr)

    if mode.startswith('bubble'):
        # เหมือน BuildBubbleSort() ใน Form1.cs
        for _i in range(1, n + 1):
            cur = list(cur)
            for j in range(0, n - 1):
                need_swap = (is_asc and cur[j] > cur[j + 1]) or (not is_asc and cur[j] < cur[j + 1])
                if need_swap:
                    cur[j], cur[j + 1] = cur[j + 1], cur[j]
            steps.append(list(cur))

    elif mode.startswith('selection'):
        # เหมือน BuildSelectionSort() ใน Form1.cs
        for i in range(1, n + 1):
            cur = list(cur)
            current_pos = i - 1
            target_idx = current_pos
            for j in range(current_pos + 1, n):
                if (is_asc and cur[j] < cur[target_idx]) or (not is_asc and cur[j] > cur[target_idx]):
                    target_idx = j
            cur[current_pos], cur[target_idx] = cur[target_idx], cur[current_pos]
            steps.append(list(cur))

    else:
        # เหมือน BuildInsertionSort() ใน Form1.cs
        for i in range(1, n + 1):
            cur = list(cur)
            if i > 1:
                key = cur[i - 1]
                j = i - 2
                if is_asc:
                    while j >= 0 and cur[j] > key:
                        cur[j + 1] = cur[j]
                        j -= 1
                else:
                    while j >= 0 and cur[j] < key:
                        cur[j + 1] = cur[j]
                        j -= 1
                cur[j + 1] = key
            steps.append(list(cur))

    return steps


def calculate_sorting_score(right_rounds, wrong_attempts, time_used_sec, unit_count):
    """
    สูตรคิดคะแนนจาก DSbasicsorting/Form1.cs (บรรทัด 587):
    double sum = ((right * 10) - (wrong * 2)) - (time / unit);
    """
    unit = max(1, int(unit_count or 7))
    raw = ((float(right_rounds) * 10.0) - (float(wrong_attempts) * 2.0)) - (float(time_used_sec) / float(unit))
    return round(raw, 2)


def normalize_sorting_room_filter(room_filter):
    """แปลงค่าตัวกรอง Sorting ให้เป็น '' (ทุกชั้น/ห้อง), 'grade:<ระดับชั้น>', '01' หรือหลายห้อง '01,02,03'"""
    if isinstance(room_filter, (list, tuple, set)):
        items = [str(x or '').strip() for x in room_filter if str(x or '').strip()]
        if not items or any(x.lower() == 'all' for x in items):
            return ''
        parts = []
        for x in items:
            parts.extend(p.strip() for p in x.split(',') if p.strip())
    else:
        rf = (str(room_filter) if room_filter is not None else '').strip()
        if not rf or rf.lower() == 'all':
            return ''
        if rf.lower().startswith('grade:'):
            lvl = rf.split(':', 1)[1].strip()
            return f'grade:{lvl}' if lvl else ''
        parts = [p.strip() for p in rf.split(',') if p.strip()]

    if not parts or any(p.lower() == 'all' for p in parts):
        return ''
    if len(parts) == 1 and parts[0].lower().startswith('grade:'):
        lvl = parts[0].split(':', 1)[1].strip()
        return f'grade:{lvl}' if lvl else ''
    codes = sorted({p.zfill(2) if p.isdigit() else p for p in parts if not p.lower().startswith('grade:')})
    return ','.join(codes)


def filter_sorting_qs_by_room(qs, room_filter=''):
    """กรอง QuerySet ของ SortingScore ให้ตรงเฉพาะชั้น/ห้อง (หรือหลายห้อง) ที่เลือกเท่านั้น"""
    rf = normalize_sorting_room_filter(room_filter)
    if not rf:
        return qs
    if rf.startswith('grade:'):
        lvl = rf.split(':', 1)[1]
        return qs.filter(student__classroom__grade_level=lvl)
    codes = [c for c in rf.split(',') if c]
    if len(codes) == 1:
        return qs.filter(student__room_code=codes[0])
    return qs.filter(student__room_code__in=codes)


def room_matches_sorting_filter(room_code, grade_level='', room_filter=''):
    """ตรวจสอบว่าห้องเรียนหรือระดับชั้นของนักเรียนตรงกับเงื่อนไขกรองของ Sorting หรือไม่"""
    rf = normalize_sorting_room_filter(room_filter)
    if not rf:
        return True
    if rf.startswith('grade:'):
        return str(grade_level or '').strip() == rf.split(':', 1)[1]
    rc = str(room_code or '').strip()
    if rc.isdigit():
        rc = rc.zfill(2)
    return rc in [c for c in rf.split(',') if c]


def get_sorting_room_filter_label(room_filter=''):
    """คืนชื่อแสดงผลของตัวกรองชั้น/ห้องใน Sorting"""
    rf = normalize_sorting_room_filter(room_filter)
    if not rf:
        return 'ทุกชั้น/ห้อง'
    if rf.startswith('grade:'):
        return f'ระดับชั้น ม.{rf.split(":", 1)[1]} ทั้งหมด'
    codes = [c for c in rf.split(',') if c]
    name_map = dict(Classroom.objects.filter(code__in=codes).values_list('code', 'name'))
    names = [name_map.get(c, f'ห้อง {c}') for c in codes]
    if len(names) <= 3:
        return ', '.join(names)
    return ', '.join(names[:3]) + f' (+{len(names) - 3} ห้อง)'


def reward_sorting_exam_top_x(activity, teacher_user=None, room_filter='', close_exam=None):
    """
    เมื่อผู้ดูแลปิดโหมดสอบ หรือกดมอบคะแนน Top X คนสูงสุด:
    คัดเลือกเฉพาะผู้เล่นในชั้น/ห้องที่เลือก (หรือทั้งหมดหากไม่ได้กรอง) แล้วมอบคะแนนตามกฎ
    """
    top_x = max(1, int(activity.top_x_reward or 5))
    rf = normalize_sorting_room_filter(room_filter)
    scope_label = get_sorting_room_filter_label(rf)

    base_qs = SortingScore.objects.filter(
        activity=activity, status='completed', session_no=activity.session_no
    ).select_related('student', 'student__classroom')
    exam_qs = filter_sorting_qs_by_room(base_qs, rf)
    if not exam_qs.exists():
        fallback_qs = SortingScore.objects.filter(
            activity=activity, status='completed'
        ).select_related('student', 'student__classroom')
        exam_qs = filter_sorting_qs_by_room(fallback_qs, rf)

    already_rewarded_sids = set(
        exam_qs.filter(is_top_rewarded=True).values_list('student_id', flat=True)
    )

    best_by_student = {}
    for sc in exam_qs:
        eff_score = max(float(sc.cumulative_score or 0.0), float(sc.score or 0.0))
        prev = best_by_student.get(sc.student_id)
        if prev is None or (eff_score, sc.play_round_no, sc.id) > (prev[0], prev[1].play_round_no, prev[1].id):
            best_by_student[sc.student_id] = (eff_score, sc)

    ranked = sorted(
        best_by_student.values(),
        key=lambda item: (-item[0], item[1].time_used, item[1].attempts, item[1].student.room_code or '', item[1].student.class_number),
    )
    winners = [item[1] for item in ranked[:top_x]]

    rewarded_list = []
    for rank, sc in enumerate(winners, start=1):
        display_pts = max(float(sc.cumulative_score or 0.0), float(sc.score or 0.0))
        if sc.student_id not in already_rewarded_sids and not sc.is_top_rewarded:
            already_rewarded_sids.add(sc.student_id)
            sc.is_top_rewarded = True
            sc.save(update_fields=['is_top_rewarded'])
            award_points_by_rule(
                student=sc.student,
                rule_code='sorting_top_exam',
                default_name='ทำกิจกรรมเรียงลำดับสูงสุดในรอบสอบ',
                default_points=2.0,
                source_module='sorting',
                custom_reason=f'ทำกิจกรรมเรียงลำดับสูงสุดในรอบสอบ ({activity.title} · {scope_label} · อันดับที่ {rank} ได้ {display_pts:.2f} คะแนน) [SORT_ACT:{activity.id}]',
                awarded_by=teacher_user,
            )
            rewarded_list.append(sc)

    should_close = (rf == '') if close_exam is None else bool(close_exam)
    if should_close:
        activity.is_exam_mode = False
        activity.exam_closed_rewarded = True
        activity.save(update_fields=['is_exam_mode', 'exam_closed_rewarded'])
    return winners, rewarded_list


def finalize_sorting_exam_record(activity, room_filter='', teacher_user=None, status='finished'):
    """ปิดรอบสอบ (จบการสอบ หรือปิดชั่วคราว) พร้อมเก็บสถิติและอันดับคะแนนของรอบนั้นลง SortingExamRecord"""
    rf = normalize_sorting_room_filter(room_filter if room_filter is not None else (activity.broadcast_room_code or ''))
    scope_lbl = get_sorting_room_filter_label(rf)
    sess_no = int(activity.session_no or 1)

    base_qs = SortingScore.objects.filter(
        activity=activity, session_no=sess_no, status='completed'
    ).select_related('student', 'student__classroom')
    filtered_qs = filter_sorting_qs_by_room(base_qs, rf)

    best_by_student = {}
    for sc in filtered_qs:
        eff_score = round(max(float(sc.cumulative_score or 0.0), float(sc.score or 0.0)), 2)
        prev = best_by_student.get(sc.student_id)
        if prev is None or (eff_score, sc.play_round_no, sc.id) > (prev[0], prev[1].play_round_no, prev[1].id):
            best_by_student[sc.student_id] = (eff_score, sc)

    ranked = sorted(
        best_by_student.values(),
        key=lambda item: (-item[0], item[1].time_used, item[1].attempts, item[1].student.room_code or '', item[1].student.class_number),
    )

    stats_rows = []
    scores_vals = []
    for rank, (eff_sc, sc) in enumerate(ranked, start=1):
        scores_vals.append(eff_sc)
        stats_rows.append({
            'rank': rank,
            'student_id': sc.student.student_id or sc.student.username,
            'name': sc.student.get_full_name(),
            'room': sc.student.room_display,
            'class_number': sc.student.class_number,
            'score': eff_sc,
            'time_used': round(float(sc.time_used or 0.0), 1),
            'attempts': int(sc.attempts or 0),
            'detail': sc.algorithm_detail or '',
        })

    participant_cnt = len(stats_rows)
    avg_sc = round(sum(scores_vals) / participant_cnt, 2) if participant_cnt else 0.0
    max_sc = round(max(scores_vals), 2) if participant_cnt else 0.0

    rec = SortingExamRecord.objects.filter(activity=activity, session_no=sess_no).order_by('-id').first()
    if not rec:
        rec = SortingExamRecord(
            activity=activity,
            session_no=sess_no,
            title=f'รอบสอบ #{sess_no} — {activity.title}',
            created_by=teacher_user,
        )
    rec.room_filter = rf
    rec.room_label = scope_lbl
    rec.sort_type = activity.broadcast_sort or 'bubble'
    rec.sort_order = activity.broadcast_order or 'asc'
    rec.unit_count = int(activity.broadcast_unit or activity.unit_count or 7)
    rec.status = status
    rec.participant_count = participant_cnt
    rec.avg_score = avg_sc
    rec.max_score = max_sc
    rec.stats_json = json.dumps(stats_rows, ensure_ascii=False)
    rec.ended_at = timezone.now() if status == SortingExamRecord.STATUS_FINISHED else None
    rec.save()

    if status in (SortingExamRecord.STATUS_FINISHED, SortingExamRecord.STATUS_PAUSED):
        activity.is_exam_mode = False
        activity.save(update_fields=['is_exam_mode'])

    return rec


# =============================================================================
# 4. ระบบ Bingo Engine + Guard ตรวจสอบการเลือกเลขของนักเรียน
# =============================================================================
def generate_student_bingo_grid(room, custom_numbers=None):
    """
    สร้างหรือตรวจสอบตาราง Bingo ของนักเรียนตามขนาด (3x3, 4x4, 5x5) และช่วงตัวเลข
    พร้อมบังคับเงื่อนไขตำแหน่งมุมหรือตำแหน่งกลางตามที่ผู้ดูแลตั้งค่า (G.2)
    """
    n = int(room.size or 5)
    total_cells = n * n
    mn, mx = room.get_min_max_range()
    pool = list(range(mn, mx + 1))
    if len(pool) < total_cells:
        pool = list(range(1, total_cells + 10))

    corners, centers, special_indices = room.get_corner_and_center_indices()

    if custom_numbers and len(custom_numbers) == total_cells:
        nums = [int(x) for x in custom_numbers]
        if len(set(nums)) != total_cells:
            raise ValueError('ตัวเลขในตาราง Bingo ต้องไม่ซ้ำกัน')
        if any(x < mn or x > mx for x in nums):
            raise ValueError(f'ตัวเลขทุกช่องต้องอยู่ในช่วง {mn} ถึง {mx}')

        if room.special_corner_center_rule == 'even_corners_odd_center':
            for idx in corners:
                if nums[idx] % 2 != 0:
                    raise ValueError(f'ตามเงื่อนไขพิเศษของห้องนี้: ช่องมุมตารางต้องเป็น "เลขคู่" เท่านั้น (ช่องที่ {idx + 1})')
            for idx in centers:
                if nums[idx] % 2 == 0:
                    raise ValueError(f'ตามเงื่อนไขพิเศษของห้องนี้: ช่องตรงกลางตารางต้องเป็น "เลขคี่" เท่านั้น (ช่องที่ {idx + 1})')
        return nums

    # กรณีสุ่มสร้างตารางอัตโนมัติ (หรือล็อกมุม/กลางโดยระบบ)
    if room.special_corner_center_rule == 'even_corners_odd_center':
        evens = [x for x in pool if x % 2 == 0]
        odds = [x for x in pool if x % 2 == 1]
        random.shuffle(evens)
        random.shuffle(odds)
        grid = [0] * total_cells
        used = set()
        for idx in sorted(corners):
            val = evens.pop()
            grid[idx] = val
            used.add(val)
        for idx in sorted(centers):
            val = odds.pop()
            grid[idx] = val
            used.add(val)
        remaining = [x for x in pool if x not in used]
        random.shuffle(remaining)
        for i in range(total_cells):
            if grid[i] == 0:
                grid[i] = remaining.pop()
        return grid

    sampled = random.sample(pool, total_cells)
    return sampled


def check_bingo_win_patterns(room, grid_numbers, drawn_set):
    """
    ตรวจสอบว่าตาราง grid_numbers (ขนาด N*N) มีแนวที่ครบ Bingo ตาม win_patterns ของห้องหรือยัง
    คืนค่า list ของชื่อรูปแบบที่ชนะ (ถ้ายังไม่ชนะ คืน list ว่าง)
    """
    n = int(room.size or 5)
    if len(grid_numbers) != n * n:
        return []

    patterns_cfg = (room.win_patterns or 'all').lower()
    check_h = 'all' in patterns_cfg or 'horizontal' in patterns_cfg
    check_v = 'all' in patterns_cfg or 'vertical' in patterns_cfg
    check_d = 'all' in patterns_cfg or 'diagonal' in patterns_cfg
    check_c = 'four_corners' in patterns_cfg

    matched = []

    if check_h:
        for r in range(n):
            row_vals = [grid_numbers[r * n + c] for c in range(n)]
            if all(v in drawn_set for v in row_vals):
                matched.append(f'แนวนอน แถวที่ {r + 1}')

    if check_v:
        for c in range(n):
            col_vals = [grid_numbers[r * n + c] for r in range(n)]
            if all(v in drawn_set for v in col_vals):
                matched.append(f'แนวตั้ง คอลัมน์ที่ {c + 1}')

    if check_d:
        diag1 = [grid_numbers[i * n + i] for i in range(n)]
        if all(v in drawn_set for v in diag1):
            matched.append('แนวทแยงหลัก (ซ้ายบน-ขวาล่าง)')
        diag2 = [grid_numbers[i * n + (n - 1 - i)] for i in range(n)]
        if all(v in drawn_set for v in diag2):
            matched.append('แนวทแยงรอง (ขวาบน-ซ้ายล่าง)')

    if check_c:
        corners = [grid_numbers[0], grid_numbers[n - 1], grid_numbers[n * (n - 1)], grid_numbers[n * n - 1]]
        if all(v in drawn_set for v in corners):
            matched.append('4 มุมตาราง')

    return matched


def normalize_bingo_room_filter(target_room_code):
    """แปลงค่า filter ห้อง Bingo ให้เป็น 'all' หรือรหัสห้อง 2 หลัก เช่น '01'..'14'"""
    if target_room_code is None:
        return 'all'
    s = str(target_room_code).strip()
    if not s or s.lower() == 'all':
        return 'all'
    if s.isdigit():
        return s.zfill(2)
    return s


def get_bingo_room_label(room_code):
    """คืนชื่อแสดงผลของขอบเขตห้อง Bingo"""
    rc = normalize_bingo_room_filter(room_code)
    if rc == 'all':
        return 'ทุกห้อง (ทั้งระดับ)'
    c = Classroom.objects.filter(code=rc).first()
    if c:
        return c.name
    if rc.isdigit():
        return f'ม.4/{int(rc)}'
    return rc


def get_effective_drawn_qs(room, room_code='all'):
    """คืน QuerySet ของเลขที่ออกแล้วสำหรับขอบเขตที่เลือก (เฉพาะ 'all' หรือรวม 'all' + ห้องที่เลือก)"""
    rc = normalize_bingo_room_filter(room_code)
    if rc == 'all':
        return room.drawn_numbers.filter(target_room_code='all').order_by('created_at', 'id')
    return room.drawn_numbers.filter(target_room_code__in=['all', rc]).order_by('created_at', 'id')


def get_effective_drawn_list_and_set(room, room_code='all'):
    """คืน (list ของเลขที่ออกเรียงตามลำดับไม่ซ้ำ, set ของเลขที่ออก) สำหรับห้องที่กำหนด"""
    nums = []
    seen = set()
    for d in get_effective_drawn_qs(room, room_code).only('number'):
        if d.number not in seen:
            seen.add(d.number)
            nums.append(d.number)
    return nums, seen


def compute_bingo_number_hits(room, number, target_room_code='all'):
    """คำนวณสถิติว่าเลข number มีนักเรียนในห้องที่เลือก (หรือทุกห้อง) ได้รับลงในตารางกี่คน/กี่เปอร์เซ็นต์"""
    rc = normalize_bingo_room_filter(target_room_code)
    target_val = int(number)
    cards_qs = room.cards.select_related('student', 'student__classroom').order_by('student__room_code', 'student__class_number')
    if rc != 'all':
        cards_qs = cards_qs.filter(student__room_code=rc)

    cards = list(cards_qs)
    hit_students = []
    room_buckets = {}

    for card in cards:
        st = card.student
        st_rc = st.room_code or '99'
        st_label = st.room_display
        sort_key = (int(st_rc) if st_rc.isdigit() else 99, st_label)
        if sort_key not in room_buckets:
            room_buckets[sort_key] = {'room_code': st_rc, 'hits': 0, 'total': 0}
        room_buckets[sort_key]['total'] += 1

        grid = card.get_grid_list()
        if target_val in grid:
            room_buckets[sort_key]['hits'] += 1
            hit_students.append({
                'student_id': st.student_id or st.username,
                'name': st.get_full_name(),
                'room': st_label,
                'room_code': st_rc,
                'class_number': st.class_number,
                'avatar': st.avatar_url,
                'initial': st.avatar_initial,
            })

    by_room = {}
    for (_sk, r_label), b in sorted(room_buckets.items(), key=lambda x: x[0]):
        tot = b['total']
        by_room[r_label] = {
            'room_code': b['room_code'],
            'hits': b['hits'],
            'total': tot,
            'percent': round((b['hits'] * 100.0 / tot), 1) if tot else 0.0,
        }

    eligible_cnt = len(cards)
    hit_cnt = len(hit_students)
    return {
        'number': target_val,
        'target_room_code': rc,
        'target_room_label': get_bingo_room_label(rc),
        'hit_count': hit_cnt,
        'eligible_cards_count': eligible_cnt,
        'hit_percent': round((hit_cnt * 100.0 / eligible_cnt), 1) if eligible_cnt else 0.0,
        'hit_students': hit_students,
        'by_room': by_room,
    }


def validate_student_bingo_pick(room, student, chosen_number):
    """
    G.3 & G.4 การตรวจสอบเงื่อนไขการเลือกเลขของนักเรียน:
    1. ต้องเป็นตัวเลขที่ยังไม่ถูกเปิด (ตรวจสอบจากเลขที่ออกของทั้งระดับ + ห้องของนักเรียน)
    2. ก่อนนักเรียนเลือกเลข ระบบตรวจสอบว่าเลขนั้นอยู่ในตารางของตนเองหรือไม่
    3. ตัวเลขนั้นต้องไม่เป็นตำแหน่งมุมหรือตรงกลางของตาราง
    4. ระบบจำลองการออกเลขนั้น (Simulation) และตรวจว่าทำให้เกิดแนวชนะหรือไม่
       หากทำให้คนเลือก Bingo ทันที ให้ระบบปฏิเสธการเลือก!
    """
    mn, mx = room.get_min_max_range()
    num = int(chosen_number)
    if num < mn or num > mx:
        return False, f'หมายเลข {num} ไม่อยู่ในช่วงที่กำหนด ({mn}–{mx})'

    st_room_code = normalize_bingo_room_filter(student.room_code or '01')
    _, drawn_set = get_effective_drawn_list_and_set(room, st_room_code)
    if num in drawn_set:
        return False, f'หมายเลข {num} ถูกเปิดไปแล้ว กรุณาเลือกหมายเลขอื่น'

    card = BingoCard.objects.filter(room=room, student=student).first()
    if not card:
        return False, 'คุณยังไม่ได้สร้างตาราง Bingo ในห้องนี้'

    grid = card.get_grid_list()
    if num not in grid:
        return False, f'ปฏิเสธการเลือก: หมายเลข {num} ไม่ได้อยู่ในตาราง Bingo ของคุณ'

    idx = grid.index(num)
    corners, centers, _ = room.get_corner_and_center_indices()
    if idx in corners:
        return False, f'ปฏิเสธการเลือก (G.4): หมายเลข {num} อยู่ในตำแหน่ง "มุมตาราง" ห้ามนักเรียนเลือกเลขตำแหน่งมุม'
    if idx in centers:
        return False, f'ปฏิเสธการเลือก (G.4): หมายเลข {num} อยู่ในตำแหน่ง "กลางตาราง" ห้ามนักเรียนเลือกเลขตำแหน่งตรงกลาง'

    # จำลองการออกเลขนั้น (Simulation)
    simulated_drawn = set(drawn_set)
    simulated_drawn.add(num)
    simulated_wins = check_bingo_win_patterns(room, grid, simulated_drawn)
    if simulated_wins:
        return False, (
            f'ปฏิเสธการเลือก (G.4 Simulation Guard): หากออกหมายเลข {num} จะทำให้ตารางของคุณ '
            f'ชนะ Bingo ทันที ({", ".join(simulated_wins)}) ระบบไม่อนุญาตให้เลือกเลขที่ทำให้ตนเอง Bingo ทันที!'
        )

    return True, 'ผ่านเงื่อนไขการเลือกตัวเลข'


def draw_next_bingo_number(room, mode='normal', chosen_number=None, student=None, teacher=None, target_room_code='all'):
    """
    ดำเนินการออกเลข Bingo ตามโหมดและห้องที่เลือก filter (all=ทั้งระดับ หรือ 01-14=รายห้อง):
    - normal: สุ่มแบบปกติ
    - conditional: สุ่มแบบมีเงื่อนไข (เช่น ไม่ออกเลขคู่/คี่ หรือออกเฉพาะครึ่งช่วง)
    - student_pick: นักเรียนเลือกเลข (ออกให้ห้องของนักเรียนคนนั้นและผ่าน validate_student_bingo_pick)
    - teacher_manual: ผู้ดูแลพิมพ์ระบุเลข
    พร้อมคำนวณสถิติว่าเลขที่ออกมีนักเรียนในห้องที่เลือกได้รับกี่คน และตรวจหาผู้ชนะอัตโนมัติ
    """
    mn, mx = room.get_min_max_range()
    rc = normalize_bingo_room_filter(target_room_code)
    if mode == BingoDrawnNumber.MODE_STUDENT and student and rc == 'all':
        rc = normalize_bingo_room_filter(student.room_code or '01')

    _, drawn_set = get_effective_drawn_list_and_set(room, rc)

    if mode == BingoDrawnNumber.MODE_STUDENT:
        ok, msg = validate_student_bingo_pick(room, student, chosen_number)
        if not ok:
            raise ValueError(msg)
        target_num = int(chosen_number)
    elif mode == BingoDrawnNumber.MODE_MANUAL and chosen_number is not None:
        target_num = int(chosen_number)
        if target_num < mn or target_num > mx:
            raise ValueError(f'ตัวเลขต้องอยู่ในช่วง {mn}–{mx}')
        if target_num in drawn_set:
            raise ValueError(f'หมายเลข {target_num} ถูกเปิดไปแล้วใน{get_bingo_room_label(rc)}')
    else:
        candidates = [x for x in range(mn, mx + 1) if x not in drawn_set]
        if not candidates:
            raise ValueError(f'ตัวเลขในช่วงนี้ของ {get_bingo_room_label(rc)} ถูกเปิดครบทุกหมายเลขแล้ว')

        if mode == BingoDrawnNumber.MODE_CONDITIONAL or room.draw_condition != 'normal':
            cond = room.draw_condition
            mid = (mn + mx) // 2
            if cond == 'no_even':
                filtered = [x for x in candidates if x % 2 == 1]
            elif cond == 'no_odd':
                filtered = [x for x in candidates if x % 2 == 0]
            elif cond == 'only_low_half':
                filtered = [x for x in candidates if x <= mid]
            elif cond == 'only_high_half':
                filtered = [x for x in candidates if x > mid]
            else:
                filtered = candidates
            if filtered:
                candidates = filtered

        target_num = random.choice(candidates)

    hit_stats = compute_bingo_number_hits(room, target_num, rc)

    with transaction.atomic():
        drawn_obj = BingoDrawnNumber.objects.create(
            room=room,
            target_room_code=rc,
            number=target_num,
            drawn_by_mode=mode,
            drawn_by_student=student if mode == BingoDrawnNumber.MODE_STUDENT else None,
            hit_count=hit_stats['hit_count'],
            eligible_cards_count=hit_stats['eligible_cards_count'],
        )
        if room.status in (BingoRoom.STATUS_OPEN, BingoRoom.STATUS_FINISHED):
            room.status = BingoRoom.STATUS_PLAYING
            room.save(update_fields=['status'])

        existing_winner_ids = set(room.winners.values_list('student_id', flat=True))
        next_rank = len(existing_winner_ids) + 1
        new_winners = []

        cards_qs = room.cards.select_related('student', 'student__classroom').order_by('created_at')
        if rc != 'all':
            cards_qs = cards_qs.filter(student__room_code=rc)

        room_drawn_cache = {}
        for card in cards_qs:
            if card.student_id in existing_winner_ids:
                continue
            st_room_code = normalize_bingo_room_filter(card.student.room_code or '01')
            if st_room_code not in room_drawn_cache:
                _, s_set = get_effective_drawn_list_and_set(room, st_room_code)
                room_drawn_cache[st_room_code] = s_set
            grid = card.get_grid_list()
            wins = check_bingo_win_patterns(room, grid, room_drawn_cache[st_room_code])
            if wins:
                room_rank = BingoWinner.objects.filter(room=room, room_code=st_room_code).count() + 1
                log = award_points_by_rule(
                    student=card.student,
                    rule_code='bingo_winner',
                    default_name='ชนะเกม Bingo',
                    default_points=2.0,
                    source_module='bingo',
                    custom_reason=f'ชนะเกม Bingo "{room.title}" (ห้อง {card.student.room_display} อันดับที่ {room_rank}, {", ".join(wins)}) ด้วยหมายเลข {target_num}',
                    awarded_by=teacher,
                )
                w_obj = BingoWinner.objects.create(
                    room=room,
                    student=card.student,
                    room_code=st_room_code,
                    room_rank_order=room_rank,
                    win_pattern=', '.join(wins),
                    winning_number=target_num,
                    rank_order=next_rank,
                    score_awarded=log.points,
                )
                next_rank += 1
                new_winners.append(w_obj)

    drawn_obj.hit_stats = hit_stats
    return drawn_obj, new_winners


# =============================================================================
# 5. AI สร้างข้อสอบวิทยาการคำนวณ (Gemini API / Claude API / Smart CS Bank)
# =============================================================================
SMART_CS_QUESTION_TEMPLATES = [
    {
        'q': 'อัลกอริทึมการเรียงลำดับข้อมูล (Sorting Algorithm) ใดที่เปรียบเทียบข้อมูลคู่ที่อยู่ติดกันแล้วสลับตำแหน่งไปเรื่อย ๆ จนกว่าจะเรียงเสร็จ?',
        'a': 'Bubble Sort', 'b': 'Selection Sort', 'c': 'Insertion Sort', 'd': 'Binary Search', 'ans': 'A',
        'exp': 'Bubble Sort เปรียบเทียบข้อมูลคู่ที่อยู่ติดกันและสลับตำแหน่งหากลำดับไม่ถูกต้อง เหมือนฟองอากาศที่ลอยขึ้น'
    },
    {
        'q': 'หากมีชุดข้อมูล [12, 5, 9, 3] เมื่อผ่านการเรียงลำดับแบบ Selection Sort (น้อยไปมาก) ในรอบที่ 1 (Pass 1) จะได้ผลลัพธ์ใด?',
        'a': '[3, 5, 9, 12]', 'b': '[5, 9, 3, 12]', 'c': '[3, 12, 5, 9]', 'd': '[12, 9, 5, 3]', 'ans': 'A',
        'exp': 'Selection Sort รอบที่ 1 จะค้นหาค่าที่น้อยที่สุดในชุดข้อมูลคือ 3 แล้วนำมาสลับกับตำแหน่งแรก (12) จึงได้ [3, 5, 9, 12]'
    },
    {
        'q': 'อัลกอริทึม Insertion Sort มีหลักการทำงานคล้ายกับกิจกรรมใดในชีวิตประจำวันมากที่สุด?',
        'a': 'การหยิบไพ่ขึ้นมาทีละใบแล้วแทรกเข้าในตำแหน่งที่เรียงอยู่แล้วในมือ',
        'b': 'การโยนเหรียญเสี่ยงทายหัวก้อย',
        'c': 'การเปิดพจนานุกรมจากหน้ากึ่งกลางเล่ม',
        'd': 'การจับคู่ถุงเท้าที่มีสีเดียวกันจากกอง',
        'ans': 'A',
        'exp': 'Insertion Sort นำข้อมูลตัวถัดไปมาแทรก (Insert) ลงในส่วนของข้อมูลด้านซ้ายที่เรียงลำดับไว้เรียบร้อยแล้ว'
    },
    {
        'q': 'ความซับซ้อนของเวลา (Time Complexity) ในกรณีแย่ที่สุด (Worst Case) ของ Bubble Sort, Selection Sort และ Insertion Sort คือข้อใด?',
        'a': 'O(n²)', 'b': 'O(log n)', 'c': 'O(1)', 'd': 'O(n log n)', 'ans': 'A',
        'exp': 'อัลกอริทึมการเรียงลำดับพื้นฐานทั้ง 3 แบบมีลูปซ้อนกัน 2 ชั้น จึงมี Time Complexity ในกรณีแย่ที่สุดเป็น O(n²)'
    },
    {
        'q': 'ในแนวคิดเชิงคำนวณ (Computational Thinking) การแบ่งปัญหาใหญ่ออกเป็นปัญหาย่อย ๆ ที่จัดการได้ง่ายขึ้น เรียกว่าอะไร?',
        'a': 'Decomposition (การแยกส่วนประกอบและการย่อยปัญหา)',
        'b': 'Pattern Recognition (การหารูปแบบ)',
        'c': 'Abstraction (การคิดเชิงนามธรรม)',
        'd': 'Algorithm Design (การออกแบบขั้นตอนวิธี)',
        'ans': 'A',
        'exp': 'Decomposition คือการย่อยปัญหาที่ซับซ้อนออกเป็นส่วนย่อย ๆ เพื่อให้ง่ายต่อการแก้ปัญหาและพัฒนาโปรแกรม'
    },
    {
        'q': 'โครงสร้างข้อมูลใดทำงานในลักษณะ LIFO (Last-In, First-Out) เข้าทีหลังออกก่อน?',
        'a': 'Stack (สแต็ก)', 'b': 'Queue (คิว)', 'c': 'Array 2 มิติ', 'd': 'Linked List', 'ans': 'A',
        'exp': 'Stack มีหลักการวางซ้อนกันแบบ LIFO (Last-In, First-Out) ข้อมูลที่ใส่เข้าไปล่าสุดจะถูกนำออกมาก่อน'
    },
    {
        'q': 'การค้นหาแบบทวิภาค (Binary Search) มีเงื่อนไขสำคัญอย่างไรก่อนที่จะสามารถใช้งานได้?',
        'a': 'ข้อมูลในชุดนั้นจะต้องถูกเรียงลำดับ (Sorted) ไว้เรียบร้อยแล้ว',
        'b': 'ข้อมูลต้องมีจำนวนเป็นเลขคู่เท่านั้น',
        'c': 'ข้อมูลต้องไม่มีเลขซ้ำกันเลย',
        'd': 'ต้องเก็บข้อมูลไว้ในตัวแปรแบบ String เท่านั้น',
        'ans': 'A',
        'exp': 'Binary Search ใช้วิธีแบ่งครึ่งช่วงการค้นหา ดังนั้นข้อมูลจะต้องเรียงลำดับจากน้อยไปมากหรือมากไปน้อยก่อนเสมอ'
    },
]


def generate_ai_quiz_questions(topic_prompt, count=5):
    """
    เรียก Gemini API หรือ Claude API หากตั้งค่า API Key ไว้ใน SystemSetting
    หากไม่ได้ใส่ API Key ระบบจะใช้ Smart Computational Science Question Generator สร้างข้อสอบตามหัวข้อทันที
    """
    setting = SystemSetting.get()
    count = max(1, min(15, int(count or 5)))

    if setting.ai_provider == 'gemini' and setting.gemini_api_key.strip():
        try:
            url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={setting.gemini_api_key.strip()}'
            prompt = (
                f'สร้างข้อสอบปรนัย 4 ตัวเลือก วิชาวิทยาการคำนวณ ระดับมัธยมศึกษา จำนวน {count} ข้อ '
                f'ในหัวข้อ: "{topic_prompt}" โดยตอบกลับมาเป็น JSON Array เท่านั้น รูปแบบ: '
                '[{"q":"...","a":"...","b":"...","c":"...","d":"...","ans":"A","exp":"..."}]'
            )
            payload = json.dumps({'contents': [{'parts': [{'text': prompt}]}]}).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                text = data['candidates'][0]['content']['parts'][0]['text']
                m = re.search(r'\[.*\]', text, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list) and parsed:
                        return parsed[:count], 'Google Gemini API'
        except Exception:
            pass

    elif setting.ai_provider == 'claude' and setting.claude_api_key.strip():
        try:
            url = 'https://api.anthropic.com/v1/messages'
            prompt = (
                f'สร้างข้อสอบปรนัย 4 ตัวเลือก วิชาวิทยาการคำนวณ จำนวน {count} ข้อ ในหัวข้อ "{topic_prompt}" '
                'ตอบเป็น JSON Array รูปแบบ [{"q":"...","a":"...","b":"...","c":"...","d":"...","ans":"A","exp":"..."}]'
            )
            payload = json.dumps({
                'model': 'claude-3-haiku-20240307',
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}],
            }).encode('utf-8')
            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'x-api-key': setting.claude_api_key.strip(),
                'anthropic-version': '2023-06-01',
            })
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                text = data['content'][0]['text']
                m = re.search(r'\[.*\]', text, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list) and parsed:
                        return parsed[:count], 'Anthropic Claude API'
        except Exception:
            pass

    # Fallback: Smart CS Generator ที่สับเปลี่ยนตัวเลือกอัตโนมัติ
    pool = list(SMART_CS_QUESTION_TEMPLATES)
    results = []
    for i in range(count):
        base = dict(pool[i % len(pool)])
        choices = [('A', base['a']), ('B', base['b']), ('C', base['c']), ('D', base['d'])]
        correct_text = dict(choices).get(base['ans'], base['a'])
        vals = [c[1] for c in choices]
        random.shuffle(vals)
        new_ans = ['A', 'B', 'C', 'D'][vals.index(correct_text)]
        results.append({
            'q': f'[{topic_prompt or "วิทยาการคำนวณ"}] {base["q"]}',
            'a': vals[0],
            'b': vals[1],
            'c': vals[2],
            'd': vals[3],
            'ans': new_ans,
            'exp': base['exp'],
        })
    return results, 'Smart CS AI Generator (Built-in)'


# =============================================================================
# 6. นำเข้าข้อมูลเริ่มต้นจากโปรเจกต์เดิม (DebsirinLotto & DSbasicsorting)
# =============================================================================
def seed_initial_comsc4_data():
    """
    สร้างข้อมูลเริ่มต้นให้พร้อมใช้งานทันทีเมื่อรัน run.bat:
    1. Admin (admin / 1234) และ ครูผู้สอน (teacher / 1234)
    2. ห้องเรียน ม.4/1 ถึง ม.4/14 (รหัส 01 - 14, กระดานดำอยู่ล่างสุด)
    3. นำเข้ารายชื่อนักเรียนโรงเรียนเทพศิรินทร์จาก students_data.csv (ตามกระบวนการ DSCOMPOSN)
    4. สร้างกฎคะแนนมาตรฐาน (รวม 3 กฎจากระบบเกม และเช็คหนังสือ/สมุด)
    5. สร้างกิจกรรมเริ่มต้นของ DSBasicSorting, ห้อง Bingo (เล่นพร้อมกันทั้งระดับต่อเนื่อง) และรอบสุ่ม Debsirin Lotto
    """
    setting = SystemSetting.get()
    cur_yr = (setting.current_academic_year or '2569').strip() or '2569'
    ensure_academic_year_exists(cur_yr, make_current=True)

    # 1. สร้าง Admin และ Teacher เริ่มต้น
    if not User.objects.filter(username='admin').exists():
        User.objects.create_superuser('admin', '1234', first_name='ผู้ดูแลระบบสูงสุด', last_name='COMSC4', role=User.ROLE_ADMIN, email='admin@debsirin.ac.th', allow_password_login=True)
    else:
        User.objects.filter(username='admin').update(allow_password_login=True)
    if not User.objects.filter(username='teacher').exists():
        User.objects.create_user('teacher', '1234', first_name='ครูผู้สอน', last_name='วิทยาการคำนวณ', role=User.ROLE_TEACHER, email='teacher@debsirin.ac.th', allow_password_login=True)
    else:
        User.objects.filter(username='teacher').update(allow_password_login=True)

    # 2. สร้างกฎคะแนนมาตรฐาน
    default_rules = [
        ('sorting_top_exam', 'ทำกิจกรรมเรียงลำดับสูงสุดในรอบสอบ', ScoreRule.TYPE_PLUS, 2.0, 'ระบบให้อัตโนมัติเมื่อปิดรอบสอบ', True),
        ('bingo_winner', 'ชนะเกม Bingo', ScoreRule.TYPE_PLUS, 2.0, 'ระบบให้อัตโนมัติเมื่อมีผู้ชนะ', True),
        ('lotto_picked', 'ถูกสุ่มจาก Lotto', ScoreRule.TYPE_PLUS, 1.0, 'ระบบให้อัตโนมัติเมื่อสุ่ม', True),
        ('attendance_on_time', 'เข้าเรียนตรงเวลาและเช็คชื่อตามแผนผังที่นั่ง', ScoreRule.TYPE_PLUS, 1.0, 'ระบบให้อัตโนมัติเมื่อเช็คชื่อ', True),
        ('bring_book', 'นำหนังสือเรียนมาเรียน', ScoreRule.TYPE_PLUS, 1.0, 'ครูคลิกเช็คจากแผนผังที่นั่งห้องเรียน', True),
        ('bring_notebook', 'นำสมุดมาเรียน', ScoreRule.TYPE_PLUS, 1.0, 'ครูคลิกเช็คจากแผนผังที่นั่งห้องเรียน', True),
        ('quiz_passed', 'ทำแบบทดสอบ AI ผ่านเกณฑ์', ScoreRule.TYPE_PLUS, 2.0, 'ระบบให้อัตโนมัติเมื่อส่งแบบทดสอบ', True),
        ('active_answer', 'ตอบคำถามหรือนำเสนอผลงานในชั้นเรียน', ScoreRule.TYPE_PLUS, 1.0, 'ครูผู้สอนกดให้คะแนน', False),
        ('late_submission', 'ส่งงานล่าช้ากว่ากำหนด', ScoreRule.TYPE_MINUS, 1.0, 'ครูผู้สอนกดหักคะแนน', False),
    ]
    for code, name, rtype, pts, note, is_auto in default_rules:
        ScoreRule.objects.get_or_create(
            code=code,
            defaults={'name': name, 'rule_type': rtype, 'points': pts, 'note': note, 'is_auto': is_auto}
        )

    # 3. สร้างห้องเรียน 01 - 14 (กระดานดำอยู่ล่างสุดเป็นค่าเริ่มต้น)
    rooms_map = {}
    for i in range(1, 15):
        rcode = f'{i:02d}'
        room_obj, _ = Classroom.objects.get_or_create(
            code=rcode,
            defaults={
                'name': f'ม.4/{i}',
                'grade_level': '4',
                'seat_rows': 6,
                'seat_cols': 7,
                'board_position': Classroom.BOARD_BOTTOM,
            }
        )
        rooms_map[rcode] = room_obj

    # 3.1 สร้างแผนผังห้องเรียนกลางเริ่มต้น (ห้อง 134, 135, 136) และผูกห้องเรียนที่ยังไม่เลือกให้ใช้ห้อง 134
    ensure_default_lab_rooms()

    # 4. นำเข้ารายชื่อนักเรียนเริ่มต้นจากไฟล์ students_data.csv (หรือ student_data.csv)
    if User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr).exclude(student_id='99999').count() == 0:
        csv_candidates = [
            Path(settings.BASE_DIR) / 'students_data.csv',
            Path(settings.BASE_DIR) / 'student_data.csv',
        ]
        csv_file = next((p for p in csv_candidates if p.exists()), None)
        if csv_file:
            from .excel_import import import_students_file
            with open(csv_file, 'rb') as fp:
                import_students_file(fp, academic_year=cur_yr)

    # 4.1 เพิ่ม/อัปเดตนักเรียนกรณีพิเศษ 99999 นายสมชาย ใจดี ม.4/1 เลขที่สุดท้าย (username: 99999 / password: 1234)
    room01 = rooms_map.get('01') or Classroom.objects.filter(code='01').first()
    max_no_01 = (
        User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr, room_code='01')
        .exclude(student_id='99999')
        .aggregate(m=Max('class_number'))['m']
        or 0
    )
    last_no_01 = max_no_01 + 1
    special_st = (
        User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr, student_id='99999').first()
        or User.objects.filter(username='99999').first()
    )
    if not special_st:
        sp_uname = '99999' if not User.objects.filter(username='99999').exists() else f'99999_{cur_yr}'
        special_st = User(username=sp_uname, student_id='99999')
    if not special_st.username:
        special_st.username = '99999' if not User.objects.filter(username='99999').exists() else f'99999_{cur_yr}'
    special_st.student_id = '99999'
    special_st.academic_year = cur_yr
    special_st.email = 's99999@debsirin.ac.th'
    special_st.title_prefix = 'นาย'
    special_st.first_name = 'สมชาย'
    special_st.last_name = 'ใจดี'
    special_st.classroom = room01
    special_st.room_code = '01'
    special_st.class_number = last_no_01
    special_st.group_name = special_st.group_name or 'กลุ่ม A'
    special_st.role = User.ROLE_STUDENT
    special_st.allow_password_login = True
    special_st.is_active = True
    if not special_st.pk or not special_st.check_password('1234'):
        special_st.set_password('1234')
    special_st.save()

    # 4.2 ห้องเรียนทุกห้องที่ผูกกับแผนผังห้อง (เช่น ห้อง 134) จะใช้นั่งตามเลขเครื่องเป็นค่าเริ่มต้นโดยอัตโนมัติ
    ensure_default_lab_rooms()

    teacher_user = User.objects.filter(role__in=[User.ROLE_TEACHER, User.ROLE_ADMIN]).first()

    # 5. สร้างกิจกรรมเริ่มต้นในแต่ละส่วนงาน
    if not SortingActivity.objects.filter(academic_year=cur_yr).exists():
        SortingActivity.objects.create(
            title='DSBasicSorting — กิจกรรมเรียงลำดับข้อมูล (Bubble ฟองสบู่ / Selection แบบเลือก / Insertion แบบแทรก)',
            academic_year=cur_yr,
            dataset='12, 5, 9, 1, 15, 8, 3',
            unit_count=7,
            mode=SortingActivity.MODE_BUBBLE_ASC,
            time_limit=180,
            is_active=True,
            is_exam_mode=True,
            top_x_reward=5,
            created_by=teacher_user,
        )

    if not BingoRoom.objects.filter(academic_year=cur_yr).exists():
        BingoRoom.objects.create(
            title='Bingo พร้อมกันทั้งระดับ ม.4 (เล่นต่อเนื่องหาผู้ชนะทุกห้อง)',
            academic_year=cur_yr,
            classroom=None,
            is_grade_wide=True,
            continuous_play=True,
            size=5,
            number_range='1-36',
            win_patterns='all',
            special_corner_center_rule='student_fill_protected',
            draw_condition='normal',
            allow_student_pick=True,
            status=BingoRoom.STATUS_OPEN,
            created_by=teacher_user,
        )

    if not LottoSession.objects.filter(academic_year=cur_yr).exists():
        LottoSession.objects.create(
            title='ID LOTTO',
            academic_year=cur_yr,
            classroom=rooms_map.get('01'),
            winner_count=5,
            only_never_picked_in_session=True,
            bonus_points=1.0,
            is_open=True,
            created_by=teacher_user,
        )

    ensure_default_score_rank_tiers()

    if not LessonContent.objects.filter(academic_year=cur_yr).exists():
        lesson = LessonContent.objects.create(
            academic_year=cur_yr,
            unit_number=1,
            title='อัลกอริทึมการจัดเรียงข้อมูล (Bubble, Selection, Insertion Sort)',
            summary='เรียนรู้หลักการทำงานของอัลกอริทึมการเรียงลำดับข้อมูลพื้นฐานทั้ง 3 รูปแบบ พร้อมเปรียบเทียบจำนวนรอบการทำงาน',
            content_html=(
                '<h5>1. Bubble Sort (การเรียงลำดับแบบฟองสบู่)</h5>'
                '<p>เปรียบเทียบข้อมูลคู่ที่อยู่ติดกันทีละคู่ หากลำดับไม่ถูกต้องจะสลับที่กันทันที ทำซ้ำทีละรอบจนข้อมูลเรียงครบ</p>'
                '<h5>2. Selection Sort (การเรียงลำดับแบบเลือก)</h5>'
                '<p>ค้นหาข้อมูลที่มีค่าน้อยที่สุด (หรือมากที่สุด) ในส่วนที่ยังไม่ได้เรียง แล้วนำมาสลับไว้ในตำแหน่งเริ่มต้นของรอบนั้น</p>'
                '<h5>3. Insertion Sort (การเรียงลำดับแบบแทรก)</h5>'
                '<p>นำข้อมูลตัวถัดไปมาแทรกลงในตำแหน่งที่เหมาะสมของกลุ่มข้อมูลด้านซ้ายที่เรียงลำดับไว้แล้ว</p>'
            ),
            created_by=teacher_user,
        )
        quiz = AIQuiz.objects.create(
            lesson=lesson,
            academic_year=cur_yr,
            title='แบบทดสอบ AI: อัลกอริทึมการเรียงลำดับและแนวคิดเชิงคำนวณ',
            topic_prompt='Sorting Algorithms (Bubble, Selection, Insertion Sort) & Computational Thinking',
            ai_engine_used='Smart CS AI Generator',
            time_limit_minutes=15,
            points_per_question=1.0,
            sync_to_total_score=True,
            is_active=True,
            created_by=teacher_user,
        )
        q_list, _ = generate_ai_quiz_questions(quiz.topic_prompt, count=5)
        for idx, qd in enumerate(q_list, start=1):
            AIQuizQuestion.objects.create(
                quiz=quiz,
                order_no=idx,
                question_text=qd['q'],
                choice_a=qd['a'],
                choice_b=qd['b'],
                choice_c=qd['c'],
                choice_d=qd['d'],
                correct_choice=qd['ans'],
                explanation=qd['exp'],
            )

    seed_sample_activities_room01(teacher_user)

    if not AttendanceSession.objects.filter(academic_year=cur_yr).exists() and rooms_map.get('01'):
        AttendanceSession.objects.create(
            classroom=rooms_map['01'],
            academic_year=cur_yr,
            title='คาบเรียนวิทยาการคำนวณ สัปดาห์ที่ 1',
            check_in_code='DS2569',
            attendance_points=1.0,
            is_open=True,
            created_by=teacher_user,
        )


def _get_thai_font(size=30):
    """โหลดฟอนต์ THSarabunNew สำหรับวาดข้อความไทย/อังกฤษลงภาพ PNG"""
    font_candidates = [
        Path(settings.BASE_DIR) / 'static' / 'fonts' / 'THSarabunNew Bold.ttf',
        Path(settings.BASE_DIR) / 'static' / 'fonts' / 'THSarabunNew.ttf',
    ]
    for fp in font_candidates:
        if fp.exists():
            try:
                return ImageFont.truetype(str(fp), size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_fruit_art(draw, key):
    """วาดภาพประกอบผลไม้แบบการ์ตูนสีสันสดใสบนแคนวาส 300x300"""
    outline = (30, 41, 59)
    leaf_green = (22, 163, 74)
    stem_brown = (120, 53, 15)
    if key == 'apple':
        draw.ellipse([72, 68, 178, 198], fill=(220, 38, 38), outline=outline, width=3)
        draw.ellipse([122, 68, 228, 198], fill=(220, 38, 38), outline=outline, width=3)
        draw.ellipse([90, 74, 210, 195], fill=(239, 68, 68))
        draw.arc([95, 85, 130, 130], start=180, end=270, fill=(254, 202, 202), width=5)
        draw.rectangle([146, 36, 155, 76], fill=stem_brown)
        draw.polygon([(153, 52), (195, 32), (188, 65)], fill=leaf_green, outline=outline)
    elif key == 'banana':
        draw.pieslice([65, 55, 235, 205], start=15, end=155, fill=(250, 204, 21), outline=outline, width=3)
        draw.pieslice([82, 42, 232, 178], start=15, end=155, fill=(254, 249, 195))
        draw.pieslice([55, 78, 220, 212], start=20, end=160, fill=(234, 179, 8), outline=outline, width=3)
        draw.pieslice([72, 64, 218, 185], start=20, end=160, fill=(254, 249, 195))
        draw.rectangle([198, 70, 216, 90], fill=stem_brown, outline=outline)
    elif key == 'orange':
        draw.ellipse([75, 58, 225, 204], fill=(249, 115, 22), outline=outline, width=3)
        draw.ellipse([90, 72, 128, 108], fill=(253, 186, 116))
        for dx, dy in [(170, 95), (188, 120), (165, 145), (130, 168), (110, 140), (148, 115)]:
            draw.ellipse([dx, dy, dx + 5, dy + 5], fill=(194, 65, 12))
        draw.rectangle([146, 36, 154, 62], fill=stem_brown)
        draw.polygon([(152, 46), (192, 30), (182, 60)], fill=leaf_green, outline=outline)
    elif key == 'watermelon':
        draw.ellipse([65, 54, 235, 204], fill=(21, 128, 61), outline=outline, width=3)
        for offset in (95, 128, 162, 194):
            draw.arc([offset - 18, 56, offset + 22, 202], start=250, end=110, fill=(134, 239, 172), width=6)
        draw.pieslice([95, 126, 225, 218], start=0, end=180, fill=(239, 68, 68), outline=(22, 163, 74), width=5)
        for sx, sy in [(135, 178), (158, 190), (182, 178), (148, 198), (170, 196)]:
            draw.ellipse([sx, sy, sx + 5, sy + 7], fill=(15, 23, 42))
    elif key == 'grape':
        draw.rectangle([145, 34, 155, 68], fill=stem_brown)
        draw.polygon([(154, 48), (192, 34), (182, 62)], fill=leaf_green, outline=outline)
        berry_centers = [
            (118, 82), (150, 82), (182, 82),
            (104, 112), (134, 112), (166, 112), (196, 112),
            (120, 142), (150, 142), (180, 142),
            (136, 172), (164, 172), (150, 194),
        ]
        for cx, cy in berry_centers:
            draw.ellipse([cx - 19, cy - 19, cx + 19, cy + 19], fill=(126, 34, 206), outline=outline, width=2)
            draw.ellipse([cx - 9, cy - 11, cx - 2, cy - 4], fill=(216, 180, 254))
    elif key == 'mango':
        draw.polygon(
            [(138, 56), (196, 70), (212, 130), (168, 198), (105, 192), (88, 128), (106, 74)],
            fill=(245, 158, 11),
            outline=outline,
        )
        draw.ellipse([96, 62, 206, 200], fill=(251, 191, 36), outline=outline, width=3)
        draw.ellipse([112, 82, 145, 125], fill=(254, 243, 199))
        draw.rectangle([146, 36, 154, 65], fill=stem_brown)
        draw.polygon([(152, 48), (192, 34), (182, 60)], fill=leaf_green, outline=outline)
    elif key == 'strawberry':
        draw.polygon([(82, 80), (218, 80), (150, 206)], fill=(225, 29, 72), outline=outline)
        draw.ellipse([82, 62, 218, 148], fill=(225, 29, 72), outline=outline, width=3)
        draw.polygon([(105, 76), (130, 52), (150, 74), (170, 52), (195, 76), (150, 88)], fill=leaf_green, outline=outline)
        for sx, sy in [(115, 108), (145, 105), (178, 108), (130, 135), (162, 135), (148, 164), (148, 186)]:
            draw.ellipse([sx, sy, sx + 5, sy + 8], fill=(254, 240, 138))
    elif key == 'pineapple':
        draw.polygon([(125, 72), (102, 28), (138, 56), (150, 18), (162, 56), (198, 28), (175, 72)], fill=leaf_green, outline=outline)
        draw.ellipse([92, 68, 208, 204], fill=(234, 179, 8), outline=outline, width=3)
        for off in range(-45, 55, 26):
            draw.line([(105, 135 + off), (195, 95 + off)], fill=(161, 98, 7), width=2)
            draw.line([(105, 95 + off), (195, 135 + off)], fill=(161, 98, 7), width=2)


def _ensure_sample_fruit_images(force_regenerate=False):
    """สร้างภาพผลไม้ตัวอย่าง PNG 300x300 ด้วย Pillow + ฟอนต์ THSarabunNew สำหรับกิจกรรมที่ 2 (ห้อง 4/1)"""
    fruits = [
        ('apple.png', 'Apple', 'แอปเปิ้ล', (220, 38, 38), (254, 226, 226)),
        ('banana.png', 'Banana', 'กล้วยหอม', (202, 138, 4), (254, 249, 195)),
        ('orange.png', 'Orange', 'ส้ม', (234, 88, 12), (255, 237, 213)),
        ('watermelon.png', 'Watermelon', 'แตงโม', (22, 163, 74), (220, 252, 231)),
        ('grape.png', 'Grape', 'องุ่น', (126, 34, 206), (243, 232, 255)),
        ('mango.png', 'Mango', 'มะม่วงสุก', (217, 119, 6), (254, 243, 199)),
        ('strawberry.png', 'Strawberry', 'สตรอว์เบอร์รี', (225, 29, 72), (255, 228, 230)),
        ('pineapple.png', 'Pineapple', 'สับปะรด', (161, 98, 7), (254, 240, 138)),
    ]
    fruit_dir = Path(settings.MEDIA_ROOT) / 'activities' / 'fruits'
    fruit_dir.mkdir(parents=True, exist_ok=True)
    title_font = _get_thai_font(34)
    sub_font = _get_thai_font(22)
    for fname, en_name, th_name, fg, bg in fruits:
        fpath = fruit_dir / fname
        if force_regenerate or not fpath.exists():
            img = Image.new('RGB', (300, 300), bg)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([12, 12, 288, 288], radius=24, fill=bg, outline=fg, width=3)
            draw.ellipse([54, 24, 246, 212], fill=(255, 255, 255))
            _draw_fruit_art(draw, fname.replace('.png', ''))
            draw.rounded_rectangle([24, 216, 276, 278], radius=14, fill=(255, 255, 255), outline=fg, width=2)
            label_text = f"{th_name} ({en_name})"
            try:
                bbox = draw.textbbox((0, 0), label_text, font=title_font)
                tw = bbox[2] - bbox[0]
                tx = max(32, (300 - tw) // 2)
            except Exception:
                tx = 56
            draw.text((tx, 222), label_text, fill=(15, 23, 42), font=title_font)
            draw.text((88, 252), "ตัวอย่างผลงาน ม.4/1", fill=fg, font=sub_font)
            img.save(fpath, format='PNG')
    return fruits


def seed_sample_activities_room01(teacher_user=None, force_refresh_samples=False):
    """
    สร้างช่วงคะแนนเริ่มต้น (ช่วงที่ 1 มีหลายกิจกรรม + ช่วงที่ 2 สำรอง)
    และกิจกรรมตัวอย่างสำหรับห้อง 4/1:
    - กิจกรรมที่ 1: ถามตอบ 1 + 1 = เท่าไร? (ตอบมาแล้ว 28/30 คน)
    - กิจกรรมที่ 2: แนบรูปภาพส่ง (แนบรูปผลไม้) (ส่งครบ 30/30 คน)
    """
    cur_yr = get_current_academic_year()
    if not ScorePeriod.objects.filter(academic_year=cur_yr).exists():
        p1 = ScorePeriod.objects.create(
            name='ช่วงคะแนนที่ 1 (ก่อนกลางภาค)',
            academic_year=cur_yr,
            order_no=1,
            max_score=20.0,
            is_finalized=False,
            note='รวมกิจกรรมที่ 1, กิจกรรมที่ 2 และคะแนนบวก/ลบในคาบ',
        )
        ScorePeriod.objects.create(
            name='ช่วงคะแนนที่ 2 (หลังกลางภาค)',
            academic_year=cur_yr,
            order_no=2,
            max_score=20.0,
            is_finalized=False,
            note='เปิดรับคะแนนช่วงถัดไปหลังจากสรุปช่วงที่ 1',
        )
    else:
        p1 = ScorePeriod.objects.filter(academic_year=cur_yr).order_by('order_no', 'id').first()

    LearningActivity.objects.filter(academic_year=cur_yr, score_period__isnull=True).update(score_period=p1)

    fruits = _ensure_sample_fruit_images(force_regenerate=force_refresh_samples)
    students_r01 = list(
        User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr, room_code='01').order_by('class_number', 'student_id')
    )

    act1_desc = (
        'โจทย์คำถามสั้นๆ ในชั้นเรียน: 1 + 1 เท่ากับเท่าไร? '
        'ให้นักเรียนพิมพ์คำตอบของตนเองพร้อมอธิบายเหตุผลสั้นๆ (ตัวอย่างห้อง ม.4/1 ตอบแล้ว 28 คน)'
    )
    act1 = LearningActivity.objects.filter(academic_year=cur_yr, title__startswith='กิจกรรมที่ 1:').first()
    if not act1:
        act1 = LearningActivity.objects.create(
            title='กิจกรรมที่ 1: ถามตอบ 1 + 1 = เท่าไร?',
            description=act1_desc,
            activity_type=LearningActivity.TYPE_QA,
            academic_year=cur_yr,
            score_period=p1,
            max_score=5.0,
            is_active=True,
            created_by=teacher_user,
        )
    elif force_refresh_samples:
        act1.description = act1_desc
        act1.activity_type = LearningActivity.TYPE_QA
        act1.score_period = act1.score_period or p1
        act1.is_active = True
        act1.save()

    act2_desc = (
        'ให้นักเรียนอัปโหลดรูปภาพผลไม้ที่ชื่นชอบคนละ 1 ภาพ (เช่น แอปเปิ้ล กล้วยหอม ส้ม แตงโม '
        'องุ่น มะม่วง สตรอว์เบอร์รี สับปะรด) พร้อมระบุชื่อผลไม้และเหตุผลสั้นๆ'
    )
    act2 = LearningActivity.objects.filter(academic_year=cur_yr, title__startswith='กิจกรรมที่ 2:').first()
    if not act2:
        act2 = LearningActivity.objects.create(
            title='กิจกรรมที่ 2: แนบรูปภาพส่ง (แนบรูปผลไม้)',
            description=act2_desc,
            activity_type=LearningActivity.TYPE_IMAGE,
            academic_year=cur_yr,
            score_period=p1,
            max_score=10.0,
            is_active=True,
            created_by=teacher_user,
        )
    elif force_refresh_samples:
        act2.description = act2_desc
        act2.activity_type = LearningActivity.TYPE_IMAGE
        act2.score_period = act2.score_period or p1
        act2.is_active = True
        act2.save()

    if students_r01 and (
        force_refresh_samples or ActivitySubmission.objects.filter(activity=act1, student__room_code='01').count() == 0
    ):
        # ส่งมาแล้ว 28 คน (เลขที่ 1..27 และ 30 (99999) เหลือ 2 คนยังไม่ส่ง)
        non_99999 = [s for s in students_r01 if s.student_id != '99999']
        st99 = next((s for s in students_r01 if s.student_id == '99999'), None)
        submitters_1 = non_99999[:27] + ([st99] if st99 else [])
        target_cnt_1 = max(1, min(28, len(students_r01) - 2 if len(students_r01) > 2 else len(students_r01)))
        submitters_1 = submitters_1[:target_cnt_1]
        answers = [
            '1 + 1 = 2',
            'เท่ากับ 2 ครับ',
            'คำตอบคือ 2',
            '2 ครับ',
        ]
        if force_refresh_samples:
            keep_ids = [s.id for s in submitters_1]
            ActivitySubmission.objects.filter(activity=act1, student__room_code='01').exclude(student_id__in=keep_ids).delete()
        for idx, st in enumerate(submitters_1):
            defaults = {
                'work_title': 'คำตอบ: 1 + 1 = 2',
                'work_text': answers[idx % len(answers)],
            }
            if force_refresh_samples:
                ActivitySubmission.objects.update_or_create(activity=act1, student=st, defaults=defaults)
            else:
                ActivitySubmission.objects.get_or_create(activity=act1, student=st, defaults=defaults)

    if students_r01 and (
        force_refresh_samples or ActivitySubmission.objects.filter(activity=act2, student__room_code='01').count() == 0
    ):
        for idx, st in enumerate(students_r01):
            fname, en_name, th_name, _fg, _bg = fruits[idx % len(fruits)]
            defaults = {
                'work_title': f'ภาพผลไม้: {th_name} ({en_name})',
                'work_text': f'ผลไม้ที่ชื่นชอบคือ {th_name} ({en_name}) — ส่งโดย {st.first_name} เลขที่ {st.class_number}',
                'work_file': f'activities/fruits/{fname}',
            }
            if force_refresh_samples:
                ActivitySubmission.objects.update_or_create(activity=act2, student=st, defaults=defaults)
            else:
                ActivitySubmission.objects.get_or_create(activity=act2, student=st, defaults=defaults)

    if act1 and not act1.answer_key_items.exists():
        ActivityAnswerKeyItem.objects.create(
            activity=act1,
            order_no=1,
            title='ข้อที่ 1: เฉลย 1 + 1',
            content_html='<p><strong>คำตอบที่ถูกต้องคือ 2</strong> (1 + 1 = 2)</p>',
            is_revealed=False,
        )

    if act1:
        ensure_activity_default_grading_tiers(act1)
    if act2:
        ensure_activity_default_grading_tiers(act2)

    # กิจกรรมที่ 3: กิจกรรมกลุ่มห้อง ม.4/1 แบ่งเป็น 10 กลุ่ม มีสมาชิกเลือกแล้ว 20 คน (ยกเว้น 99999)
    act3_desc = (
        'กิจกรรมกลุ่มประจำห้องเรียน ม.4/1 แบ่งเป็น 10 กลุ่ม '
        'ให้นักเรียนลากกล่องชื่อของตนเองไปวางในกลุ่มที่ต้องการ (สมาชิกเรียงลำดับตามเลขที่)'
    )
    act3 = LearningActivity.objects.filter(academic_year=cur_yr, title__startswith='กิจกรรมที่ 3:').first()
    if not act3:
        act3 = LearningActivity.objects.create(
            title='กิจกรรมที่ 3: กิจกรรมกลุ่ม (แบ่ง 10 กลุ่ม)',
            description=act3_desc,
            activity_type=LearningActivity.TYPE_GENERAL,
            academic_year=cur_yr,
            score_period=p1,
            max_score=10.0,
            is_active=True,
            is_group_activity=True,
            group_count=10,
            max_group_size=3,
            group_selection_open=True,
            target_rooms_json=json.dumps(['01']),
            created_by=teacher_user,
        )
    elif force_refresh_samples:
        act3.description = act3_desc
        act3.activity_type = LearningActivity.TYPE_GENERAL
        act3.score_period = act3.score_period or p1
        act3.max_score = 10.0
        act3.is_active = True
        act3.is_group_activity = True
        act3.group_count = 10
        act3.max_group_size = 3
        act3.group_selection_open = True
        act3.target_rooms_json = json.dumps(['01'])
        act3.save()

    ensure_activity_default_grading_tiers(act3)
    groups_r01 = ensure_activity_room_groups(act3, '01')

    if students_r01 and groups_r01 and (
        force_refresh_samples or ActivityGroupMember.objects.filter(activity=act3, group__room_code='01').count() == 0
    ):
        if force_refresh_samples:
            ActivityGroupMember.objects.filter(activity=act3, group__room_code='01').delete()
        non_99999_r01 = [s for s in students_r01 if (s.student_id or s.username) != '99999'][:20]
        # แบ่ง 20 คนลง 10 กลุ่ม -> กลุ่ม 1-2 เต็ม 3 คน, กลุ่ม 3-8 กลุ่มละ 2 คน, กลุ่ม 9-10 กลุ่มละ 1 คน
        quotas = [3, 3, 2, 2, 2, 2, 2, 2, 1, 1]
        cursor = 0
        for grp, q_cnt in zip(groups_r01[:10], quotas):
            for _ in range(q_cnt):
                if cursor >= len(non_99999_r01):
                    break
                ActivityGroupMember.objects.update_or_create(
                    activity=act3,
                    student=non_99999_r01[cursor],
                    defaults={'group': grp},
                )
                cursor += 1
        # กันเหนียว: 99999 ต้องยังไม่ถูกจัดเข้ากลุ่มใดในตัวอย่าง
        ActivityGroupMember.objects.filter(activity=act3, student__student_id='99999').delete()

    return {'act1': act1, 'act2': act2, 'act3': act3, 'total_r01': len(students_r01)}


def copy_classroom_layout(src_room, target_room, copy_numbers_mode='auto_vertical', per_col=None):
    """คัดลอกโครงสร้างแผนผัง (ขนาด แนวทางเดิน เลขเครื่อง กระดานดำ และการจัดเลขที่) จากห้องต้นทางไปยังห้องปลายทาง"""
    if not src_room or not target_room or src_room.pk == target_room.pk:
        return target_room
    src_ld = src_room.get_layout_dict()
    layout_dict = target_room.get_layout_dict()
    target_room.lab_room = src_room.lab_room
    target_room.seat_rows = src_room.get_effective_rows()
    target_room.seat_cols = src_room.get_effective_cols()
    target_room.board_position = src_room.get_effective_board_position()
    layout_dict['row_order'] = src_room.get_row_order()
    layout_dict['disabled_seats'] = sorted([str(x) for x in src_room.get_disabled_seats_set()])
    layout_dict['custom_labels'] = dict(src_room.get_custom_labels_dict())
    copied_dir = src_ld.get('auto_direction')
    if copied_dir not in VALID_AUTO_SEAT_DIRECTIONS:
        copied_dir = 'col_front_to_back'
    layout_dict['auto_direction'] = copied_dir

    if copy_numbers_mode == 'keep':
        new_nums = dict(src_room.get_seat_numbers_dict())
    elif copy_numbers_mode == 'clear':
        new_nums = {}
    else:
        disabled_set = set(layout_dict['disabled_seats'])
        st_nos = list(
            User.objects.filter(role=User.ROLE_STUDENT, room_code=target_room.code, class_number__gt=0)
            .order_by('class_number')
            .values_list('class_number', flat=True)
        )
        if not st_nos:
            enabled_count = max(0, (target_room.seat_rows * target_room.seat_cols) - len(disabled_set))
            st_nos = list(range(1, enabled_count + 1))
        new_nums = build_auto_seat_numbers(
            target_room.seat_rows,
            target_room.seat_cols,
            st_nos,
            disabled_seats=disabled_set,
            direction=copied_dir,
            per_col=per_col or None,
        )

    if target_room.lab_room_id:
        overrides = layout_dict.get('seat_overrides_by_lab')
        if not isinstance(overrides, dict):
            overrides = {}
        overrides[str(target_room.lab_room_id)] = new_nums
        layout_dict['seat_overrides_by_lab'] = overrides
    layout_dict['seat_numbers'] = new_nums
    target_room.layout_config = json.dumps(layout_dict)
    target_room.save()
    return target_room


# =============================================================================
# 7. ระบบสรุปคะแนน Lotto และให้คะแนนใบงานแบบลดหลั่นตามครั้งที่ให้คะแนน
# =============================================================================
@transaction.atomic
def finalize_lotto_session_scores(session, teacher_user=None):
    """สรุปคะแนน Debsirin Lotto: มอบคะแนนเฉพาะนักเรียนที่สุ่มได้และยังไม่ถูกลบออก"""
    pending = list(
        session.results.filter(removed=False, is_awarded=False)
        .select_related('student')
        .order_by('order_index', 'id')
    )
    if not pending:
        return [], 0.0

    active_period = get_active_score_period()
    if active_period.is_finalized:
        raise ValueError(f'ช่วงคะแนน "{active_period.name}" สรุปคะแนนและล็อกไว้แล้ว กรุณาสร้างช่วงคะแนนถัดไปก่อนสรุปผลสุ่ม')

    now = timezone.now()
    points_each = 0.0
    for res in pending:
        log = award_points_by_rule(
            student=res.student,
            rule_code='lotto_picked',
            default_name='ถูกสุ่มจาก Lotto',
            default_points=session.bonus_points,
            source_module='lotto',
            custom_reason=f'สรุปผลสุ่ม ID LOTTO รอบ "{session.title}" (ลำดับที่ {res.order_index})',
            awarded_by=teacher_user,
        )
        points_each = float(log.points)
        res.points_awarded = points_each
        res.is_awarded = True
        res.awarded_at = now
        res.save(update_fields=['points_awarded', 'is_awarded', 'awarded_at'])

    session.is_finalized = True
    session.finalized_at = now
    session.save(update_fields=['is_finalized', 'finalized_at'])
    return pending, points_each


def _normalize_bulk_room_code(room_code='all'):
    rc = (str(room_code) if room_code is not None else 'all').strip()
    if not rc or rc.lower() == 'all':
        return 'all'
    if rc.isdigit():
        return rc.zfill(2)
    return rc


def get_default_activity_max_score():
    """คืนคะแนนกิจกรรมล่าสุดที่จดจำไว้ (ค่าเริ่มต้น 5 คะแนน)"""
    setting = SystemSetting.get()
    return max(1.0, round(float(setting.last_activity_max_score or 5.0), 2))


def remember_activity_max_score(score_val):
    """บันทึกคะแนนกิจกรรมล่าสุดเพื่อเป็นค่าเริ่มต้นในกิจกรรมใหม่ถัดไป"""
    val = max(1.0, round(float(score_val or 5.0), 2))
    SystemSetting.objects.filter(pk=1).update(last_activity_max_score=val)
    setting = SystemSetting.get()
    if setting.last_activity_max_score != val:
        setting.last_activity_max_score = val
        setting.save(update_fields=['last_activity_max_score'])
    return val


def build_default_grading_tiers(max_score=5.0):
    """สร้างเกณฑ์คะแนนอัตโนมัติ 3 ครั้ง (ครั้งที่ 1 เต็ม, ครั้งที่ 2 -1, ครั้งที่ 3 เป็นต้นไป -2 ต่ำสุด 1)"""
    full = max(1.0, round(float(max_score or 5.0), 2))
    return [
        {'round_no': 1, 'score': full, 'label': 'ตรวจครั้งที่ 1 (คะแนนเต็ม)'},
        {'round_no': 2, 'score': max(1.0, round(full - 1.0, 2)), 'label': 'ตรวจครั้งที่ 2'},
        {'round_no': 3, 'score': max(1.0, round(full - 2.0, 2)), 'label': 'ตรวจครั้งที่ 3 เป็นต้นไป'},
    ]


def ensure_activity_default_grading_tiers(activity):
    """สร้าง ActivityGradingTier เริ่มต้น 3 รอบให้กิจกรรมโดยอัตโนมัติหากยังไม่มี"""
    if not activity or activity.grading_tiers.exists():
        return []
    objs = [
        ActivityGradingTier(
            activity=activity,
            round_no=item['round_no'],
            score=item['score'],
            label=item['label'],
        )
        for item in build_default_grading_tiers(activity.max_score)
    ]
    return ActivityGradingTier.objects.bulk_create(objs)


def get_next_activity_bulk_grade_info(activity, room_code='all'):
    """คำนวณครั้งที่ให้คะแนนและคะแนนที่จะได้รับในครั้งถัดไป (ตรวจรอบที่ x เป็นต้นไปใช้คะแนนรอบสุดท้ายที่ตั้งไว้)"""
    rc = _normalize_bulk_room_code(room_code)
    if not activity:
        return {
            'room_code': rc,
            'batch_no': 1,
            'next_score': 1.0,
            'full_score': 1.0,
            'prior_batches': [],
            'tiers': [],
            'has_custom_tiers': False,
            'graded_rounds_count': 0,
            'last_tier_round': None,
        }
    prior_qs = ActivityBulkGradeBatch.objects.filter(activity=activity, room_code=rc).order_by('-batch_no', '-id')
    prior_batches = list(prior_qs[:10])
    prior_count = prior_qs.count()
    batch_no = prior_count + 1
    full_score = max(1.0, float(activity.max_score or 10.0))
    tiers = list(ActivityGradingTier.objects.filter(activity=activity).order_by('round_no', 'id'))
    if tiers:
        chosen = next((t for t in tiers if t.round_no == batch_no), None)
        if not chosen:
            if batch_no >= tiers[-1].round_no:
                chosen = tiers[-1]
            else:
                earlier = [t for t in tiers if t.round_no < batch_no]
                chosen = earlier[-1] if earlier else tiers[0]
        next_score = round(float(chosen.score), 2)
    else:
        next_score = max(1.0, round(full_score - float(prior_count), 2))
    return {
        'room_code': rc,
        'batch_no': batch_no,
        'next_score': next_score,
        'full_score': full_score,
        'prior_batches': prior_batches,
        'tiers': tiers,
        'has_custom_tiers': bool(tiers),
        'graded_rounds_count': prior_count,
        'last_tier_round': tiers[-1].round_no if tiers else None,
    }


@transaction.atomic
def bulk_grade_activity_submissions(activity, room_code='all', teacher_user=None):
    """ให้คะแนนนักเรียนทุกคนในห้องที่ส่งงานแล้วและยังไม่ได้คะแนน (บันทึกครั้งที่ตรวจลงผลงานด้วย)"""
    rc = _normalize_bulk_room_code(room_code)
    target_period = activity.score_period or get_active_score_period()
    if target_period.is_finalized:
        raise ValueError(f'ช่วงคะแนน "{target_period.name}" สรุปคะแนนและล็อกไว้แล้ว ไม่สามารถให้คะแนนเพิ่มเติมได้')

    subs_qs = ActivitySubmission.objects.filter(
        activity=activity, score_awarded__isnull=True
    ).select_related('student')
    if rc != 'all':
        subs_qs = subs_qs.filter(student__room_code=rc)

    ungraded_subs = list(subs_qs.order_by('student__room_code', 'student__class_number', 'id'))
    if not ungraded_subs:
        return None, []

    info = get_next_activity_bulk_grade_info(activity, rc)
    batch_no = info['batch_no']
    score_val = info['next_score']
    full_score = info['full_score']

    for sub in ungraded_subs:
        sub.score_awarded = score_val
        sub.graded_round_no = batch_no
        if not sub.feedback:
            sub.feedback = f'ให้คะแนนรวมครั้งที่ {batch_no} ({score_val:g}/{full_score:g} คะแนน)'
        sub.save(update_fields=['score_awarded', 'graded_round_no', 'feedback'])

        ActivityPresentationDraw.objects.filter(
            activity=activity, student=sub.student, is_removed=False, is_graded=False
        ).update(
            is_graded=True,
            score_awarded=score_val,
            feedback=sub.feedback[:300],
        )

        ScoreLog.objects.create(
            student=sub.student,
            score_period=target_period,
            points=score_val,
            reason=f'ให้คะแนนผลงานกิจกรรม "{activity.title}" (ครั้งที่ {batch_no}: {score_val:g} คะแนน)',
            source_module='activity',
            awarded_by=teacher_user,
        )

    batch = ActivityBulkGradeBatch.objects.create(
        activity=activity,
        room_code=rc,
        batch_no=batch_no,
        score_given=score_val,
        graded_count=len(ungraded_subs),
        score_period=target_period,
        awarded_by=teacher_user,
    )
    return batch, ungraded_subs


# =============================================================================
# 8. โครงสร้างคะแนนรายช่วง (รวมตรงไม่หาร vs เฉลี่ยสัดส่วน)
# =============================================================================
def compute_period_structure(period):
    """คำนวณโครงสร้างกิจกรรม น้ำหนัก และคะแนนรวมของช่วงคะแนน (รวมแบบไม่หาร vs เฉลี่ยสัดส่วน)"""
    acts = list(
        LearningActivity.objects.filter(score_period=period)
        .annotate(submission_count=Count('submissions'))
        .order_by('id')
    )
    raw_activity_sum = round(sum(float(a.max_score or 0.0) for a in acts), 2)
    target_max = round(float(period.max_score or 0.0), 2)
    is_prop = (period.score_calc_mode == ScorePeriod.CALC_PROPORTIONAL)

    for a in acts:
        m = float(a.max_score or 0.0)
        a.share_pct = round((m * 100.0 / raw_activity_sum), 1) if raw_activity_sum > 0 else 0.0
        a.effective_max = round((m * target_max / raw_activity_sum), 2) if (is_prop and raw_activity_sum > 0) else round(m, 2)

    effective_activity_max = target_max if (is_prop and raw_activity_sum > 0) else raw_activity_sum
    diff_from_target = round(raw_activity_sum - target_max, 2)

    return {
        'period': period,
        'activities': acts,
        'raw_activity_sum': raw_activity_sum,
        'target_max_score': target_max,
        'effective_activity_max': effective_activity_max,
        'diff_from_target': diff_from_target,
        'is_proportional': is_prop,
    }


def compute_student_period_scores(period, logs_qs):
    """คำนวณคะแนนนักเรียนแยกตามกิจกรรม / บวก(+) / ลบ(-) / รวมในช่วง ตามโหมดของ ScorePeriod"""
    raw_buckets = {}
    for sid, src, pts in logs_qs.values_list('student_id', 'source_module', 'points'):
        b = raw_buckets.setdefault(sid, {'raw_activity': 0.0, 'plus': 0.0, 'minus': 0.0})
        val = float(pts or 0.0)
        if src == 'activity':
            b['raw_activity'] += val
        elif val >= 0:
            b['plus'] += val
        else:
            b['minus'] += val

    is_prop = bool(period and period.score_calc_mode == ScorePeriod.CALC_PROPORTIONAL)
    raw_max_sum = 0.0
    target_max = 0.0
    if is_prop:
        raw_max_sum = float(LearningActivity.objects.filter(score_period=period).aggregate(s=Sum('max_score'))['s'] or 0.0)
        target_max = float(period.max_score or 0.0)

    stats = {}
    for sid, b in raw_buckets.items():
        raw_act = b['raw_activity']
        eff_act = round(raw_act * target_max / raw_max_sum, 2) if (is_prop and raw_max_sum > 0) else round(raw_act, 2)
        net = round(eff_act + b['plus'] + b['minus'], 2)
        stats[sid] = {
            'raw_activity': round(raw_act, 2),
            'activity': eff_act,
            'plus': round(b['plus'], 2),
            'minus': round(b['minus'], 2),
            'net': net,
        }
    return stats


def recalculate_period_students_scores(period):
    """อัปเดตคะแนนรวม (total_score) ของนักเรียนที่มีรายการคะแนนในช่วงนี้เมื่อมีการเปลี่ยนโหมดหรือกิจกรรม"""
    if not period:
        return
    sids = list(
        ScoreLog.objects.filter(score_period=period).values_list('student_id', flat=True).distinct()
    )
    if not sids:
        return
    for st in User.objects.filter(pk__in=sids):
        st.recalculate_total_score()


# =============================================================================
# 9. สิทธิ์ส่งงาน / คำร้องขยายเวลา (ปิดภายใน 2 วัน) / ให้คะแนนลดสัดส่วน
# =============================================================================
def get_student_activity_access(activity, student):
    """ตรวจสอบว่านักเรียนคนนี้ยังส่งหรือแก้ไขใบงานนี้ได้หรือไม่ (คำร้องอนุมัติจะเปิดสิทธิ์เฉพาะรายคน)"""
    now = timezone.now()
    active_ext = (
        ActivityExtensionRequest.objects.filter(
            activity=activity,
            student=student,
            status=ActivityExtensionRequest.STATUS_APPROVED,
            approved_until__gte=now,
        )
        .order_by('-approved_until', '-id')
        .first()
    )
    latest_ext = (
        ActivityExtensionRequest.objects.filter(activity=activity, student=student)
        .order_by('-created_at', '-id')
        .first()
    )
    if active_ext:
        return {
            'is_locked': False,
            'lock_reason': '',
            'active_ext': active_ext,
            'latest_ext': latest_ext,
            'is_extended': True,
        }

    if activity.has_revealed_answers:
        return {
            'is_locked': True,
            'lock_reason': 'ครูเปิดเฉลยใบงานแล้ว ไม่สามารถส่งหรือแก้ไขงานได้',
            'active_ext': None,
            'latest_ext': latest_ext,
            'is_extended': False,
        }
    if not activity.is_active:
        return {
            'is_locked': True,
            'lock_reason': 'ใบงานนี้ปิดรับการส่งงานแล้ว',
            'active_ext': None,
            'latest_ext': latest_ext,
            'is_extended': False,
        }
    if activity.due_date and now > activity.due_date:
        return {
            'is_locked': True,
            'lock_reason': 'เลยกำหนดส่งงานแล้ว',
            'active_ext': None,
            'latest_ext': latest_ext,
            'is_extended': False,
        }
    return {
        'is_locked': False,
        'lock_reason': '',
        'active_ext': None,
        'latest_ext': latest_ext,
        'is_extended': False,
    }


def approve_extension_request(ext_req, teacher_user, approved_until=None, teacher_note=''):
    """อนุมัติคำร้องขอส่งงานล่าช้าเฉพาะนักเรียนคนนั้น (ค่าเริ่มต้น 2 วันนับจากเวลาที่อนุมัติ)"""
    now = timezone.now()
    target_until = approved_until or (now + timedelta(days=2))
    ext_req.status = ActivityExtensionRequest.STATUS_APPROVED
    ext_req.approved_until = target_until
    ext_req.teacher_note = (teacher_note or '').strip()[:255]
    ext_req.reviewed_by = teacher_user
    ext_req.reviewed_at = now
    ext_req.save()
    return ext_req


def reject_extension_request(ext_req, teacher_user, teacher_note=''):
    """ปฏิเสธคำร้องขอส่งงานล่าช้า"""
    now = timezone.now()
    ext_req.status = ActivityExtensionRequest.STATUS_REJECTED
    ext_req.teacher_note = (teacher_note or '').strip()[:255]
    ext_req.reviewed_by = teacher_user
    ext_req.reviewed_at = now
    ext_req.save()
    return ext_req


@transaction.atomic
def bulk_grade_filtered_ungraded_submissions(
    ungraded_subs,
    room_code='all',
    grade_mode='tier_auto',
    percent_value=80.0,
    teacher_user=None,
    override_score=None,
    consider_group=False,
    consider_material_check=False,
    group_source_activity_id=None,
    check_date=None,
):
    """
    ให้คะแนนผลงานที่ครูยังไม่ตรวจตามรอบของกิจกรรม (หรือคะแนนที่ครูกำหนดเอง)
    รองรับเงื่อนไขเพิ่มเติมสำหรับกิจกรรมเดี่ยว:
    - consider_group: พิจารณาแบบกลุ่ม (สมาชิกในกลุ่มเดียวกันต้องส่งกิจกรรมนี้ครบทุกคนจึงตรวจให้ทั้งกลุ่ม)
    - consider_material_check: พิจารณาตรวจสอบ (ต้องถูกกดตรวจสอบสมุด+หนังสือในวันที่ตรวจ)
    - เลือกทั้ง 2 เงื่อนไขพร้อมกัน: สมาชิกในกลุ่มทุกคนต้องทั้งส่งกิจกรรมนี้ และถูกตรวจสอบสมุด+หนังสือ
    """
    rc = _normalize_bulk_room_code(room_code)
    target_date = check_date or timezone.localdate()
    by_activity = {}
    for sub in ungraded_subs:
        if sub.score_awarded is None:
            by_activity.setdefault(sub.activity_id, []).append(sub)

    graded_count = 0
    skipped_locked_count = 0
    skipped_group_incomplete_count = 0
    skipped_unchecked_count = 0
    details = []
    pct = max(1.0, min(100.0, float(percent_value or 80.0)))

    checked_student_ids = set(
        StudentMaterialCheck.objects.filter(
            check_date=target_date, has_book=True, has_notebook=True
        ).values_list('student_id', flat=True)
    )
    room_group_cache = {}

    for act_id, subs in by_activity.items():
        act = subs[0].activity
        target_period = act.score_period or get_active_score_period()
        if target_period.is_finalized:
            skipped_locked_count += len(subs)
            continue

        use_grp = bool(consider_group) and not act.is_group_activity
        use_mat = bool(consider_material_check) and not act.is_group_activity
        src_grp_act_id = group_source_activity_id

        submitted_ids_all = set(
            ActivitySubmission.objects.filter(activity=act).values_list('student_id', flat=True)
        )

        eligible_subs = []
        for sub in subs:
            st = sub.student
            if use_grp:
                st_rc = (st.room_code or '01').strip() or '01'
                if (st_rc, src_grp_act_id) not in room_group_cache:
                    room_group_cache[(st_rc, src_grp_act_id)] = get_room_group_activity_and_members_map(
                        st_rc, group_activity_id=src_grp_act_id
                    )
                _, st_to_grp, grp_to_members = room_group_cache[(st_rc, src_grp_act_id)]
                grp = st_to_grp.get(st.id)
                if not grp:
                    skipped_group_incomplete_count += 1
                    continue
                members = grp_to_members.get(grp.id) or []
                if not members or any(m.id not in submitted_ids_all for m in members):
                    skipped_group_incomplete_count += 1
                    continue
                if use_mat and any(m.id not in checked_student_ids for m in members):
                    skipped_unchecked_count += 1
                    continue
            elif use_mat:
                if st.id not in checked_student_ids:
                    skipped_unchecked_count += 1
                    continue
            eligible_subs.append(sub)

        if not eligible_subs:
            continue

        info = get_next_activity_bulk_grade_info(act, rc)
        batch_no = info['batch_no']
        full_sc = info['full_score']

        if override_score is not None:
            score_val = max(0.0, round(float(override_score), 2))
            fb_text = f'ให้คะแนนรวมครั้งที่ {batch_no} ({score_val:g}/{full_sc:g} คะแนน)'
            create_batch_record = True
        elif grade_mode == 'percent':
            full_p = max(1.0, float(act.max_score or 5.0))
            score_val = max(1.0, round(full_p * pct / 100.0, 2))
            fb_text = f'ตรวจรวมลดตามสัดส่วน {pct:g}% (ครั้งที่ {batch_no}: {score_val:g}/{full_p:g} คะแนน)'
            create_batch_record = False
        else:
            score_val = info['next_score']
            fb_text = f'ให้คะแนนรวมครั้งที่ {batch_no} ({score_val:g}/{full_sc:g} คะแนน)'
            create_batch_record = True

        for sub in eligible_subs:
            sub.score_awarded = score_val
            sub.graded_round_no = batch_no
            if not sub.feedback:
                sub.feedback = fb_text[:300]
            sub.save(update_fields=['score_awarded', 'graded_round_no', 'feedback'])
            ActivityPresentationDraw.objects.filter(
                activity=act, student=sub.student, is_removed=False, is_graded=False
            ).update(
                is_graded=True,
                score_awarded=score_val,
                feedback=sub.feedback[:300],
            )
            ScoreLog.objects.create(
                student=sub.student,
                score_period=target_period,
                points=score_val,
                reason=f'ให้คะแนนผลงาน "{act.title}" (ครั้งที่ {batch_no}: {score_val:g} คะแนน)',
                source_module='activity',
                awarded_by=teacher_user,
            )
            graded_count += 1

        if create_batch_record:
            ActivityBulkGradeBatch.objects.create(
                activity=act,
                room_code=rc,
                batch_no=batch_no,
                score_given=score_val,
                graded_count=len(eligible_subs),
                score_period=target_period,
                awarded_by=teacher_user,
            )
        details.append(f'{act.title} (ครั้งที่ {batch_no}): +{score_val:g} คะแนน ({len(eligible_subs)} คน)')

    return {
        'graded_count': graded_count,
        'skipped_locked_count': skipped_locked_count,
        'skipped_group_incomplete_count': skipped_group_incomplete_count,
        'skipped_unchecked_count': skipped_unchecked_count,
        'details': details,
    }


# =============================================================================
# 10. ระบบตรวจสอบผู้ลงทะเบียน & จัดกลุ่มกิจกรรม (แบ่ง X กลุ่ม / ลากกล่องเข้ากลุ่ม)
# =============================================================================
def get_registered_student_ids(room_code='all', target_date=None):
    """คืนเซต ID ของนักเรียนที่ 'ลงทะเบียนแล้ว' (กำลัง Login ออนไลน์ หรือเช็คชื่อในคาบ/วันดังกล่าว)"""
    cur_yr = get_current_academic_year()
    rc = _normalize_bulk_room_code(room_code)
    tdate = target_date or timezone.localdate()
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(tdate, time.min), tz)
    end_dt = start_dt + timedelta(days=1)

    sess_qs = UserSession.objects.filter(
        is_active=True, user__role=User.ROLE_STUDENT, user__academic_year=cur_yr, user__is_active=True
    )
    att_qs = AttendanceRecord.objects.filter(
        student__role=User.ROLE_STUDENT, student__academic_year=cur_yr, student__is_active=True,
        session__academic_year=cur_yr,
    ).filter(
        Q(session__is_open=True) | Q(checked_at__gte=start_dt, checked_at__lt=end_dt)
    )
    if rc != 'all':
        sess_qs = sess_qs.filter(user__room_code=rc)
        att_qs = att_qs.filter(student__room_code=rc)

    reg_ids = set(sess_qs.values_list('user_id', flat=True))
    reg_ids.update(att_qs.values_list('student_id', flat=True))
    return reg_ids


def compute_group_max_size(activity, room_code='01', group_no=1):
    """คำนวณจำนวนสมาชิกสูงสุดของกลุ่มตามจำนวนกลุ่มที่แบ่ง (group_count) หรือค่า fallback"""
    rc = (room_code or '01').strip() or '01'
    if not activity:
        return 4
    g_cnt = int(getattr(activity, 'group_count', 0) or 0)
    if g_cnt > 0:
        act_yr = (getattr(activity, 'academic_year', '') or get_current_academic_year()).strip()
        room_student_cnt = User.objects.filter(
            role=User.ROLE_STUDENT, academic_year=act_yr, room_code=rc, is_active=True
        ).count()
        base, rem = divmod(max(1, room_student_cnt), g_cnt)
        g_idx = int(group_no or 1)
        cap = base + (1 if g_idx <= rem else 0)
        return max(1, cap)
    return max(1, int(activity.max_group_size or 4))


def ensure_activity_room_groups(activity, room_code='01'):
    """สร้างกลุ่มตามจำนวนกลุ่มที่กำหนด (group_count) หรือคำนวณจากห้องเรียนอัตโนมัติ"""
    rc = (room_code or '01').strip() or '01'
    if not activity or not activity.is_group_activity:
        return []
    g_cnt = int(getattr(activity, 'group_count', 0) or 0)
    if g_cnt > 0:
        needed = max(2, g_cnt)
        ActivityGroup.objects.filter(activity=activity, room_code=rc, group_no__gt=needed).annotate(
            mc=Count('members')
        ).filter(mc=0).delete()
    else:
        act_yr = (getattr(activity, 'academic_year', '') or get_current_academic_year()).strip()
        room_student_cnt = User.objects.filter(
            role=User.ROLE_STUDENT, academic_year=act_yr, room_code=rc, is_active=True
        ).count()
        max_sz = max(1, int(activity.max_group_size or 4))
        needed = max(4, math.ceil(max(1, room_student_cnt) / max_sz) + 1)

    existing_nos = set(
        ActivityGroup.objects.filter(activity=activity, room_code=rc).values_list('group_no', flat=True)
    )
    for gno in range(1, needed + 1):
        if gno not in existing_nos:
            ActivityGroup.objects.get_or_create(
                activity=activity,
                room_code=rc,
                group_no=gno,
                defaults={'name': f'กลุ่มที่ {gno}'},
            )
    return list(
        ActivityGroup.objects.filter(activity=activity, room_code=rc)
        .prefetch_related('members__student')
        .order_by('group_no', 'id')
    )


def serialize_activity_groups_for_room(activity, room_code='01', current_student=None):
    """คืนรายการกลุ่มของห้องเรียน พร้อมจำนวนสมาชิกสูงสุดของแต่ละกลุ่มและรายชื่อเรียงตามเลขที่เสมอ"""
    rc = (room_code or '01').strip() or '01'
    groups = ensure_activity_room_groups(activity, rc)
    payload = []
    for g in groups:
        max_sz = compute_group_max_size(activity, rc, g.group_no)
        sorted_members = sorted(
            g.members.all(),
            key=lambda m: (int(m.student.class_number or 9999), str(m.student.student_id or ''), m.id),
        )
        m_list = []
        is_mine = False
        for m in sorted_members:
            st = m.student
            is_me = bool(current_student and st.id == current_student.id)
            if is_me:
                is_mine = True
            m_list.append({
                'user_id': st.id,
                'student_id': st.student_id or st.username,
                'class_number': st.class_number,
                'full_name': st.get_full_name(),
                'short_name': st.first_name or st.username,
                'avatar_url': st.avatar_url,
                'avatar_initial': st.avatar_initial,
                'is_me': is_me,
            })
        payload.append({
            'id': g.id,
            'group_no': g.group_no,
            'name': g.name or f'กลุ่มที่ {g.group_no}',
            'max_size': max_sz,
            'remaining_slots': max(0, max_sz - len(m_list)),
            'count': len(m_list),
            'is_full': len(m_list) >= max_sz,
            'is_mine': is_mine,
            'members': m_list,
        })
    return payload


@transaction.atomic
def assign_student_to_activity_group(activity, student, target_group_id=None):
    """ย้าย/นำกล่องนักเรียนเข้ากลุ่ม หรือออกจากกลุ่ม (ตราบเท่าที่ยังไม่ปิดระบบจับกลุ่ม)"""
    if not activity or not activity.is_group_activity:
        raise ValueError('กิจกรรมนี้ไม่ได้เปิดเป็นกิจกรรมกลุ่ม')
    if not activity.group_selection_open:
        raise ValueError('ครูผู้สอนปิดระบบจับกลุ่มสำหรับกิจกรรมนี้แล้ว ไม่สามารถย้ายกลุ่มได้')

    rc = (student.room_code or '01').strip() or '01'
    raw_gid = str(target_group_id or '').strip()
    if not raw_gid or raw_gid in ('0', 'leave', 'none'):
        ActivityGroupMember.objects.filter(activity=activity, student=student).delete()
        return serialize_activity_groups_for_room(activity, rc, current_student=student)

    target_group = ActivityGroup.objects.select_for_update().filter(pk=int(raw_gid), activity=activity).first()
    if not target_group:
        raise ValueError('ไม่พบกลุ่มเป้าหมายที่เลือก')
    if student.room_code and target_group.room_code != student.room_code:
        raise ValueError('ไม่สามารถเข้ากลุ่มของห้องเรียนอื่นได้')

    max_sz = compute_group_max_size(activity, target_group.room_code, target_group.group_no)
    other_cnt = target_group.members.exclude(student=student).count()
    if other_cnt >= max_sz:
        raise ValueError(f'{target_group.name} เต็มแล้ว (สูงสุด {max_sz} คน)')

    ActivityGroupMember.objects.update_or_create(
        activity=activity,
        student=student,
        defaults={'group': target_group},
    )
    return serialize_activity_groups_for_room(activity, target_group.room_code, current_student=student)


# =============================================================================
# 11. ระบบปิดเช็คชื่ออัตโนมัติ (25 นาที) & ดึงห้องเช็คชื่อล่าสุด
# =============================================================================
def auto_close_expired_attendance_sessions(classroom=None):
    """ปิดรอบเช็คชื่อที่เปิดเกินระยะเวลา 25 นาที (auto_close_minutes) โดยอัตโนมัติ"""
    qs = AttendanceSession.objects.filter(is_open=True)
    if classroom is not None:
        qs = qs.filter(classroom=classroom)
    closed_count = 0
    for sess in qs:
        if sess.is_expired:
            sess.is_open = False
            sess.save(update_fields=['is_open'])
            closed_count += 1
    return closed_count


def get_latest_attendance_room_code():
    """คืนรหัสห้องที่มีการเปิดเช็คชื่อล่าสุด (แม้ปิดระบบเช็คชื่อแล้วก็ตาม) สำหรับตั้งเป็นตัวกรองหลัก"""
    cur_yr = get_current_academic_year()
    latest = (
        AttendanceSession.objects.filter(academic_year=cur_yr)
        .select_related('classroom')
        .order_by('-opened_at', '-created_at', '-id')
        .first()
    )
    if latest and latest.classroom_id and latest.classroom.code:
        return latest.classroom.code
    return ''


# =============================================================================
# 12. กลุ่มกิจกรรมอ้างอิง / AI ตรวจคำตอบและคำตอบซ้ำเพื่อน / Bingo / ID LOTTO Filters
# =============================================================================
def get_group_source_activities_for_room(room_code='all'):
    """คืนรายการกิจกรรมกลุ่มที่มีการจัดแบ่งสมาชิกกลุ่มไว้แล้ว (สำหรับอ้างอิงกิจกรรมเดี่ยวหรือลงโทษเข้าสายทั้งกลุ่ม)"""
    cur_yr = get_current_academic_year()
    rc = _normalize_bulk_room_code(room_code)
    qs = LearningActivity.objects.filter(is_group_activity=True, academic_year=cur_yr)
    if rc != 'all':
        qs = qs.filter(group_members__group__room_code=rc)
    else:
        qs = qs.filter(group_members__isnull=False)
    return list(qs.distinct().order_by('-created_at', '-id'))


def get_room_group_activity_and_members_map(room_code, group_activity_id=None):
    """คืน (group_act, student_id->ActivityGroup, group_id->list[User]) สำหรับห้องเรียนที่ระบุ"""
    cur_yr = get_current_academic_year()
    rc = (room_code or '01').strip() or '01'
    if rc.isdigit():
        rc = rc.zfill(2)

    group_act = None
    if group_activity_id and str(group_activity_id).isdigit():
        cand = LearningActivity.objects.filter(pk=int(group_activity_id), is_group_activity=True).first()
        if cand and ActivityGroupMember.objects.filter(activity=cand, group__room_code=rc).exists():
            group_act = cand
    if not group_act:
        group_act = (
            LearningActivity.objects.filter(
                is_group_activity=True,
                academic_year=cur_yr,
                group_members__group__room_code=rc,
            )
            .distinct()
            .order_by('-created_at', '-id')
            .first()
        )
    if not group_act:
        return None, {}, {}

    student_to_group = {}
    group_to_members = {}
    memberships = (
        ActivityGroupMember.objects.filter(activity=group_act, group__room_code=rc)
        .select_related('group', 'student')
        .order_by('student__class_number', 'student__student_id')
    )
    for m in memberships:
        student_to_group[m.student_id] = m.group
        group_to_members.setdefault(m.group_id, []).append(m.student)
    return group_act, student_to_group, group_to_members


def _normalize_compare_text(text):
    raw = str(text or '').strip().lower()
    raw = re.sub(r'\s+', ' ', raw)
    dense = re.sub(r'[\s\W_]+', '', raw)
    return raw, dense


def _char_ngrams(text, n=3):
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def check_submission_peer_similarity(activity, student, text):
    """ตรวจสอบว่าข้อความคำตอบของนักเรียนคล้ายกับเพื่อนที่ส่งมาก่อนหน้าหรือไม่ (ไม่เปิดเผยชื่อเพื่อน)"""
    raw_norm, dense = _normalize_compare_text(text)
    if len(dense) < 12:
        return {
            'is_similar': False,
            'similarity_percent': 0.0,
            'similarity_score': 0.0,
            'similarity_warning': '',
        }

    peers_qs = ActivitySubmission.objects.filter(activity=activity).exclude(student=student)
    if activity.is_group_activity and student is not None:
        my_mem = ActivityGroupMember.objects.filter(activity=activity, student=student).first()
        if my_mem:
            teammate_ids = ActivityGroupMember.objects.filter(
                activity=activity, group=my_mem.group
            ).values_list('student_id', flat=True)
            peers_qs = peers_qs.exclude(student_id__in=teammate_ids)

    my_grams = _char_ngrams(dense, 3)
    best_sim = 0.0
    for peer_sub in peers_qs.only('work_title', 'work_text'):
        peer_combined = ' '.join(
            p for p in [peer_sub.work_text or '', peer_sub.work_title or ''] if p.strip()
        ).strip()
        if not peer_combined:
            continue
        p_raw, p_dense = _normalize_compare_text(peer_combined)
        if len(p_dense) < 12:
            continue
        seq_ratio = SequenceMatcher(None, dense, p_dense).ratio()
        p_grams = _char_ngrams(p_dense, 3)
        union = my_grams | p_grams
        jaccard = (len(my_grams & p_grams) / float(len(union))) if union else 0.0
        sim = max(seq_ratio, jaccard)
        if raw_norm == p_raw:
            sim = 1.0
        if sim > best_sim:
            best_sim = sim

    pct = round(best_sim * 100.0, 1)
    if best_sim >= 0.72:
        return {
            'is_similar': True,
            'similarity_percent': pct,
            'similarity_score': round(best_sim, 3),
            'similarity_warning': f'พบข้อความคำตอบคล้ายกับเพื่อนนักเรียนที่ส่งมาแล้ว ({pct:g}%) กรุณาเรียบเรียงคำอธิบายด้วยตนเอง',
        }
    return {
        'is_similar': False,
        'similarity_percent': pct,
        'similarity_score': round(best_sim, 3),
        'similarity_warning': '',
    }


def evaluate_student_submission_with_ai(activity, student, work_title='', work_text='', has_file=False):
    """
    ตรวจประเมินคำตอบเบื้องต้นด้วย AI (Gemini/Claude หากตั้งค่า API Key ไว้ หรือ Smart Heuristic Engine)
    ว่าคำตอบเพียงพอและตรงกับคำถามหรือไม่ พร้อมเตือนเมื่อคล้ายคำตอบของเพื่อน
    """
    combined = (work_text or '').strip()
    if not combined:
        combined = (work_title or '').strip()
    elif work_title and work_title.strip() not in combined and not work_title.startswith('ผลงานรูปภาพ:'):
        combined = f'{work_title.strip()} {combined}'.strip()

    sim_info = check_submission_peer_similarity(activity, student, combined)
    raw_norm, dense = _normalize_compare_text(combined)

    prompt_parts = [activity.title or '', activity.description or '', strip_tags(activity.content_html or '')]
    for ak in activity.answer_key_items.all()[:5]:
        prompt_parts.append(ak.title or '')
        prompt_parts.append(strip_tags(ak.content_html or ''))
    prompt_text = ' '.join(p for p in prompt_parts if p).strip()
    _, prompt_dense = _normalize_compare_text(prompt_text)

    ai_engine = 'Smart Answer Check'
    is_sufficient = True
    is_relevant = True
    ai_feedback = 'คำตอบมีความครบถ้วนและตรงกับประเด็นคำถาม สามารถกดส่งได้'

    placeholders = {'ไม่รู้', 'ไม่ทราบ', 'ไม่มี', 'ทดสอบ', 'test', 'asdf', 'qwer', 'xxx', 'none', 'na', 'null', '-', '.', '...'}
    if not dense and not has_file:
        is_sufficient = False
        is_relevant = False
        ai_feedback = 'ยังไม่ได้ระบุคำตอบหรือแนบไฟล์ผลงาน กรุณากรอกรายละเอียดให้ครบก่อนส่ง'
    elif dense in placeholders or raw_norm in placeholders or (len(dense) >= 3 and len(set(dense)) == 1):
        is_sufficient = False
        is_relevant = False
        ai_feedback = 'คำตอบยังไม่ครบถ้วนหรือเป็นข้อความทดสอบ กรุณาอธิบายคำตอบที่แท้จริงให้ชัดเจน'
    elif has_file and activity.activity_type == LearningActivity.TYPE_IMAGE and not dense:
        is_sufficient = True
        is_relevant = True
        ai_feedback = 'แนบไฟล์ภาพผลงานแล้ว แนะนำให้พิมพ์คำอธิบายสั้นๆ ประกอบภาพเพิ่มเติม'
    else:
        setting = SystemSetting.get()
        used_external_ai = False
        api_prompt = (
            f'ตรวจคำตอบนักเรียนเบื้องต้นอย่างกระชับ:\n'
            f'โจทย์/กิจกรรม: {prompt_text[:600]}\n'
            f'คำตอบนักเรียน: {combined[:800]} (แนบไฟล์: {"มี" if has_file else "ไม่มี"})\n'
            f'ตอบกลับเป็น JSON Object เดียวเท่านั้น: '
            f'{{"is_sufficient": true/false, "is_relevant": true/false, "feedback": "คำแนะนำสั้นๆ ภาษาไทย"}}'
        )
        if setting.ai_provider == 'gemini' and (setting.gemini_api_key or '').strip():
            try:
                url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={setting.gemini_api_key.strip()}'
                payload = json.dumps({'contents': [{'parts': [{'text': api_prompt}]}]}).encode('utf-8')
                req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    text_out = data['candidates'][0]['content']['parts'][0]['text']
                    m = re.search(r'\{.*\}', text_out, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                        is_sufficient = bool(parsed.get('is_sufficient', True))
                        is_relevant = bool(parsed.get('is_relevant', True))
                        ai_feedback = str(parsed.get('feedback') or ai_feedback)[:260]
                        ai_engine = 'Google Gemini AI'
                        used_external_ai = True
            except Exception:
                pass
        elif setting.ai_provider == 'claude' and (setting.claude_api_key or '').strip():
            try:
                url = 'https://api.anthropic.com/v1/messages'
                payload = json.dumps({
                    'model': 'claude-3-haiku-20240307',
                    'max_tokens': 300,
                    'messages': [{'role': 'user', 'content': api_prompt}],
                }).encode('utf-8')
                req = urllib.request.Request(url, data=payload, headers={
                    'Content-Type': 'application/json',
                    'x-api-key': setting.claude_api_key.strip(),
                    'anthropic-version': '2023-06-01',
                })
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    text_out = data['content'][0]['text']
                    m = re.search(r'\{.*\}', text_out, re.DOTALL)
                    if m:
                        parsed = json.loads(m.group(0))
                        is_sufficient = bool(parsed.get('is_sufficient', True))
                        is_relevant = bool(parsed.get('is_relevant', True))
                        ai_feedback = str(parsed.get('feedback') or ai_feedback)[:260]
                        ai_engine = 'Anthropic Claude AI'
                        used_external_ai = True
            except Exception:
                pass

        if not used_external_ai:
            is_short_math_prompt = bool(re.search(r'(\d+\s*[+\-×x*/=]\s*\d+|เท่าไร|กี่|คำนวณ|ผลลัพธ์)', prompt_text))
            has_digits = bool(re.search(r'\d', combined))
            if is_short_math_prompt:
                if not has_digits and len(dense) < 6:
                    is_sufficient = False
                    is_relevant = False
                    ai_feedback = 'คำถามลักษณะนี้ต้องการคำตอบที่เป็นตัวเลขหรือผลลัพธ์ที่ชัดเจน กรุณาตรวจสอบคำตอบอีกครั้ง'
                elif not has_digits and not any(k in dense for k in ('สอง', 'หนึ่ง', 'สาม', 'สี่', 'ห้า', 'คำตอบ', 'ตอบ')):
                    is_relevant = False
                    ai_feedback = 'คำตอบยังไม่พบตัวเลขหรือผลลัพธ์ที่สอดคล้องกับโจทย์คำถาม'
            else:
                if not has_file and len(dense) < 6:
                    is_sufficient = False
                    ai_feedback = 'คำตอบยังสั้นเกินไป แนะนำให้อธิบายรายละเอียดหรือเหตุผลให้ครบถ้วนมากขึ้น'
                elif len(prompt_dense) >= 6 and len(dense) >= 6:
                    # ตรวจสอบความสอดคล้องของคำสำคัญระหว่างโจทย์/เฉลย กับคำตอบ (ข้ามกรณีเฉลยไม่เปิดเผย)
                    ascii_ans_tokens = set(re.findall(r'[a-z0-9]{2,}', raw_norm))
                    ascii_prompt_tokens = set(re.findall(r'[a-z0-9]{2,}', prompt_text.lower()))
                    p_grams = _char_ngrams(prompt_dense, 3)
                    a_grams = _char_ngrams(dense, 3)
                    gram_overlap = len(p_grams & a_grams)
                    tok_overlap = len(ascii_ans_tokens & ascii_prompt_tokens)
                    off_topic_markers = ('หิวข้าว', 'กินข้าว', 'หมูปิ้ง', 'เล่นเกม', 'ง่วงนอน', 'สบายดี', 'อะไรก็ได้')
                    if (any(mk in dense for mk in off_topic_markers) or (len(prompt_dense) >= 15 and len(dense) >= 12)) and gram_overlap == 0 and tok_overlap == 0 and not has_file:
                        is_relevant = False
                        ai_feedback = 'คำตอบดูไม่สอดคล้องกับหัวข้อกิจกรรมที่ถาม กรุณาปรับคำตอบให้ตรงประเด็น'

    status = 'warning' if (not is_sufficient or not is_relevant or sim_info['is_similar']) else 'pass'
    return {
        'ok': True,
        'status': status,
        'is_sufficient': is_sufficient,
        'is_relevant': is_relevant,
        'ai_feedback': ai_feedback,
        'ai_engine': ai_engine,
        'is_similar': sim_info['is_similar'],
        'similarity_percent': sim_info['similarity_percent'],
        'similarity_score': sim_info['similarity_score'],
        'similarity_warning': sim_info['similarity_warning'],
    }


def get_valid_student_bingo_picks(room, student):
    """คืนรายการตัวเลขบนตาราง Bingo ของนักเรียนที่เลือกออกเลขได้ตามกติกา (ไม่ใช่มุม/กลาง และไม่ทำให้ตนเอง Bingo ทันที)"""
    card = BingoCard.objects.filter(room=room, student=student).first()
    if not card:
        return []
    valid = []
    seen = set()
    for num in card.get_grid_list():
        if num in seen:
            continue
        seen.add(num)
        ok, _ = validate_student_bingo_pick(room, student, num)
        if ok:
            valid.append(num)
    return sorted(valid)


def get_late_last_x_student_ids(room_code=None, target_date=None, last_x_count=5):
    """
    คืน set ของ student_id ที่ถือว่าเข้าสาย / เป็น X คนสุดท้ายของวัน (ใช้กรองใน ID LOTTO):
    - พิจารณาจาก ScoreLog ติดลบในหมวด attendance ในวันดังกล่าว หรือ AttendanceRecord ที่ status=late
    - หากยังไม่มีรายการบันทึกเข้าสาย จะดึง X คนสุดท้ายที่ลงทะเบียนในวันนั้น
    """
    tdate = target_date or timezone.localdate()
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(tdate, time.min), tz)
    end_dt = start_dt + timedelta(days=1)

    rc = None
    if room_code and str(room_code).strip().lower() not in ('all', ''):
        rc = str(room_code).strip()
        if rc.isdigit():
            rc = rc.zfill(2)

    late_log_qs = ScoreLog.objects.filter(
        source_module='attendance',
        points__lt=0,
        created_at__range=(start_dt, end_dt),
    )
    if rc:
        late_log_qs = late_log_qs.filter(student__room_code=rc)
    late_ids = set(late_log_qs.values_list('student_id', flat=True))

    att_late_qs = AttendanceRecord.objects.filter(
        status=AttendanceRecord.STATUS_LATE,
        checked_at__range=(start_dt, end_dt),
    )
    if rc:
        att_late_qs = att_late_qs.filter(student__room_code=rc)
    late_ids.update(att_late_qs.values_list('student_id', flat=True))

    if late_ids:
        return late_ids

    # Fallback: X คนสุดท้ายที่เช็คชื่อหรือ Login ล่าสุดในวันนั้น
    x_n = max(1, min(50, int(last_x_count or 5)))
    time_map = {}
    rec_qs = AttendanceRecord.objects.filter(checked_at__range=(start_dt, end_dt))
    if rc:
        rec_qs = rec_qs.filter(student__room_code=rc)
    for sid, dt in rec_qs.values_list('student_id', 'checked_at'):
        if sid not in time_map or dt > time_map[sid]:
            time_map[sid] = dt

    sess_qs = UserSession.objects.filter(
        user__role=User.ROLE_STUDENT, is_active=True, last_active__range=(start_dt, end_dt)
    )
    if rc:
        sess_qs = sess_qs.filter(user__room_code=rc)
    for sid, dt in sess_qs.values_list('user_id', 'last_active'):
        if sid not in time_map or dt > time_map[sid]:
            time_map[sid] = dt

    ordered = sorted(time_map.items(), key=lambda kv: kv[1], reverse=True)
    return {sid for sid, _ in ordered[:x_n]}


def get_lotto_eligible_students_qs(session, override_filters=None):
    """คืน QuerySet นักเรียนที่มีสิทธิ์ถูกสุ่มใน ID LOTTO ตามห้อง กลุ่ม และ 4 ตัวกรองเพิ่มเติม"""
    ov = override_filters or {}
    sess_yr = (getattr(session, 'academic_year', '') or get_current_academic_year()).strip()
    cand_qs = User.objects.filter(role=User.ROLE_STUDENT, academic_year=sess_yr, is_active=True)

    rc = None
    if session.classroom_id and session.classroom.code:
        rc = session.classroom.code
        cand_qs = cand_qs.filter(room_code=rc)
    if session.group_filter:
        cand_qs = cand_qs.filter(group_name__iexact=session.group_filter)

    removed_ids = session.results.filter(removed=True).values_list('student_id', flat=True)
    cand_qs = cand_qs.exclude(id__in=removed_ids)
    if session.only_never_picked_in_session:
        picked_ids = session.results.values_list('student_id', flat=True)
        cand_qs = cand_qs.exclude(id__in=picked_ids)

    f_sub = ov.get('filter_submitted_activity', session.filter_submitted_activity)
    f_act_id = ov.get('filter_activity_id', session.filter_activity_id)
    f_excl_last_x = ov.get('filter_exclude_last_x', session.filter_exclude_last_x)
    f_last_x = ov.get('filter_last_x_count', session.filter_last_x_count or 5)
    f_mat = ov.get('filter_material_checked', session.filter_material_checked)
    f_pres = ov.get('filter_presented_activity', session.filter_presented_activity)
    f_date = ov.get('filter_check_date', session.filter_check_date) or timezone.localdate()

    if f_sub:
        sub_qs = ActivitySubmission.objects.all()
        if f_act_id and str(f_act_id).isdigit():
            sub_qs = sub_qs.filter(activity_id=int(f_act_id))
        cand_qs = cand_qs.filter(id__in=sub_qs.values_list('student_id', flat=True))

    if f_excl_last_x:
        late_ids = get_late_last_x_student_ids(room_code=rc, target_date=f_date, last_x_count=f_last_x)
        if late_ids:
            cand_qs = cand_qs.exclude(id__in=late_ids)

    if f_mat:
        checked_ids = StudentMaterialCheck.objects.filter(
            check_date=f_date, has_book=True, has_notebook=True
        ).values_list('student_id', flat=True)
        cand_qs = cand_qs.filter(id__in=checked_ids)

    if f_pres:
        pres_qs = ActivityPresentationDraw.objects.filter(is_removed=False)
        if f_act_id and str(f_act_id).isdigit():
            pres_qs = pres_qs.filter(activity_id=int(f_act_id))
        cand_qs = cand_qs.filter(id__in=pres_qs.values_list('student_id', flat=True))

    return cand_qs


# =============================================================================
# 13. ลบกิจกรรมเรียงลำดับพร้อมคำนวณคะแนนใหม่ & ระดับถ้วยรางวัลตามช่วงคะแนน (%)
# =============================================================================
@transaction.atomic
def delete_sorting_activity_and_recalculate(activity):
    """ลบกิจกรรมเรียงลำดับ พร้อมหัก/ลบ ScoreLog ที่มาจากกิจกรรมนี้ออกและคำนวณคะแนนนักเรียนใหม่"""
    if activity is None:
        return 0, 0
    act_id = activity.id
    act_title = activity.title or ''
    matched_logs_qs = ScoreLog.objects.filter(source_module='sorting').filter(
        Q(reason__contains=f'[SORT_ACT:{act_id}]')
        | (Q(reason__contains=f'({act_title} ') | Q(reason__contains=f'({act_title}·') | Q(reason__contains=f'({act_title})'))
    ).select_related('score_period')

    matched_logs = list(matched_logs_qs)
    for log in matched_logs:
        if log.score_period_id and log.score_period.is_finalized:
            raise ValueError(f'ไม่สามารถลบกิจกรรม "{act_title}" ได้ เนื่องจากคะแนนบางส่วนอยู่ในช่วงคะแนนที่สรุปล็อกไว้แล้ว')

    affected_sids = {log.student_id for log in matched_logs}
    affected_sids.update(
        SortingScore.objects.filter(activity=activity).values_list('student_id', flat=True)
    )

    deleted_logs_count = len(matched_logs)
    if matched_logs:
        ScoreLog.objects.filter(pk__in=[l.id for l in matched_logs]).delete()

    activity.delete()

    recalculated_count = 0
    if affected_sids:
        for st in User.objects.filter(pk__in=affected_sids):
            st.recalculate_total_score()
            recalculated_count += 1
    return deleted_logs_count, recalculated_count


DEFAULT_SCORE_RANK_TIERS = [
    {'order_no': 1, 'code': 'member', 'name': 'Member', 'operator': 'gte', 'min_percent': 0.0, 'color_hex': '#64748b', 'default_icon_path': '/static/ranks/trophy_member.png'},
    {'order_no': 2, 'code': 'bronze', 'name': 'Bronze', 'operator': 'gt', 'min_percent': 20.0, 'color_hex': '#c2410c', 'default_icon_path': '/static/ranks/trophy_bronze.png'},
    {'order_no': 3, 'code': 'silver', 'name': 'Silver', 'operator': 'gt', 'min_percent': 40.0, 'color_hex': '#475569', 'default_icon_path': '/static/ranks/trophy_silver.png'},
    {'order_no': 4, 'code': 'gold', 'name': 'Gold', 'operator': 'gt', 'min_percent': 50.0, 'color_hex': '#ca8a04', 'default_icon_path': '/static/ranks/trophy_gold.png'},
    {'order_no': 5, 'code': 'platinum', 'name': 'Platinum', 'operator': 'gt', 'min_percent': 70.0, 'color_hex': '#0891b2', 'default_icon_path': '/static/ranks/trophy_platinum.png'},
    {'order_no': 6, 'code': 'diamond', 'name': 'Diamond', 'operator': 'gt', 'min_percent': 80.0, 'color_hex': '#2563eb', 'default_icon_path': '/static/ranks/trophy_diamond.png'},
    {'order_no': 7, 'code': 'crown', 'name': 'Crown', 'operator': 'gte', 'min_percent': 95.0, 'color_hex': '#d97706', 'default_icon_path': '/static/ranks/trophy_crown.png'},
]


def ensure_default_score_rank_tiers(reset=False):
    """สร้างหรือรีเซ็ตระดับถ้วยรางวัลเริ่มต้นทั้ง 7 ระดับ (Member, Bronze, Silver, Gold, Platinum, Diamond, Crown)"""
    if not reset and ScoreRankTier.objects.exists():
        return list(ScoreRankTier.objects.order_by('order_no', 'min_percent', 'id'))
    tiers = []
    for item in DEFAULT_SCORE_RANK_TIERS:
        obj, created = ScoreRankTier.objects.get_or_create(code=item['code'], defaults=item)
        if reset and not created:
            for k, v in item.items():
                setattr(obj, k, v)
            if obj.custom_icon:
                obj.custom_icon.delete(save=False)
                obj.custom_icon = None
            obj.save()
        tiers.append(obj)
    return list(ScoreRankTier.objects.order_by('order_no', 'min_percent', 'id'))


def resolve_score_rank(percent_val, tiers=None):
    """คืน ScoreRankTier ที่ตรงกับค่าเปอร์เซ็นต์ (ประเมินจากระดับสูงสุดลงมา)"""
    if tiers is None:
        tiers = ensure_default_score_rank_tiers()
    if not tiers:
        return None
    p = max(0.0, min(100.0, float(percent_val or 0.0)))
    ordered_desc = sorted(
        tiers,
        key=lambda t: (float(t.min_percent or 0.0), 1 if t.operator == ScoreRankTier.OP_GT else 0, int(t.order_no or 0)),
        reverse=True,
    )
    for tier in ordered_desc:
        if tier.matches(p):
            return tier
    ordered_asc = sorted(tiers, key=lambda t: (float(t.min_percent or 0.0), int(t.order_no or 0)))
    return ordered_asc[0]


def compute_student_rank_overview(student, periods=None, tiers=None):
    """คำนวณระดับถ้วยรางวัลของนักเรียนโดยรวมทั้ง 2 ช่วงคะแนน (คิดเป็น % เต็ม 100%) และแยกรายช่วง"""
    if tiers is None:
        tiers = ensure_default_score_rank_tiers()
    if periods is None:
        st_yr = (getattr(student, 'academic_year', '') or get_current_academic_year()).strip()
        periods = list(ScorePeriod.objects.filter(academic_year=st_yr).order_by('order_no', 'id'))
    tiers_asc = sorted(tiers, key=lambda t: (float(t.min_percent or 0.0), int(t.order_no or 0)))

    def _find_next_tier(cur_tier):
        if not cur_tier:
            return tiers_asc[0] if tiers_asc else None
        for t in tiers_asc:
            if float(t.min_percent or 0.0) > float(cur_tier.min_percent or 0.0):
                return t
        return None

    period_entries = []
    overall_score = 0.0
    overall_max = 0.0
    for p in periods:
        p_stats = compute_student_period_scores(p, ScoreLog.objects.filter(student=student, score_period=p)).get(
            student.id, {'raw_activity': 0.0, 'activity': 0.0, 'plus': 0.0, 'minus': 0.0, 'net': 0.0}
        )
        net_pts = float(p_stats['net'] or 0.0)
        p_max = float(p.max_score or 0.0)
        pct = round(max(0.0, min(100.0, (net_pts / p_max) * 100.0)), 2) if p_max > 0 else 0.0
        p_rank = resolve_score_rank(pct, tiers)
        period_entries.append({
            'period': p,
            'raw_activity_pts': round(p_stats['raw_activity'], 2),
            'activity_pts': round(p_stats['activity'], 2),
            'plus_pts': round(p_stats['plus'], 2),
            'minus_pts': round(p_stats['minus'], 2),
            'net_pts': round(net_pts, 2),
            'max_score': p_max,
            'percent': pct,
            'rank': p_rank,
            'next_rank': _find_next_tier(p_rank),
        })
        overall_score += net_pts
        overall_max += p_max

    overall_score = round(overall_score, 2)
    if overall_max > 0:
        overall_percent = round(max(0.0, min(100.0, (overall_score / overall_max) * 100.0)), 2)
    else:
        overall_percent = round(max(0.0, min(100.0, float(student.total_score or 0.0))), 2)

    overall_rank = resolve_score_rank(overall_percent, tiers)
    next_rank = _find_next_tier(overall_rank)
    held_tier_ids = {e['rank'].id for e in period_entries if e.get('rank')}
    if overall_rank:
        held_tier_ids.add(overall_rank.id)

    return {
        'overall_score': overall_score,
        'overall_max': round(overall_max, 2),
        'overall_percent': overall_percent,
        'overall_rank': overall_rank,
        'next_rank': next_rank,
        'period_entries': period_entries,
        'tiers': tiers_asc,
        'held_tier_ids': list(held_tier_ids),
    }


def build_room_period_stats_tabs(student, room_code, periods=None, tiers=None):
    """สร้างข้อมูลสถิติคะแนนแยกตามช่วงคะแนน เรียงจากคะแนนมากไปน้อย โดยไม่เปิดเผยชื่อของเพื่อนร่วมห้อง"""
    if tiers is None:
        tiers = ensure_default_score_rank_tiers()
    st_yr = (getattr(student, 'academic_year', '') or get_current_academic_year()).strip()
    if periods is None:
        periods = list(ScorePeriod.objects.filter(academic_year=st_yr).order_by('order_no', 'id'))

    students = list(User.objects.filter(role=User.ROLE_STUDENT, academic_year=st_yr, room_code=room_code, is_active=True))
    tabs = []
    for p in periods:
        p_map = compute_student_period_scores(p, ScoreLog.objects.filter(score_period=p, student__room_code=room_code))
        p_max = float(p.max_score or 0.0)
        raw_rows = []
        for st in students:
            st_stats = p_map.get(st.id, {'net': 0.0})
            net_pts = round(float(st_stats.get('net') or 0.0), 2)
            pct = round(max(0.0, min(100.0, (net_pts / p_max) * 100.0)), 2) if p_max > 0 else 0.0
            rank_tier = resolve_score_rank(pct, tiers)
            raw_rows.append({
                'student': st,
                'is_me': (st.id == student.id),
                'net_pts': net_pts,
                'percent': pct,
                'rank': rank_tier,
                'class_number': st.class_number or 9999,
                'student_id': st.student_id or '',
            })

        raw_rows.sort(key=lambda r: (-r['net_pts'], r['class_number'], r['student_id']))
        prev_score = None
        cur_rank = 0
        my_row = None
        for idx, row in enumerate(raw_rows, start=1):
            if prev_score is None or abs(row['net_pts'] - prev_score) > 1e-6:
                cur_rank = idx
                prev_score = row['net_pts']
            row['rank_no'] = cur_rank
            if row['is_me']:
                my_row = row

        period_logs = list(student.score_logs.filter(score_period=p).select_related('rule', 'score_period'))
        tabs.append({
            'period': p,
            'rows': raw_rows,
            'my_row': my_row,
            'total_students': len(raw_rows),
            'period_logs': period_logs,
        })
    return tabs


def get_user_ui_preferences(user):
    """ดึงการตั้งค่าการใช้งานล่าสุดของผู้ใช้จาก UserProfile (บันทึกถาวรในฐานข้อมูล)"""
    if not user or not getattr(user, 'is_authenticated', False):
        return {'sidebar_open': {}}
    profile = getattr(user, 'profile_record', None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.get_ui_preferences()


def save_user_ui_preferences(user, updates):
    """อัปเดตและบันทึกการตั้งค่าการใช้งานล่าสุดของผู้ใช้ลง UserProfile"""
    if not user or not getattr(user, 'is_authenticated', False):
        return {'sidebar_open': {}}
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile.update_ui_preferences(updates)


# =============================================================================
# 14. ระบบปีการศึกษา, การคัดลอกขึ้นปีใหม่, รีเซ็ตเกม, การตรวจสอบ/อัปโหลด Google Drive และเมนูไฟล์ข้อมูล
# =============================================================================
def get_current_academic_year():
    """คืนค่าปีการศึกษาปัจจุบันจาก SystemSetting"""
    return (SystemSetting.get().current_academic_year or '2569').strip() or '2569'


def ensure_academic_year_exists(year=None, make_current=False, created_by=None):
    """รับรองว่ามีข้อมูล AcademicYear สำหรับปีการศึกษานี้ในฐานข้อมูล"""
    yr = (year or get_current_academic_year()).strip() or '2569'
    ay, created = AcademicYear.objects.get_or_create(
        year=yr,
        defaults={
            'title': f'ปีการศึกษา {yr}',
            'is_current': bool(make_current or (yr == get_current_academic_year())),
            'created_by': created_by,
        },
    )
    if make_current:
        AcademicYear.objects.exclude(pk=ay.pk).update(is_current=False)
        if not ay.is_current:
            ay.is_current = True
            ay.save(update_fields=['is_current'])
    return ay


def get_active_students_qs(academic_year=None, **extra_filters):
    """คืน QuerySet นักเรียนที่กำลังใช้งานในปีการศึกษาที่ระบุ (ค่าเริ่มต้นคือปีการศึกษาปัจจุบัน)"""
    yr = (academic_year or get_current_academic_year()).strip()
    return User.objects.filter(
        role=User.ROLE_STUDENT,
        academic_year=yr,
        is_active=True,
        **extra_filters,
    )


def generate_unique_student_username(student_id, academic_year):
    """สร้าง username ที่ไม่ซ้ำกัน แม้เลขประจำตัวนักเรียนจะซ้ำกับปีการศึกษาที่ผ่านมา"""
    sid = re.sub(r'[^0-9A-Za-z_]', '', str(student_id or '').strip())
    yr = re.sub(r'[^0-9A-Za-z_]', '', str(academic_year or '').strip())
    base = f's{sid}' if not User.objects.filter(username=f's{sid}').exists() else f's{sid}_{yr}'
    cand = base[:100]
    idx = 2
    while User.objects.filter(username=cand).exists():
        suffix = f'_{idx}'
        cand = f'{base[:100 - len(suffix)]}{suffix}'
        idx += 1
    return cand


def _clone_field_file(field_file):
    """คัดลอกไฟล์แนบจาก model เดิมเป็น ContentFile อิสระ (ไม่ผูกพาธเดียวกัน)"""
    if not field_file or not getattr(field_file, 'name', ''):
        return None, None
    try:
        field_file.open('rb')
        data = field_file.read()
        field_file.close()
        return os.path.basename(field_file.name) or 'copied_file', ContentFile(data)
    except Exception:
        return None, None


def reset_all_games_for_academic_year(target_year, deactivate_previous=True, created_by=None):
    """
    รีเซ็ตเกมทั้งหมด (DSBasicSorting, Bingo, ID LOTTO) สำหรับปีการศึกษาใหม่:
    - ปิด/ยุติเกมจากปีก่อนหน้า (คงสถิติประวัติปีเดิมไว้ในฐานข้อมูล)
    - ล้างรอบเกม/สถิติค้างของปีเป้าหมาย และสร้างกิจกรรมฝึกเรียงลำดับเริ่มต้นที่สะอาดให้ทันที
    """
    yr = (target_year or get_current_academic_year()).strip()
    if not yr:
        raise ValueError('กรุณาระบุปีการศึกษา')

    with transaction.atomic():
        if deactivate_previous:
            SortingActivity.objects.exclude(academic_year=yr).update(is_active=False, is_exam_mode=False)
            BingoRoom.objects.exclude(academic_year=yr).update(
                status=BingoRoom.STATUS_CLOSED,
                active_picker_student=None,
                active_picker_room_code='',
                active_picker_accepted=False,
            )
            LottoSession.objects.exclude(academic_year=yr).update(is_open=False)

        SortingActivity.objects.filter(academic_year=yr).delete()
        BingoRoom.objects.filter(academic_year=yr).delete()
        LottoSession.objects.filter(academic_year=yr).delete()

        SortingActivity.objects.create(
            title=f'DSBasicSorting — กิจกรรมฝึกเรียงลำดับข้อมูล (ปีการศึกษา {yr})',
            academic_year=yr,
            dataset='12, 5, 9, 1, 15, 8, 3',
            unit_count=7,
            mode=SortingActivity.MODE_BUBBLE_ASC,
            time_limit=180,
            is_active=True,
            is_exam_mode=False,
            top_x_reward=5,
            session_no=1,
            broadcast_seq=0,
            created_by=created_by,
        )
        AcademicYear.objects.filter(year=yr).update(games_reset_at=timezone.now())


@transaction.atomic
def create_new_academic_year(new_year, source_year='', title='', created_by=None):
    """
    สร้างปีการศึกษาใหม่อย่างเป็นทางการ:
    - ห้ามซ้ำกับปีที่สร้างไว้แล้ว
    - พักบัญชีนักเรียนปีเก่า (คงประวัติคะแนน/ผลงานเก่าไว้ทั้งหมด เพื่อรองรับอัปโหลดรายชื่อใหม่ที่รหัสอาจซ้ำกับปีเก่า)
    - เตรียมช่วงคะแนนใหม่ที่ยังไม่สรุปล็อก และรีเซ็ตเกมทั้งหมด
    """
    yr = (new_year or '').strip()[:20]
    if not yr:
        raise ValueError('กรุณาระบุปีการศึกษาใหม่ (เช่น 2570)')
    if AcademicYear.objects.filter(year=yr).exists():
        raise ValueError(f'ปีการศึกษา {yr} ถูกสร้างไว้ในระบบแล้ว กรุณาสลับไปใช้งานหรือระบุปีการศึกษาอื่น')

    prev_yr = (source_year or get_current_academic_year()).strip()[:20]
    if prev_yr and prev_yr != yr:
        ensure_academic_year_exists(prev_yr)

    # พักสถานะ active ของนักเรียนปีก่อนหน้า เพื่อให้ปีใหม่เริ่มจากรายชื่อที่อัปโหลดเข้ามาใหม่
    User.objects.filter(role=User.ROLE_STUDENT).exclude(academic_year=yr).update(
        is_active=False, current_session_key='', current_device_info=''
    )
    UserSession.objects.filter(user__role=User.ROLE_STUDENT, is_active=True).exclude(
        user__academic_year=yr
    ).update(is_active=False)

    AcademicYear.objects.update(is_current=False)
    ay = AcademicYear.objects.create(
        year=yr,
        title=(title or f'ปีการศึกษา {yr}').strip()[:120],
        is_current=True,
        copied_from_year=prev_yr if prev_yr != yr else '',
        games_reset_at=timezone.now(),
        created_by=created_by,
    )

    setting = SystemSetting.get()
    setting.current_academic_year = yr
    setting.save(update_fields=['current_academic_year', 'updated_at'])

    # เตรียมช่วงคะแนนใหม่ของปีนี้ (อ้างอิงโครงสร้างชื่อและคะแนนเต็มจากปีที่ผ่านมา หรือสร้าง 2 ช่วงมาตรฐาน)
    src_periods = list(ScorePeriod.objects.filter(academic_year=prev_yr).order_by('order_no', 'id')) if prev_yr else []
    if src_periods:
        for sp in src_periods:
            ScorePeriod.objects.create(
                name=sp.name,
                academic_year=yr,
                order_no=sp.order_no,
                max_score=sp.max_score,
                score_calc_mode=sp.score_calc_mode,
                note=sp.note,
                is_finalized=False,
            )
    else:
        ScorePeriod.objects.create(
            name='ช่วงคะแนนที่ 1 (ก่อนกลางภาค)',
            academic_year=yr,
            order_no=1,
            max_score=20.0,
            is_finalized=False,
        )
        ScorePeriod.objects.create(
            name='ช่วงคะแนนที่ 2 (หลังกลางภาค)',
            academic_year=yr,
            order_no=2,
            max_score=20.0,
            is_finalized=False,
        )

    reset_all_games_for_academic_year(yr, deactivate_previous=True, created_by=created_by)
    return ay


@transaction.atomic
def copy_lessons_and_activities_to_academic_year(
    source_year,
    target_year,
    lesson_ids=None,
    activity_ids=None,
    created_by=None,
):
    """คัดลอกบทเรียนและกิจกรรมจากปีการศึกษาที่ผ่านมาเข้าสู่ปีการศึกษาเป้าหมาย (ไม่คัดลอกผลงาน/คะแนนนักเรียน)"""
    src_yr = (source_year or '').strip()
    tgt_yr = (target_year or '').strip()
    if not src_yr or not tgt_yr:
        raise ValueError('กรุณาระบุปีการศึกษาต้นทางและปลายทางให้ครบถ้วน')
    if src_yr == tgt_yr:
        raise ValueError('ปีการศึกษาต้นทางและปลายทางต้องเป็นคนละปีกัน')
    if not AcademicYear.objects.filter(year=tgt_yr).exists():
        raise ValueError(f'ไม่พบปีการศึกษา {tgt_yr} กรุณากดสร้างปีการศึกษาใหม่ก่อน')

    lessons_copied = 0
    lessons_skipped = 0
    activities_copied = 0
    activities_skipped = 0

    if lesson_ids is None:
        lessons_qs = LessonContent.objects.filter(academic_year=src_yr).order_by('unit_number', 'id')
    else:
        clean_lids = [int(x) for x in lesson_ids if str(x).isdigit()]
        lessons_qs = (
            LessonContent.objects.filter(academic_year=src_yr, pk__in=clean_lids).order_by('unit_number', 'id')
            if clean_lids else LessonContent.objects.none()
        )

    for orig in lessons_qs:
        if LessonContent.objects.filter(academic_year=tgt_yr, copied_from=orig).exists():
            lessons_skipped += 1
            continue
        new_lesson = LessonContent(
            title=orig.title,
            academic_year=tgt_yr,
            unit_number=orig.unit_number,
            summary=orig.summary,
            content_html=orig.content_html,
            video_url=orig.video_url,
            is_published=orig.is_published,
            copied_from=orig,
            created_by=created_by or orig.created_by,
        )
        att_name, att_cf = _clone_field_file(orig.attachment)
        if att_name and att_cf:
            new_lesson.attachment.save(att_name, att_cf, save=False)
        new_lesson.save()
        lessons_copied += 1

    target_periods = list(ScorePeriod.objects.filter(academic_year=tgt_yr).order_by('order_no', 'id'))
    period_by_order = {p.order_no: p for p in target_periods}
    period_by_name = {p.name: p for p in target_periods}
    fallback_period = target_periods[0] if target_periods else get_active_score_period(tgt_yr)

    if activity_ids is None:
        acts_qs = LearningActivity.objects.filter(academic_year=src_yr).select_related('score_period').order_by('id')
    else:
        clean_aids = [int(x) for x in activity_ids if str(x).isdigit()]
        acts_qs = (
            LearningActivity.objects.filter(academic_year=src_yr, pk__in=clean_aids)
            .select_related('score_period')
            .order_by('id')
            if clean_aids else LearningActivity.objects.none()
        )

    for orig_act in acts_qs:
        if LearningActivity.objects.filter(academic_year=tgt_yr, copied_from=orig_act).exists():
            activities_skipped += 1
            continue
        mapped_period = fallback_period
        if orig_act.score_period:
            mapped_period = (
                period_by_order.get(orig_act.score_period.order_no)
                or period_by_name.get(orig_act.score_period.name)
                or fallback_period
            )
        new_act = LearningActivity(
            title=orig_act.title,
            description=orig_act.description,
            content_html=orig_act.content_html,
            pdf_display_mode=orig_act.pdf_display_mode,
            activity_type=orig_act.activity_type,
            academic_year=tgt_yr,
            allowed_file_extensions=orig_act.allowed_file_extensions,
            storage_destination=orig_act.storage_destination,
            gdrive_folder_url=orig_act.gdrive_folder_url,
            gdrive_folder_id=orig_act.gdrive_folder_id,
            gdrive_verified=orig_act.gdrive_verified,
            gdrive_verified_at=orig_act.gdrive_verified_at,
            gdrive_verify_message=orig_act.gdrive_verify_message,
            score_period=mapped_period,
            max_score=orig_act.max_score,
            due_date=None,
            is_active=orig_act.is_active,
            is_group_activity=orig_act.is_group_activity,
            group_count=orig_act.group_count,
            max_group_size=orig_act.max_group_size,
            group_selection_open=orig_act.group_selection_open,
            target_rooms_json=orig_act.target_rooms_json,
            require_group_complete=False,
            require_material_check=False,
            group_source_activity=None,
            copied_from=orig_act,
            created_by=created_by or orig_act.created_by,
        )
        ws_name, ws_cf = _clone_field_file(orig_act.worksheet_pdf)
        if ws_name and ws_cf:
            new_act.worksheet_pdf.save(ws_name, ws_cf, save=False)
        new_act.save()

        for ans in orig_act.answer_key_items.order_by('order_no', 'id'):
            new_ans = ActivityAnswerKeyItem(
                activity=new_act,
                order_no=ans.order_no,
                title=ans.title,
                content_html=ans.content_html,
                pdf_display_mode=ans.pdf_display_mode,
                is_revealed=False,
                revealed_at=None,
            )
            ans_name, ans_cf = _clone_field_file(ans.pdf_file)
            if ans_name and ans_cf:
                new_ans.pdf_file.save(ans_name, ans_cf, save=False)
            new_ans.save()

        tiers = list(orig_act.grading_tiers.order_by('round_no', 'id'))
        if tiers:
            for t in tiers:
                ActivityGradingTier.objects.create(
                    activity=new_act,
                    round_no=t.round_no,
                    score=t.score,
                    label=t.label,
                )
        else:
            ensure_activity_default_grading_tiers(new_act)

        activities_copied += 1

    AcademicYear.objects.filter(year=tgt_yr).update(
        lessons_copied_count=LessonContent.objects.filter(academic_year=tgt_yr, copied_from__isnull=False).count(),
        activities_copied_count=LearningActivity.objects.filter(academic_year=tgt_yr, copied_from__isnull=False).count(),
    )
    return {
        'lessons_copied': lessons_copied,
        'lessons_skipped': lessons_skipped,
        'activities_copied': activities_copied,
        'activities_skipped': activities_skipped,
    }


@transaction.atomic
def switch_current_academic_year(target_year):
    """สลับปีการศึกษาปัจจุบันไปยังปีที่ถูกสร้างไว้แล้ว พร้อมสลับสถานะ active ของนักเรียนตามปีนั้น"""
    yr = (target_year or '').strip()
    ay = AcademicYear.objects.filter(year=yr).first()
    if not ay:
        raise ValueError(f'ไม่พบปีการศึกษา "{yr}" ในระบบ กรุณากดปุ่ม "ขึ้นปีการศึกษาใหม่" เพื่อสร้างก่อน')

    AcademicYear.objects.exclude(pk=ay.pk).update(is_current=False)
    if not ay.is_current:
        ay.is_current = True
        ay.save(update_fields=['is_current'])

    setting = SystemSetting.get()
    setting.current_academic_year = yr
    setting.save(update_fields=['current_academic_year', 'updated_at'])

    User.objects.filter(role=User.ROLE_STUDENT, academic_year=yr).update(is_active=True)
    User.objects.filter(role=User.ROLE_STUDENT).exclude(academic_year=yr).update(
        is_active=False, current_session_key='', current_device_info=''
    )
    UserSession.objects.filter(user__role=User.ROLE_STUDENT, is_active=True).exclude(
        user__academic_year=yr
    ).update(is_active=False)
    return ay


def format_bytes_human(num_bytes):
    sz = int(num_bytes or 0)
    if sz <= 0:
        return '0 B'
    if sz < 1024:
        return f'{sz} B'
    if sz < 1024 * 1024:
        return f'{sz / 1024:.1f} KB'
    if sz < 1024 * 1024 * 1024:
        return f'{sz / (1024 * 1024):.2f} MB'
    return f'{sz / (1024 * 1024 * 1024):.2f} GB'


def parse_gdrive_folder_id(raw_url_or_id):
    """แยก Google Drive Folder ID จาก URL หรือ Folder ID โดยตรง"""
    s = (raw_url_or_id or '').strip()
    if not s:
        return ''
    m = re.search(r'drive\.google\.com/(?:drive(?:/u/\d+)?/)?folders/([A-Za-z0-9_-]{10,120})', s)
    if m:
        return m.group(1)
    m2 = re.search(r'drive\.google\.com/open\?(?:[^#]*&)?id=([A-Za-z0-9_-]{10,120})', s)
    if m2:
        return m2.group(1)
    if not s.lower().startswith('http') and re.match(r'^[A-Za-z0-9_-]{15,120}$', s):
        return s
    return ''


def _b64url_encode(data_bytes):
    return base64.urlsafe_b64encode(data_bytes).rstrip(b'=').decode('ascii')


def get_gdrive_access_token_from_service_account(sa_json_str, http_opener=None):
    """สร้าง OAuth2 Access Token จาก Google Service Account JSON (ใช้ cryptography RS256 JWT)"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    info = json.loads(sa_json_str)
    client_email = (info.get('client_email') or '').strip()
    private_key_pem = info.get('private_key') or ''
    token_uri = (info.get('token_uri') or 'https://oauth2.googleapis.com/token').strip()
    if not client_email or not private_key_pem:
        raise ValueError('Service Account JSON ไม่สมบูรณ์ (ขาด client_email หรือ private_key)')

    now_ts = int(py_time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    claim = {
        'iss': client_email,
        'scope': 'https://www.googleapis.com/auth/drive',
        'aud': token_uri,
        'exp': now_ts + 3600,
        'iat': now_ts,
    }
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
        + '.'
        + _b64url_encode(json.dumps(claim, separators=(',', ':')).encode('utf-8'))
    ).encode('ascii')

    private_key = serialization.load_pem_private_key(private_key_pem.encode('utf-8'), password=None)
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = signing_input.decode('ascii') + '.' + _b64url_encode(signature)

    form_bytes = urllib.parse.urlencode({
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion,
    }).encode('utf-8')
    req = urllib.request.Request(
        token_uri,
        data=form_bytes,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    opener = http_opener or urllib.request.urlopen
    with opener(req, timeout=12) as resp:
        token_data = json.loads(resp.read().decode('utf-8', errors='ignore'))
    access_token = (token_data.get('access_token') or '').strip()
    if not access_token:
        raise ValueError('ไม่สามารถขอ Access Token จาก Google OAuth ได้')
    return access_token, client_email


def verify_gdrive_folder_access(raw_url_or_id, setting=None, http_opener=None):
    """
    ตรวจสอบว่า Google Drive Folder ที่ระบุสามารถเข้าถึง/ใช้งานได้จริงก่อนอนุมัติ
    - ถ้าตั้งค่า Service Account JSON: เรียก Drive API v3 ตรวจสิทธิ์โฟลเดอร์และ canAddChildren
    - ถ้าใช้ลิงก์โฟลเดอร์แชร์ทั่วไป: ตรวจ HTTP GET ว่าเข้าถึงได้จริง ไม่ติด 404/Login/ขอสิทธิ์
    """
    folder_id = parse_gdrive_folder_id(raw_url_or_id)
    if not folder_id:
        return {
            'ok': False,
            'folder_id': '',
            'folder_url': (raw_url_or_id or '').strip(),
            'folder_name': '',
            'mode': 'invalid',
            'message': 'รูปแบบลิงก์หรือ Google Drive Folder ID ไม่ถูกต้อง (ต้องเป็น https://drive.google.com/drive/folders/... หรือ Folder ID)',
        }

    folder_url = f'https://drive.google.com/drive/folders/{folder_id}'
    if setting is None:
        setting = SystemSetting.get()
    opener = http_opener or urllib.request.urlopen
    sa_json = (getattr(setting, 'gdrive_service_account_json', '') or '').strip()

    try:
        if sa_json:
            access_token, client_email = get_gdrive_access_token_from_service_account(sa_json, http_opener=opener)
            api_url = (
                f'https://www.googleapis.com/drive/v3/files/{folder_id}'
                f'?fields=id,name,mimeType,trashed,capabilities(canAddChildren)&supportsAllDrives=true'
            )
            req = urllib.request.Request(
                api_url,
                headers={'Authorization': f'Bearer {access_token}'},
                method='GET',
            )
            with opener(req, timeout=12) as resp:
                status_code = getattr(resp, 'status', 200)
                meta = json.loads(resp.read().decode('utf-8', errors='ignore'))
            if status_code != 200:
                return {
                    'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
                    'mode': 'service_account',
                    'message': f'Google Drive API ตอบกลับสถานะ {status_code} ไม่สามารถใช้งานโฟลเดอร์นี้ได้',
                }
            if meta.get('mimeType') != 'application/vnd.google-apps.folder':
                return {
                    'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': meta.get('name', ''),
                    'mode': 'service_account',
                    'message': 'ID ที่ระบุไม่ใช่โฟลเดอร์ของ Google Drive',
                }
            if meta.get('trashed'):
                return {
                    'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': meta.get('name', ''),
                    'mode': 'service_account',
                    'message': 'โฟลเดอร์นี้อยู่ในถังขยะ (Trashed) ไม่สามารถใช้งานได้',
                }
            caps = meta.get('capabilities') or {}
            if not caps.get('canAddChildren'):
                return {
                    'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': meta.get('name', ''),
                    'mode': 'service_account',
                    'message': f'บัญชี {client_email} ไม่มีสิทธิ์เพิ่มไฟล์ในโฟลเดอร์ "{meta.get("name", folder_id)}" (ต้องแชร์สิทธิ์ Editor)',
                }
            fname = meta.get('name') or folder_id
            return {
                'ok': True,
                'folder_id': folder_id,
                'folder_url': folder_url,
                'folder_name': fname,
                'mode': 'service_account',
                'message': f'ตรวจสอบผ่าน: โฟลเดอร์ "{fname}" พร้อมอัปโหลดไฟล์อัตโนมัติผ่าน Service Account ({client_email})',
            }

        req = urllib.request.Request(
            folder_url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; COMSC4-DriveChecker/1.0)'},
            method='GET',
        )
        with opener(req, timeout=12) as resp:
            status_code = getattr(resp, 'status', 200)
            final_url = (resp.geturl() or '') if hasattr(resp, 'geturl') else folder_url
            body = resp.read().decode('utf-8', errors='ignore')

        if status_code != 200:
            return {
                'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
                'mode': 'shared_folder',
                'message': f'ไม่สามารถเข้าถึง Google Drive Folder ได้ (HTTP {status_code})',
            }
        if 'accounts.google.com' in final_url.lower() or 'servicelogin' in final_url.lower():
            return {
                'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
                'mode': 'shared_folder',
                'message': 'โฟลเดอร์นี้จำกัดสิทธิ์และบังคับเข้าสู่ระบบ กรุณาตั้งค่าแชร์เป็น "ทุกคนที่มีลิงก์" หรือตั้งค่า Service Account',
            }
        lowered = body.lower()
        for err_marker in ('error 404 (not found)', 'the requested url was not found', 'you need access', 'ขอสิทธิ์เข้าถึง'):
            if err_marker in lowered:
                return {
                    'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
                    'mode': 'shared_folder',
                    'message': 'ไม่พบโฟลเดอร์หรือยังไม่ได้เปิดสิทธิ์การเข้าถึงบน Google Drive',
                }
        m_title = re.search(r'<title>(.*?)</title>', body, re.IGNORECASE | re.DOTALL)
        raw_title = strip_tags(m_title.group(1)).strip() if m_title else ''
        fname = re.sub(r'\s*-\s*Google Drive\s*$', '', raw_title, flags=re.IGNORECASE).strip() or folder_id
        return {
            'ok': True,
            'folder_id': folder_id,
            'folder_url': folder_url,
            'folder_name': fname,
            'mode': 'shared_folder',
            'message': f'ตรวจสอบผ่าน: โฟลเดอร์ Google Drive "{fname}" สามารถเข้าถึงและใช้งานได้ (อนุมัติใช้งานแล้ว)',
        }
    except urllib.error.HTTPError as ex:
        return {
            'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
            'mode': 'service_account' if sa_json else 'shared_folder',
            'message': f'ไม่สามารถเข้าถึง Google Drive Folder ได้ (HTTP {ex.code})',
        }
    except Exception as ex:
        return {
            'ok': False, 'folder_id': folder_id, 'folder_url': folder_url, 'folder_name': '',
            'mode': 'service_account' if sa_json else 'shared_folder',
            'message': f'ตรวจสอบ Google Drive Folder ไม่ผ่าน: {ex}',
        }


def upload_file_to_gdrive(uploaded_file, folder_id, setting=None, http_opener=None):
    """อัปโหลดไฟล์นักเรียนไปยัง Google Drive Folder ที่ผ่านการตรวจสอบแล้ว (และคืน cursor ไฟล์เดิมไว้ให้ Django)"""
    if not uploaded_file or not folder_id:
        return {'ok': False, 'file_id': '', 'file_url': '', 'error': 'ไม่พบไฟล์หรือ Google Drive Folder ID'}

    if setting is None:
        setting = SystemSetting.get()
    sa_json = (getattr(setting, 'gdrive_service_account_json', '') or '').strip()
    opener = http_opener or urllib.request.urlopen

    try:
        uploaded_file.seek(0)
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)
    except Exception:
        file_bytes = b''

    if not sa_json:
        return {
            'ok': True,
            'file_id': '',
            'file_url': f'https://drive.google.com/drive/folders/{folder_id}',
            'mode': 'shared_folder',
        }

    try:
        access_token, _ = get_gdrive_access_token_from_service_account(sa_json, http_opener=opener)
        boundary = f'comsc4boundary{int(py_time.time() * 1000)}'
        fname = os.path.basename(getattr(uploaded_file, 'name', '') or 'submission_file')
        ctype = getattr(uploaded_file, 'content_type', None) or 'application/octet-stream'
        meta_json = json.dumps({'name': fname, 'parents': [folder_id]}, ensure_ascii=False).encode('utf-8')
        body = (
            f'--{boundary}\r\n'
            f'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        ).encode('utf-8') + meta_json + (
            f'\r\n--{boundary}\r\n'
            f'Content-Type: {ctype}\r\n\r\n'
        ).encode('utf-8') + file_bytes + f'\r\n--{boundary}--\r\n'.encode('utf-8')

        upload_url = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id,webViewLink'
        req = urllib.request.Request(
            upload_url,
            data=body,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': f'multipart/related; boundary={boundary}',
            },
            method='POST',
        )
        with opener(req, timeout=25) as resp:
            res_data = json.loads(resp.read().decode('utf-8', errors='ignore'))
        fid = (res_data.get('id') or '').strip()
        furl = (res_data.get('webViewLink') or '').strip() or (
            f'https://drive.google.com/file/d/{fid}/view' if fid else f'https://drive.google.com/drive/folders/{folder_id}'
        )
        return {'ok': True, 'file_id': fid, 'file_url': furl, 'mode': 'service_account'}
    except Exception as ex:
        return {
            'ok': False,
            'file_id': '',
            'file_url': f'https://drive.google.com/drive/folders/{folder_id}',
            'error': str(ex),
        }


def verify_admin_latest_password(admin_user, raw_password):
    """ตรวจสอบรหัสผ่านล่าสุดของบัญชี Admin จากฐานข้อมูลโดยตรง"""
    if not admin_user or not getattr(admin_user, 'pk', None) or not raw_password:
        return False
    fresh = User.objects.filter(pk=admin_user.pk).first()
    if not fresh or not fresh.is_admin_role:
        return False
    return bool(fresh.check_password(raw_password))


def _delete_field_file_safely(instance, field_name):
    f = getattr(instance, field_name, None)
    if not f:
        return 0
    size_bytes = 0
    try:
        file_path = Path(f.path).resolve()
        media_root = Path(settings.MEDIA_ROOT).resolve()
        if str(file_path).startswith(str(media_root)) and file_path.is_file():
            size_bytes = file_path.stat().st_size
            file_path.unlink()
    except Exception:
        pass
    setattr(instance, field_name, None)
    return size_bytes


def build_academic_year_files_summary(selected_year=''):
    """รวบรวมข้อมูลและไฟล์สะสมจากการทำกิจกรรมแยกตามปีการศึกษา"""
    cur_year = get_current_academic_year()
    ensure_academic_year_exists(cur_year)
    years = {cur_year}
    for ay in AcademicYear.objects.values_list('year', flat=True):
        ys = (ay or '').strip()
        if ys:
            years.add(ys)
    for model_cls in (
        LessonContent, AIQuiz, LearningActivity, ActivitySubmission, ScorePeriod, ScoreLog,
        AttendanceSession, StudentMaterialCheck, SortingActivity, BingoRoom, LottoSession,
    ):
        for y in model_cls.objects.values_list('academic_year', flat=True).distinct():
            ys = (y or '').strip()
            if ys:
                years.add(ys)
    for y in User.objects.filter(role=User.ROLE_STUDENT).values_list('academic_year', flat=True).distinct():
        ys = (y or '').strip()
        if ys:
            years.add(ys)
    sorted_years = sorted(years, reverse=True)
    ay_map = {ay.year: ay for ay in AcademicYear.objects.all()}

    year_summaries = []
    for y in sorted_years:
        subs_q = ActivitySubmission.objects.filter(Q(academic_year=y) | Q(activity__academic_year=y)).distinct()
        sub_files_q = subs_q.exclude(work_file='').exclude(work_file__isnull=True)
        acts_q = LearningActivity.objects.filter(academic_year=y)
        act_pdfs_q = acts_q.exclude(worksheet_pdf='').exclude(worksheet_pdf__isnull=True)
        ans_pdfs_q = ActivityAnswerKeyItem.objects.filter(activity__academic_year=y).exclude(pdf_file='').exclude(pdf_file__isnull=True)
        lessons_q = LessonContent.objects.filter(academic_year=y)
        lesson_files_q = lessons_q.exclude(attachment='').exclude(attachment__isnull=True)

        total_bytes = 0
        files_cnt = 0
        for s in sub_files_q:
            files_cnt += 1
            sz = int(s.work_file_size or 0)
            if not sz and s.work_file:
                try:
                    sz = int(s.work_file.size or 0)
                except Exception:
                    sz = 0
            total_bytes += sz
        for a in act_pdfs_q:
            files_cnt += 1
            try:
                total_bytes += int(a.worksheet_pdf.size or 0)
            except Exception:
                pass
        for ans in ans_pdfs_q:
            files_cnt += 1
            try:
                total_bytes += int(ans.pdf_file.size or 0)
            except Exception:
                pass
        for l_obj in lesson_files_q:
            files_cnt += 1
            try:
                total_bytes += int(l_obj.attachment.size or 0)
            except Exception:
                pass

        ay_obj = ay_map.get(y)
        year_summaries.append({
            'year': y,
            'is_current': (y == cur_year),
            'ay_record': ay_obj,
            'students_count': User.objects.filter(role=User.ROLE_STUDENT, academic_year=y).count(),
            'lessons_count': lessons_q.count(),
            'activities_count': acts_q.count(),
            'submissions_count': subs_q.count(),
            'files_count': files_cnt,
            'files_bytes': total_bytes,
            'files_size_display': format_bytes_human(total_bytes),
            'attendance_count': AttendanceSession.objects.filter(academic_year=y).count(),
            'score_logs_count': ScoreLog.objects.filter(academic_year=y).count(),
            'periods_count': ScorePeriod.objects.filter(academic_year=y).count(),
            'sorting_count': SortingActivity.objects.filter(academic_year=y).count(),
            'bingo_count': BingoRoom.objects.filter(academic_year=y).count(),
            'lotto_count': LottoSession.objects.filter(academic_year=y).count(),
        })

    file_rows = []
    tracked_rel_paths = set()
    sub_qs = ActivitySubmission.objects.select_related('activity', 'student').filter(
        Q(gdrive_file_url__gt='') | (~Q(work_file='') & Q(work_file__isnull=False))
    )
    if selected_year:
        sub_qs = sub_qs.filter(Q(academic_year=selected_year) | Q(activity__academic_year=selected_year))
    for s in sub_qs.order_by('-submitted_at'):
        sz = int(s.work_file_size or 0)
        if s.work_file:
            tracked_rel_paths.add(s.work_file.name.replace('\\', '/'))
            if not sz:
                try:
                    sz = int(s.work_file.size or 0)
                except Exception:
                    sz = 0
        file_rows.append({
            'target': f'sub:{s.id}',
            'academic_year': s.academic_year or s.activity.academic_year,
            'category': 'ไฟล์ส่งงานนักเรียน',
            'activity_title': s.activity.title,
            'owner_label': f'{s.student.room_display} เลขที่ {s.student.class_number} {s.student.get_full_name()}',
            'file_name': s.effective_file_label or 'ไฟล์ผลงาน',
            'file_url': s.effective_file_url,
            'gdrive_url': s.gdrive_file_url,
            'size_bytes': sz,
            'size_display': format_bytes_human(sz) if sz else '-',
            'created_at': s.submitted_at,
        })

    act_qs = LearningActivity.objects.exclude(worksheet_pdf='').exclude(worksheet_pdf__isnull=True)
    if selected_year:
        act_qs = act_qs.filter(academic_year=selected_year)
    for a in act_qs.order_by('-created_at'):
        tracked_rel_paths.add(a.worksheet_pdf.name.replace('\\', '/'))
        try:
            sz = int(a.worksheet_pdf.size or 0)
        except Exception:
            sz = 0
        file_rows.append({
            'target': f'pdf:{a.id}',
            'academic_year': a.academic_year,
            'category': 'PDF ใบงานครู',
            'activity_title': a.title,
            'owner_label': 'ครูผู้สอน',
            'file_name': os.path.basename(a.worksheet_pdf.name),
            'file_url': a.worksheet_pdf.url,
            'gdrive_url': '',
            'size_bytes': sz,
            'size_display': format_bytes_human(sz),
            'created_at': a.created_at,
        })

    ans_qs = ActivityAnswerKeyItem.objects.select_related('activity').exclude(pdf_file='').exclude(pdf_file__isnull=True)
    if selected_year:
        ans_qs = ans_qs.filter(activity__academic_year=selected_year)
    for ans in ans_qs.order_by('-id'):
        tracked_rel_paths.add(ans.pdf_file.name.replace('\\', '/'))
        try:
            sz = int(ans.pdf_file.size or 0)
        except Exception:
            sz = 0
        file_rows.append({
            'target': f'ans:{ans.id}',
            'academic_year': ans.activity.academic_year,
            'category': 'PDF เฉลยใบงาน',
            'activity_title': f'{ans.activity.title} ({ans.title})',
            'owner_label': 'ครูผู้สอน',
            'file_name': os.path.basename(ans.pdf_file.name),
            'file_url': ans.pdf_file.url,
            'gdrive_url': '',
            'size_bytes': sz,
            'size_display': format_bytes_human(sz),
            'created_at': ans.activity.created_at,
        })

    les_qs = LessonContent.objects.exclude(attachment='').exclude(attachment__isnull=True)
    if selected_year:
        les_qs = les_qs.filter(academic_year=selected_year)
    for l_obj in les_qs.order_by('-created_at'):
        tracked_rel_paths.add(l_obj.attachment.name.replace('\\', '/'))
        try:
            sz = int(l_obj.attachment.size or 0)
        except Exception:
            sz = 0
        file_rows.append({
            'target': f'lesson:{l_obj.id}',
            'academic_year': l_obj.academic_year,
            'category': 'ไฟล์สื่อบทเรียน',
            'activity_title': f'หน่วยที่ {l_obj.unit_number}: {l_obj.title}',
            'owner_label': 'ครูผู้สอน',
            'file_name': os.path.basename(l_obj.attachment.name),
            'file_url': l_obj.attachment.url,
            'gdrive_url': '',
            'size_bytes': sz,
            'size_display': format_bytes_human(sz),
            'created_at': l_obj.created_at,
        })

    # สแกนไฟล์ตกค้างใน media/activities/ เมื่อแสดงทั้งหมด
    if not selected_year:
        all_sub_paths = {p.replace('\\', '/') for p in ActivitySubmission.objects.exclude(work_file='').values_list('work_file', flat=True)}
        all_pdf_paths = {p.replace('\\', '/') for p in LearningActivity.objects.exclude(worksheet_pdf='').values_list('worksheet_pdf', flat=True)}
        all_ans_paths = {p.replace('\\', '/') for p in ActivityAnswerKeyItem.objects.exclude(pdf_file='').values_list('pdf_file', flat=True)}
        all_les_paths = {p.replace('\\', '/') for p in LessonContent.objects.exclude(attachment='').values_list('attachment', flat=True)}
        known = all_sub_paths | all_pdf_paths | all_ans_paths | all_les_paths
        act_dir = Path(settings.MEDIA_ROOT) / 'activities'
        if act_dir.exists():
            media_root = Path(settings.MEDIA_ROOT).resolve()
            for fpath in act_dir.rglob('*'):
                if not fpath.is_file():
                    continue
                rel = fpath.resolve().relative_to(media_root).as_posix()
                if rel not in known and not rel.startswith('activities/fruits/'):
                    sz = fpath.stat().st_size
                    file_rows.append({
                        'target': f'disk:{rel}',
                        'academic_year': 'ค้างบนดิสก์',
                        'category': 'ไฟล์สื่อ/รูปภาพกิจกรรม',
                        'activity_title': '-',
                        'owner_label': 'ระบบ / Editor',
                        'file_name': rel,
                        'file_url': f'{settings.MEDIA_URL}{rel}',
                        'gdrive_url': '',
                        'size_bytes': sz,
                        'size_display': format_bytes_human(sz),
                        'created_at': timezone.now(),
                    })

    return {
        'current_academic_year': cur_year,
        'years': sorted_years,
        'year_summaries': year_summaries,
        'file_rows': file_rows,
        'total_listed_bytes': sum(int(r['size_bytes'] or 0) for r in file_rows),
        'total_listed_display': format_bytes_human(sum(int(r['size_bytes'] or 0) for r in file_rows)),
    }


def delete_activity_files_for_year(academic_year='', file_target=None):
    """ลบไฟล์กิจกรรมสะสมตามปีการศึกษา หรือเฉพาะไฟล์เป้าหมายที่ระบุ"""
    deleted_count = 0
    freed_bytes = 0

    if file_target:
        if file_target.startswith('sub:'):
            sid = file_target.split(':', 1)[1]
            sub = ActivitySubmission.objects.filter(pk=sid).first() if sid.isdigit() else None
            if sub:
                freed_bytes += _delete_field_file_safely(sub, 'work_file')
                sub.work_file_name = ''
                sub.work_file_size = 0
                sub.gdrive_file_id = ''
                sub.gdrive_file_url = ''
                sub.save(update_fields=['work_file', 'work_file_name', 'work_file_size', 'gdrive_file_id', 'gdrive_file_url'])
                deleted_count += 1
        elif file_target.startswith('pdf:'):
            aid = file_target.split(':', 1)[1]
            act = LearningActivity.objects.filter(pk=aid).first() if aid.isdigit() else None
            if act:
                freed_bytes += _delete_field_file_safely(act, 'worksheet_pdf')
                act.save(update_fields=['worksheet_pdf'])
                deleted_count += 1
        elif file_target.startswith('ans:'):
            ans_id = file_target.split(':', 1)[1]
            ans = ActivityAnswerKeyItem.objects.filter(pk=ans_id).first() if ans_id.isdigit() else None
            if ans:
                freed_bytes += _delete_field_file_safely(ans, 'pdf_file')
                ans.save(update_fields=['pdf_file'])
                deleted_count += 1
        elif file_target.startswith('lesson:'):
            lid = file_target.split(':', 1)[1]
            l_obj = LessonContent.objects.filter(pk=lid).first() if lid.isdigit() else None
            if l_obj:
                freed_bytes += _delete_field_file_safely(l_obj, 'attachment')
                l_obj.save(update_fields=['attachment'])
                deleted_count += 1
        elif file_target.startswith('disk:'):
            rel = file_target.split(':', 1)[1].lstrip('/').replace('\\', '/')
            act_root = (Path(settings.MEDIA_ROOT) / 'activities').resolve()
            candidate = (Path(settings.MEDIA_ROOT) / rel).resolve()
            if str(candidate).startswith(str(act_root)) and candidate.is_file():
                freed_bytes += candidate.stat().st_size
                candidate.unlink()
                deleted_count += 1
        return deleted_count, freed_bytes

    yr = (academic_year or '').strip()
    if not yr:
        return 0, 0

    subs_qs = ActivitySubmission.objects.filter(Q(academic_year=yr) | Q(activity__academic_year=yr)).distinct()
    for sub in subs_qs:
        had_any = bool(sub.work_file or sub.gdrive_file_url)
        freed_bytes += _delete_field_file_safely(sub, 'work_file')
        if had_any:
            sub.work_file_name = ''
            sub.work_file_size = 0
            sub.gdrive_file_id = ''
            sub.gdrive_file_url = ''
            sub.save(update_fields=['work_file', 'work_file_name', 'work_file_size', 'gdrive_file_id', 'gdrive_file_url'])
            deleted_count += 1

    for act in LearningActivity.objects.filter(academic_year=yr).exclude(worksheet_pdf='').exclude(worksheet_pdf__isnull=True):
        freed_bytes += _delete_field_file_safely(act, 'worksheet_pdf')
        act.save(update_fields=['worksheet_pdf'])
        deleted_count += 1

    for ans in ActivityAnswerKeyItem.objects.filter(activity__academic_year=yr).exclude(pdf_file='').exclude(pdf_file__isnull=True):
        freed_bytes += _delete_field_file_safely(ans, 'pdf_file')
        ans.save(update_fields=['pdf_file'])
        deleted_count += 1

    for l_obj in LessonContent.objects.filter(academic_year=yr).exclude(attachment='').exclude(attachment__isnull=True):
        freed_bytes += _delete_field_file_safely(l_obj, 'attachment')
        l_obj.save(update_fields=['attachment'])
        deleted_count += 1

    return deleted_count, freed_bytes


def delete_all_data_for_academic_year(academic_year):
    """ลบข้อมูลกิจกรรม บทเรียน ไฟล์สะสม เช็คชื่อ คะแนน และเกมทั้งหมดของปีการศึกษาที่ระบุ (และลบนักเรียนปีเก่าที่ไม่ใช่ปีปัจจุบัน)"""
    yr = (academic_year or '').strip()
    if not yr:
        return {'deleted_files': 0, 'freed_bytes': 0, 'deleted_records': 0}

    cur_yr = get_current_academic_year()
    deleted_files, freed_bytes = delete_activity_files_for_year(yr)
    deleted_records = 0
    with transaction.atomic():
        for qs in (
            ScoreLog.objects.filter(Q(academic_year=yr) | Q(score_period__academic_year=yr)),
            ActivityPresentationDraw.objects.filter(activity__academic_year=yr),
            ActivityBulkGradeBatch.objects.filter(activity__academic_year=yr),
            ActivitySubmission.objects.filter(Q(academic_year=yr) | Q(activity__academic_year=yr)),
            ActivityGroup.objects.filter(activity__academic_year=yr),
            ActivityExtensionRequest.objects.filter(activity__academic_year=yr),
            ActivityAnswerKeyItem.objects.filter(activity__academic_year=yr),
            ActivityGradingTier.objects.filter(activity__academic_year=yr),
            LearningActivity.objects.filter(academic_year=yr),
            AIQuiz.objects.filter(Q(academic_year=yr) | Q(lesson__academic_year=yr)),
            LessonContent.objects.filter(academic_year=yr),
            StudentMaterialCheck.objects.filter(Q(academic_year=yr) | Q(session__academic_year=yr)),
            AttendanceSession.objects.filter(academic_year=yr),
            ScorePeriod.objects.filter(academic_year=yr),
            SortingActivity.objects.filter(academic_year=yr),
            BingoRoom.objects.filter(academic_year=yr),
            LottoSession.objects.filter(academic_year=yr),
        ):
            cnt, _ = qs.delete()
            deleted_records += int(cnt or 0)

        if yr != cur_yr:
            cnt_u, _ = User.objects.filter(role=User.ROLE_STUDENT, academic_year=yr).delete()
            deleted_records += int(cnt_u or 0)
            AcademicYear.objects.filter(year=yr, is_current=False).delete()

        for st in User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr, is_active=True):
            st.recalculate_total_score()

    return {
        'deleted_files': deleted_files,
        'freed_bytes': freed_bytes,
        'freed_display': format_bytes_human(freed_bytes),
        'deleted_records': deleted_records,
    }
