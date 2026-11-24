"""
Views & Real-time JSON APIs ของระบบ COMSC4
"""
import csv
import json
import random
import re
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.http import require_POST

from .excel_import import (
    STUDENT_COLUMNS,
    STUDENT_COLUMNS_TH,
    build_workbook,
    import_students_file,
    import_students_from_preview,
    preview_students,
    students_export_workbook,
    students_template_workbook,
    workbook_response,
)
from .middleware import get_client_ip, get_device_summary
from .models import (
    AcademicYear,
    AIQuiz, AIQuizAttempt, AIQuizQuestion,
    ActivityAnswerKeyItem, ActivityBulkGradeBatch, ActivityExtensionRequest,
    ActivityGradingTier, ActivityGroup, ActivityGroupMember,
    ActivityPresentationDraw, ActivitySubmission,
    AttendanceRecord, AttendanceSession,
    BingoCard, BingoDrawnNumber, BingoRoom, BingoWinner,
    Classroom, LabRoomLayout, LearningActivity, LessonContent,
    LottoResult, LottoSession,
    ScoreLog, ScorePeriod, ScoreRule,
    SortingActivity, SortingExamRecord, SortingScore,
    StudentMaterialCheck, ScoreRankTier, SystemSetting, User, UserProfile, UserSession,
)
from .services import (
    DEFAULT_SCORE_RANK_TIERS,
    VALID_AUTO_SEAT_DIRECTIONS,
    approve_extension_request,
    assign_student_to_activity_group,
    auto_close_expired_attendance_sessions,
    award_points_by_rule,
    build_academic_year_files_summary,
    build_auto_seat_numbers,
    build_default_grading_tiers,
    build_room_period_stats_tabs,
    build_sorting_steps,
    bulk_grade_activity_submissions,
    bulk_grade_filtered_ungraded_submissions,
    calculate_sorting_score,
    check_bingo_win_patterns,
    compute_bingo_number_hits,
    compute_group_max_size,
    compute_period_structure,
    compute_student_period_scores,
    compute_student_rank_overview,
    copy_classroom_layout,
    copy_lessons_and_activities_to_academic_year,
    create_new_academic_year,
    delete_activity_files_for_year,
    delete_all_data_for_academic_year,
    delete_sorting_activity_and_recalculate,
    draw_next_bingo_number,
    ensure_academic_year_exists,
    ensure_activity_default_grading_tiers,
    ensure_activity_room_groups,
    ensure_default_lab_rooms,
    ensure_default_score_rank_tiers,
    evaluate_student_submission_with_ai,
    filter_sorting_qs_by_room,
    finalize_lotto_session_scores,
    finalize_score_period,
    finalize_sorting_exam_record,
    generate_ai_quiz_questions,
    generate_student_bingo_grid,
    generate_unique_student_username,
    get_active_score_period,
    get_active_students_qs,
    get_bingo_room_label,
    get_current_academic_year,
    get_default_activity_max_score,
    get_effective_drawn_list_and_set,
    get_effective_drawn_qs,
    get_group_source_activities_for_room,
    get_latest_attendance_room_code,
    get_lotto_eligible_students_qs,
    get_next_activity_bulk_grade_info,
    get_registered_student_ids,
    get_room_group_activity_and_members_map,
    get_sorting_room_filter_label,
    get_student_activity_access,
    get_user_ui_preferences,
    get_valid_student_bingo_picks,
    normalize_bingo_room_filter,
    normalize_sorting_room_filter,
    parse_gdrive_folder_id,
    process_cropped_or_rotated_avatar,
    recalculate_period_students_scores,
    reject_extension_request,
    remember_activity_max_score,
    reset_all_games_for_academic_year,
    resolve_score_rank,
    reward_sorting_exam_top_x,
    room_matches_sorting_filter,
    save_user_ui_preferences,
    seed_initial_comsc4_data,
    seed_sample_activities_room01,
    serialize_activity_groups_for_room,
    switch_current_academic_year,
    toggle_student_material_check,
    upload_file_to_gdrive,
    validate_student_bingo_pick,
    verify_admin_latest_password,
    verify_gdrive_folder_access,
)


def teacher_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_teacher_role:
            messages.error(request, 'หน้านี้สำหรับผู้ดูแล/ครูผู้สอนหรือ Admin เท่านั้น')
            return redirect('core:home')
        return view_func(request, *args, **kwargs)
    return _wrapped


def admin_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_admin_role:
            messages.error(request, 'หน้านี้สำหรับ Admin ผู้ดูแลระบบสูงสุดเท่านั้น')
            return redirect('core:home')
        return view_func(request, *args, **kwargs)
    return _wrapped


def _finalize_user_login(request, user, force_takeover=False):
    """
    ตรวจสอบเงื่อนไข 1 Session ต่อบัญชี (A.1 / A.2):
    - หากผู้ใช้มี Session ที่ยัง Active อยู่ในระบบจากอุปกรณ์อื่น และยังไม่ได้กดยืนยัน force_takeover
      จะเก็บ pending_login_user_id ใน session แล้วพาไปหน้าเลือกอุปกรณ์ (Session Conflict Choice)
    """
    setting = SystemSetting.get()
    enforce = (
        (user.is_student_role and setting.enforce_single_session_student)
        or (user.is_teacher_role and setting.enforce_single_session_staff)
    )

    if not request.session.session_key:
        request.session.create()
    cur_key = request.session.session_key

    if enforce and not force_takeover and user.current_session_key and user.current_session_key != cur_key:
        active_old = UserSession.objects.filter(
            user=user, session_token=user.current_session_key, is_active=True
        ).first()
        if active_old:
            request.session['pending_login_user_id'] = user.pk
            request.session['pending_old_device'] = active_old.device_info or user.current_device_info or 'อุปกรณ์อื่น'
            request.session['pending_old_time'] = active_old.last_active.strftime('%d/%m/%Y %H:%M:%S')
            return redirect('core:session_conflict')

    # ปิด Session เดิมทั้งหมดแล้วเข้าสู่ระบบบนอุปกรณ์นี้
    UserSession.objects.filter(user=user, is_active=True).update(is_active=False)
    login(request, user)
    if not request.session.session_key:
        request.session.save()
    new_key = request.session.session_key
    device_str = get_device_summary(request)
    ip_str = get_client_ip(request)

    user.current_session_key = new_key
    user.current_device_info = device_str
    user.save(update_fields=['current_session_key', 'current_device_info'])

    UserSession.objects.create(
        user=user,
        session_token=new_key,
        device_info=device_str,
        ip_address=ip_str,
        is_active=True,
    )
    request.session.pop('pending_login_user_id', None)
    return redirect('core:home')


# =============================================================================
# ระบบล็อกอิน (Google OAuth @debsirin.ac.th + Username/Password + 1-Session Choice)
# =============================================================================
def home_redirect_view(request):
    if User.objects.count() == 0:
        seed_initial_comsc4_data()
    if not request.user.is_authenticated:
        return redirect('core:login')
    if request.user.is_teacher_role:
        return redirect('core:teacher_dashboard')
    return redirect('core:student_attendance')


def login_view(request):
    if User.objects.count() == 0:
        seed_initial_comsc4_data()
    if request.user.is_authenticated:
        return redirect('core:home')

    setting = SystemSetting.get()
    cur_yr = get_current_academic_year()
    sample_students = get_active_students_qs(cur_yr).order_by('room_code', 'class_number')[:8]

    if request.method == 'POST':
        login_mode = request.POST.get('login_mode', 'staff')
        if login_mode == 'staff':
            username = (request.POST.get('username') or '').strip()
            password = (request.POST.get('password') or '').strip()
            user = authenticate(request, username=username, password=password)
            if not user or not user.is_active:
                user = None
                by_staff = User.objects.filter(
                    role__in=[User.ROLE_ADMIN, User.ROLE_TEACHER], is_active=True
                ).filter(Q(username__iexact=username) | Q(email__iexact=username)).first()
                if by_staff and by_staff.check_password(password):
                    user = by_staff
                else:
                    clean_sid = username[1:] if username.lower().startswith('s') and username[1:].isdigit() else username
                    by_stu = get_active_students_qs(cur_yr).filter(
                        Q(username__iexact=username) | Q(email__iexact=username) | Q(student_id=clean_sid)
                    ).first()
                    if by_stu and by_stu.check_password(password):
                        user = by_stu
            if user and user.is_active:
                if user.is_teacher_role or user.allow_password_login:
                    return _finalize_user_login(request, user)
                messages.error(
                    request,
                    'บัญชีนักเรียนทั่วไปกรุณาเข้าสู่ระบบผ่านปุ่ม Google OAuth (@debsirin.ac.th) ทางด้านซ้าย '
                    '(เฉพาะผู้ดูแลหรือบัญชีกรณีพิเศษเท่านั้นที่ล็อกอินด้วยรหัสผ่านได้)'
                )
            else:
                messages.error(request, 'ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง')

    return render(request, 'auth/login.html', {
        'site_setting': setting,
        'sample_students': sample_students,
    })


@require_POST
def google_oauth_login_view(request):
    """
    A.1 & A.2 ระบบล็อกอินด้วย Google OAuth ผ่านอีเมลโรงเรียน @debsirin.ac.th
    - ตรวจสอบว่าโดเมนตรงกับ allowed_oauth_domain (เช่น debsirin.ac.th)
    - กรณีนักเรียน: แยกเลขประจำตัวนักเรียน 5 หลักจากรูปแบบ s[เลขประจำตัว 5 หลัก]@debsirin.ac.th
      เพื่อตรวจสอบกับฐานข้อมูลในปีการศึกษาปัจจุบัน หากตรงให้เข้าสู่ระบบในฐานะนักเรียน
    - กรณีครู/Admin ที่ถูกเพิ่มด้วยอีเมลโรงเรียน @debsirin.ac.th: สามารถล็อกอินผ่าน Google OAuth ได้ทันที
    """
    setting = SystemSetting.get()
    cur_yr = get_current_academic_year()
    allowed_domain = (setting.allowed_oauth_domain or 'debsirin.ac.th').strip().lower().lstrip('@')
    email = (request.POST.get('google_email') or '').strip().lower()

    if not email or '@' not in email:
        messages.error(request, 'กรุณาระบุอีเมลบัญชี Google ของโรงเรียน')
        return redirect('core:login')

    local_part, domain_part = email.split('@', 1)
    if domain_part != allowed_domain:
        messages.error(
            request,
            f'ปฏิเสธการเข้าสู่ระบบ: ต้องใช้อีเมลโรงเรียนโดเมน @{allowed_domain} เท่านั้น (ที่คุณระบุคือ @{domain_part})'
        )
        return redirect('core:login')

    # 1. ตรวจสอบว่าเป็นครู/ผู้ดูแลหรือ Admin ที่ลงทะเบียนด้วยอีเมลโรงเรียนหรือไม่ (A.2)
    staff_user = User.objects.filter(
        email__iexact=email,
        role__in=[User.ROLE_ADMIN, User.ROLE_TEACHER],
        allow_google_oauth=True,
        is_active=True,
    ).first()
    if staff_user:
        messages.success(request, f'เข้าสู่ระบบด้วย Google OAuth สำเร็จ ({staff_user.get_full_name()})')
        return _finalize_user_login(request, staff_user)

    # 2. ตรวจสอบรูปแบบอีเมลนักเรียน s[เลขประจำตัว 5 หลัก]@debsirin.ac.th ในปีการศึกษาปัจจุบัน (A.1)
    m = re.match(r'^s(\d{5})$', local_part, re.IGNORECASE)
    if not m:
        messages.error(
            request,
            f'รูปแบบอีเมลนักเรียนไม่ถูกต้อง ต้องเป็น s[เลขประจำตัวนักเรียน 5 หลัก]@{allowed_domain} '
            f'เช่น s12345@{allowed_domain}'
        )
        return redirect('core:login')

    extracted_student_id = m.group(1)
    student = User.objects.filter(
        student_id=extracted_student_id,
        role=User.ROLE_STUDENT,
        academic_year=cur_yr,
        is_active=True,
    ).first()

    if not student:
        messages.error(
            request,
            f'ไม่พบเลขประจำตัวนักเรียน {extracted_student_id} (จากอีเมล {email}) ในฐานข้อมูลปีการศึกษา {cur_yr}'
        )
        return redirect('core:login')

    if student.email.lower() != email:
        student.email = email
        student.save(update_fields=['email'])

    messages.success(
        request,
        f'ยืนยันตัวตน Google OAuth สำเร็จ: รหัสนักเรียน {student.student_id} ({student.get_full_name()})'
    )
    return _finalize_user_login(request, student)


def session_conflict_view(request):
    """
    A.1 หน้าจอให้ผู้ใช้งานเลือกว่าจะ:
    1) ใช้งานต่อที่อุปกรณ์ใหม่ (เตะอุปกรณ์เดิมออกทันที)
    2) คงอุปกรณ์เดิมไว้ (ยกเลิกการล็อกอินที่อุปกรณ์ใหม่นี้)
    """
    pending_uid = request.session.get('pending_login_user_id')
    if not pending_uid:
        return redirect('core:login')

    user = User.objects.filter(pk=pending_uid, is_active=True).first()
    if not user:
        request.session.pop('pending_login_user_id', None)
        return redirect('core:login')

    if request.method == 'POST':
        choice = request.POST.get('choice')
        if choice == 'new_device':
            messages.info(request, 'สลับมาใช้งานบนอุปกรณ์ใหม่เรียบร้อยแล้ว (อุปกรณ์เดิมถูกตัดออกจากระบบ)')
            return _finalize_user_login(request, user, force_takeover=True)
        else:
            request.session.pop('pending_login_user_id', None)
            messages.info(request, 'ยกเลิกการเข้าสู่ระบบบนอุปกรณ์นี้ เพื่อคงการใช้งานที่อุปกรณ์เดิม')
            return redirect('core:login')

    return render(request, 'auth/session_conflict.html', {
        'conflict_user': user,
        'old_device': request.session.get('pending_old_device', 'อุปกรณ์อื่น'),
        'old_time': request.session.get('pending_old_time', '-'),
        'new_device': get_device_summary(request),
    })


def logout_view(request):
    if request.user.is_authenticated:
        cur_key = request.session.session_key
        if cur_key:
            UserSession.objects.filter(user=request.user, session_token=cur_key).update(is_active=False)
        if request.user.current_session_key == cur_key:
            User.objects.filter(pk=request.user.pk).update(current_session_key='', current_device_info='')
    logout(request)
    return redirect('core:login')


# =============================================================================
# หน้าสำหรับนักเรียน (Student Views)
# =============================================================================
def _resolve_saved_period_id(user, raw_period_param, periods, pref_key, fallback_pref_key=None):
    """คืน period_id ที่เลือกอยู่พร้อมบันทึก/กู้คืนจาก UserProfile.ui_preferences_json"""
    valid_ids = [p.id for p in periods]
    if not valid_ids:
        return 0
    raw = (raw_period_param or '').strip()
    if raw.isdigit() and int(raw) in valid_ids:
        chosen_id = int(raw)
        if getattr(user, 'is_student_role', False):
            save_user_ui_preferences(user, {pref_key: chosen_id})
        return chosen_id
    prefs = get_user_ui_preferences(user) if getattr(user, 'is_student_role', False) else {}
    saved_val = prefs.get(pref_key) or (prefs.get(fallback_pref_key) if fallback_pref_key else None)
    if isinstance(saved_val, int) and saved_val in valid_ids:
        return saved_val
    if isinstance(saved_val, str) and saved_val.isdigit() and int(saved_val) in valid_ids:
        return int(saved_val)
    return valid_ids[0]


@login_required
def student_dashboard_view(request):
    student = request.user
    student.recalculate_total_score()
    cur_yr = (student.academic_year if student.is_student_role else '') or get_current_academic_year()
    recent_scores = student.score_logs.filter(academic_year=cur_yr).select_related('rule', 'score_period')[:8]
    active_sorting = SortingActivity.objects.filter(academic_year=cur_yr, is_active=True)[:3]
    active_bingo = BingoRoom.objects.filter(academic_year=cur_yr, status__in=[BingoRoom.STATUS_OPEN, BingoRoom.STATUS_PLAYING])[:3]
    active_lotto = LottoSession.objects.filter(academic_year=cur_yr, is_open=True)[:2]
    open_attendance = AttendanceSession.objects.filter(academic_year=cur_yr, is_open=True)
    if student.classroom:
        open_attendance = open_attendance.filter(classroom=student.classroom)

    classmates = get_active_students_qs(cur_yr, room_code=student.room_code).order_by('class_number', 'student_id')
    my_lotto_wins = list(
        LottoResult.objects.filter(student=student, session__academic_year=cur_yr, removed=False)
        .select_related('session')
        .order_by('-created_at', '-id')
    )
    my_lotto_count = len(my_lotto_wins)
    my_lotto_points = round(sum(float(w.points_awarded or 0.0) for w in my_lotto_wins if w.is_awarded), 2)
    tiers = ensure_default_score_rank_tiers()
    periods = list(ScorePeriod.objects.filter(academic_year=cur_yr).order_by('order_no', 'id'))
    rank_overview = compute_student_rank_overview(student, periods=periods, tiers=tiers)
    active_period_id = _resolve_saved_period_id(
        student, request.GET.get('period_id'), periods, 'dashboard_period_id', fallback_pref_key='scores_period_id'
    )
    period_tabs = build_room_period_stats_tabs(student, student.room_code or '01', periods=periods, tiers=tiers)

    return render(request, 'student/dashboard.html', {
        'recent_scores': recent_scores,
        'active_sorting': active_sorting,
        'active_bingo': active_bingo,
        'active_lotto': active_lotto,
        'open_attendance': open_attendance,
        'classmates': classmates,
        'my_lotto_wins': my_lotto_wins,
        'my_lotto_count': my_lotto_count,
        'my_lotto_points': my_lotto_points,
        'rank_overview': rank_overview,
        'period_tabs': period_tabs,
        'active_period_id': active_period_id,
    })


@login_required
def student_profile_view(request):
    """A.4 หน้าโปรไฟล์นักเรียน: อัปโหลดรูป หมุนรูป และ Crop รูปสัดส่วน 1:1 + แก้ไขข้อมูลที่อนุญาต"""
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    if request.method == 'POST':
        action = request.POST.get('action', 'update_info')
        if action == 'upload_cropped_avatar':
            cropped_b64 = request.POST.get('cropped_image_data', '')
            uploaded_file = request.FILES.get('avatar_file')
            rotate_deg = int(request.POST.get('rotate_deg') or 0)
            try:
                process_cropped_or_rotated_avatar(
                    user,
                    uploaded_file=uploaded_file,
                    cropped_base64=cropped_b64,
                    rotate_deg=rotate_deg,
                )
                messages.success(request, 'อัปโหลด หมุน และ Crop รูปประจำตัวเรียบร้อยแล้ว')
            except Exception as ex:
                messages.error(request, f'ไม่สามารถบันทึกรูปภาพได้: {ex}')
            return redirect('core:student_profile')

        elif action == 'delete_avatar':
            if user.profile_photo:
                user.profile_photo.delete(save=True)
            profile.profile_image_url = ''
            profile.save()
            messages.info(request, 'ลบรูปประจำตัวเรียบร้อยแล้ว ระบบจะใช้รูปเริ่มต้นตามตัวอักษร')
            return redirect('core:student_profile')

        elif action == 'update_info':
            profile.bio = (request.POST.get('bio') or '').strip()
            profile.phone = (request.POST.get('phone') or '').strip()
            profile.save()
            group_name = (request.POST.get('group_name') or '').strip()
            if group_name:
                user.group_name = group_name
                user.save(update_fields=['group_name'])
            messages.success(request, 'บันทึกข้อมูลโปรไฟล์เรียบร้อยแล้ว')
            return redirect('core:student_profile')

    active_sessions = user.user_sessions.filter(is_active=True)[:5]
    return render(request, 'student/profile.html', {
        'profile': profile,
        'active_sessions': active_sessions,
    })


@login_required
def student_lessons_view(request):
    """เนื้อหาบทเรียนและแบบสอบถาม/แบบทดสอบภายในแต่ละบทเรียน"""
    cur_yr = (request.user.academic_year if request.user.is_student_role else '') or get_current_academic_year()
    lessons = list(LessonContent.objects.filter(academic_year=cur_yr, is_published=True).order_by('unit_number', 'id'))
    raw_lid = (request.GET.get('lesson_id') or '').strip()
    if raw_lid == 'all':
        selected_lesson_id = 0
        if request.user.is_student_role:
            save_user_ui_preferences(request.user, {'lessons_lesson_id': 0})
    elif raw_lid.isdigit():
        selected_lesson_id = int(raw_lid)
        if request.user.is_student_role:
            save_user_ui_preferences(request.user, {'lessons_lesson_id': selected_lesson_id})
    elif request.user.is_student_role:
        saved_lid = get_user_ui_preferences(request.user).get('lessons_lesson_id') or 0
        selected_lesson_id = int(saved_lid) if str(saved_lid).isdigit() else 0
    else:
        selected_lesson_id = 0

    active_quizzes = list(AIQuiz.objects.filter(academic_year=cur_yr, is_active=True).prefetch_related('questions').order_by('id'))
    by_lesson = {}
    orphans = []
    for qz in active_quizzes:
        if qz.lesson_id:
            by_lesson.setdefault(qz.lesson_id, []).append(qz)
        else:
            orphans.append(qz)

    for idx, lesson in enumerate(lessons):
        l_quizzes = list(by_lesson.get(lesson.id, []))
        if idx == 0 and orphans:
            l_quizzes.extend(orphans)
        lesson.active_quizzes = l_quizzes

    if selected_lesson_id:
        filtered = [l for l in lessons if l.id == selected_lesson_id]
        if filtered:
            lessons = filtered + [l for l in lessons if l.id != selected_lesson_id]

    my_attempts = {
        a.quiz_id: a for a in AIQuizAttempt.objects.filter(student=request.user).order_by('created_at')
    }
    return render(request, 'student/lessons.html', {
        'lessons': lessons,
        'selected_lesson_id': selected_lesson_id,
        'my_attempts': my_attempts,
    })


@login_required
def student_take_quiz_view(request, quiz_id):
    quiz = get_object_or_404(AIQuiz, pk=quiz_id, is_active=True)
    questions = list(quiz.questions.all())

    if request.method == 'POST':
        answers = {}
        correct_cnt = 0
        for q in questions:
            chosen = (request.POST.get(f'q_{q.id}') or '').strip().upper()
            answers[str(q.id)] = chosen
            if chosen == q.correct_choice.upper():
                correct_cnt += 1

        max_sc = round(len(questions) * float(quiz.points_per_question), 2)
        earned_sc = round(correct_cnt * float(quiz.points_per_question), 2)
        time_spent = int(request.POST.get('time_spent_seconds') or 0)

        already_attempted = AIQuizAttempt.objects.filter(quiz=quiz, student=request.user).exists()
        attempt = AIQuizAttempt.objects.create(
            quiz=quiz,
            student=request.user,
            score=earned_sc,
            max_score=max_sc,
            correct_count=correct_cnt,
            total_questions=len(questions),
            answers_json=json.dumps(answers),
            time_spent_seconds=time_spent,
        )

        if quiz.sync_to_total_score and not already_attempted and earned_sc > 0:
            ScoreLog.objects.create(
                student=request.user,
                academic_year=get_current_academic_year(),
                points=earned_sc,
                reason=f'ทำแบบทดสอบ AI "{quiz.title}" ได้ {correct_cnt}/{len(questions)} ข้อ',
                source_module='quiz',
            )
            messages.success(request, f'ส่งแบบทดสอบสำเร็จ! ได้ {earned_sc}/{max_sc} คะแนน (บวกเข้าคะแนนรวมแล้ว)')
        else:
            messages.success(request, f'ส่งแบบทดสอบสำเร็จ! ได้ {earned_sc}/{max_sc} คะแนน')

        return render(request, 'student/quiz_result.html', {
            'quiz': quiz,
            'questions': questions,
            'attempt': attempt,
            'answers': answers,
        })

    return render(request, 'student/take_quiz.html', {
        'quiz': quiz,
        'questions': questions,
    })


@login_required
@require_POST
def api_activity_group_move(request, activity_id):
    """AJAX: นักเรียนลากกล่องชื่อตัวเองเข้ากลุ่ม หรือย้ายกลุ่ม (เรียงตามเลขที่)"""
    act = get_object_or_404(LearningActivity, pk=activity_id)
    target_gid = (request.POST.get('group_id') or '').strip()
    try:
        groups = assign_student_to_activity_group(act, request.user, target_group_id=target_gid)
        my_g = next((g for g in groups if g['is_mine']), None)
        return JsonResponse({
            'ok': True,
            'groups': groups,
            'my_group_id': my_g['id'] if my_g else None,
            'my_group_name': my_g['name'] if my_g else '',
        })
    except ValueError as ex:
        return JsonResponse({'ok': False, 'error': str(ex)}, status=400)


@login_required
def student_activities_view(request):
    """กิจกรรมของนักเรียน (เฉพาะผลงานตนเอง + แจ้งเตือนงานค้าง + ลากกล่องจับกลุ่ม + เฉพาะห้องที่กำหนด)"""
    cur_yr = (request.user.academic_year if request.user.is_student_role else '') or get_current_academic_year()
    user_room = (request.user.room_code or '').strip()
    activities = [
        act for act in LearningActivity.objects.filter(academic_year=cur_yr).select_related('score_period').order_by('id')
        if act.applies_to_room(user_room)
    ]
    if request.method == 'POST':
        action = (request.POST.get('action') or 'submit_work').strip()
        act_id = int(request.POST.get('activity_id') or 0)
        act = get_object_or_404(LearningActivity, pk=act_id)
        if not act.applies_to_room(user_room):
            messages.error(request, f'กิจกรรม "{act.title}" ไม่ได้กำหนดสำหรับห้องเรียนของคุณ')
            return redirect('core:student_activities')

        if action == 'move_group':
            try:
                assign_student_to_activity_group(act, request.user, request.POST.get('group_id'))
                messages.success(request, f'จัดกลุ่มสำหรับกิจกรรม "{act.title}" เรียบร้อยแล้ว')
            except ValueError as ex:
                messages.error(request, str(ex))
            return redirect('core:student_activities')

        if action == 'request_extension':
            reason = (request.POST.get('reason') or '').strip() or 'ขออนุญาตส่งงานล่าช้าเพิ่มเติม'
            pending_req = ActivityExtensionRequest.objects.filter(
                activity=act, student=request.user, status=ActivityExtensionRequest.STATUS_PENDING
            ).first()
            if pending_req:
                pending_req.reason = reason
                pending_req.save(update_fields=['reason'])
            else:
                ActivityExtensionRequest.objects.create(
                    activity=act,
                    student=request.user,
                    reason=reason,
                    status=ActivityExtensionRequest.STATUS_PENDING,
                )
            messages.success(request, f'ส่งคำร้องขอส่งงานล่าช้าสำหรับ "{act.title}" เรียบร้อยแล้ว กรุณารอครูผู้สอนอนุมัติ')
            return redirect('core:student_activities')

        access = get_student_activity_access(act, request.user)
        if access['is_locked']:
            messages.error(request, f'ไม่สามารถส่งหรือแก้ไขงาน "{act.title}" ได้: {access["lock_reason"]}')
            return redirect('core:student_activities')

        work_title = (request.POST.get('work_title') or '').strip()
        work_text = (request.POST.get('work_text') or '').strip()
        work_file = request.FILES.get('work_file')
        if work_file:
            allowed_exts = act.get_allowed_extensions_list()
            if allowed_exts:
                raw_name = (work_file.name or '').strip()
                file_ext = ('.' + raw_name.rsplit('.', 1)[-1].lower()) if '.' in raw_name else ''
                if file_ext not in allowed_exts:
                    messages.error(request, f'ประเภทไฟล์ไม่ถูกต้องสำหรับกิจกรรมนี้ (อนุญาตเฉพาะ {", ".join(allowed_exts)})')
                    return redirect(f'/student/activities/?activity_id={act.id}')
        if not work_title:
            if work_text:
                work_title = work_text[:80]
            elif work_file:
                work_title = f'ผลงานแนบไฟล์: {work_file.name[:60]}'
            else:
                work_title = act.title[:80]

        sub_year = act.academic_year or get_current_academic_year()
        file_orig_name = (work_file.name or '')[:255] if work_file else ''
        file_orig_size = int(getattr(work_file, 'size', 0) or 0) if work_file else 0
        gd_file_id = ''
        gd_file_url = ''
        if work_file and act.storage_destination == LearningActivity.STORAGE_GDRIVE and act.gdrive_verified and act.gdrive_folder_id:
            gd_res = upload_file_to_gdrive(work_file, act.gdrive_folder_id)
            gd_file_id = gd_res.get('file_id') or ''
            gd_file_url = gd_res.get('file_url') or f'https://drive.google.com/drive/folders/{act.gdrive_folder_id}'
            if not gd_res.get('ok'):
                messages.warning(request, f'บันทึกไฟล์บนเซิร์ฟเวอร์แล้ว แต่ส่งขึ้น Google Drive ไม่สำเร็จ: {gd_res.get("error", "")}')

        ai_eval = evaluate_student_submission_with_ai(
            act, request.user, work_title=work_title, work_text=work_text,
            has_file=bool(work_file or ActivitySubmission.objects.filter(activity=act, student=request.user).exclude(work_file='').exists()),
        )

        if act.is_group_activity:
            membership = ActivityGroupMember.objects.filter(activity=act, student=request.user).select_related('group').first()
            if not membership:
                messages.error(request, f'กรุณาลากกล่องชื่อของคุณเข้ากลุ่มก่อนส่งงาน "{act.title}"')
                return redirect(f'/student/activities/?activity_id={act.id}')
            member_students = [m.student for m in membership.group.members.select_related('student').all()]
            if request.user not in member_students:
                member_students.append(request.user)
            first_saved_file = None
            for idx_m, m_st in enumerate(member_students):
                sub_m, _ = ActivitySubmission.objects.get_or_create(activity=act, student=m_st)
                sub_m.academic_year = sub_year
                sub_m.group = membership.group
                sub_m.work_title = work_title
                sub_m.work_text = work_text
                sub_m.ai_review_status = ai_eval['status']
                sub_m.ai_review_feedback = ai_eval['ai_feedback']
                sub_m.similarity_warning = ai_eval['similarity_warning']
                sub_m.similarity_score = ai_eval['similarity_score']
                if work_file:
                    sub_m.work_file_name = file_orig_name
                    sub_m.work_file_size = file_orig_size
                    sub_m.gdrive_file_id = gd_file_id
                    sub_m.gdrive_file_url = gd_file_url
                    if idx_m == 0:
                        sub_m.work_file = work_file
                        sub_m.save()
                        first_saved_file = sub_m.work_file.name
                    else:
                        sub_m.work_file = first_saved_file
                        sub_m.save()
                else:
                    sub_m.save()
            messages.success(request, f'ส่งผลงานกลุ่ม ({membership.group.name}) สำหรับ "{act.title}" ให้สมาชิก {len(member_students)} คนเรียบร้อยแล้ว')
            if ai_eval['similarity_warning']:
                messages.warning(request, ai_eval['similarity_warning'])
            elif not ai_eval['is_sufficient'] or not ai_eval['is_relevant']:
                messages.warning(request, f'ข้อแนะนำจาก AI: {ai_eval["ai_feedback"]}')
            return redirect(f'/student/activities/?activity_id={act.id}')

        sub, _ = ActivitySubmission.objects.get_or_create(activity=act, student=request.user)
        sub.academic_year = sub_year
        sub.work_title = work_title
        sub.work_text = work_text
        sub.ai_review_status = ai_eval['status']
        sub.ai_review_feedback = ai_eval['ai_feedback']
        sub.similarity_warning = ai_eval['similarity_warning']
        sub.similarity_score = ai_eval['similarity_score']
        if work_file:
            sub.work_file = work_file
            sub.work_file_name = file_orig_name
            sub.work_file_size = file_orig_size
            sub.gdrive_file_id = gd_file_id
            sub.gdrive_file_url = gd_file_url
        sub.save()
        messages.success(request, f'ส่งผลงานกิจกรรม "{act.title}" เรียบร้อยแล้ว')
        if ai_eval['similarity_warning']:
            messages.warning(request, ai_eval['similarity_warning'])
        elif not ai_eval['is_sufficient'] or not ai_eval['is_relevant']:
            messages.warning(request, f'ข้อแนะนำจาก AI: {ai_eval["ai_feedback"]}')
        return redirect('core:student_activities')

    room_students_qs = User.objects.filter(role=User.ROLE_STUDENT)
    if user_room:
        room_students_qs = room_students_qs.filter(room_code=user_room)
    student_room_total = room_students_qs.count()

    my_subs = {s.activity_id: s for s in ActivitySubmission.objects.filter(student=request.user)}
    pending_activities = []

    for act in activities:
        sub_q = ActivitySubmission.objects.filter(activity=act)
        if user_room:
            sub_q = sub_q.filter(student__room_code=user_room)
        act.room_submitted_count = sub_q.count()
        act.room_total_students = student_room_total
        act.access = get_student_activity_access(act, request.user)
        act.revealed_answers = list(act.answer_key_items.filter(is_revealed=True).order_by('order_no', 'id'))
        act.my_sub = my_subs.get(act.id)
        if act.id not in my_subs:
            pending_activities.append(act)
        if act.is_group_activity:
            g_data = serialize_activity_groups_for_room(act, user_room or '01', current_student=request.user)
            act.groups_data = g_data
            act.groups_json = json.dumps(g_data, ensure_ascii=False).replace('</', '<\\/')
            act.my_group = next((g for g in g_data if g['is_mine']), None)
        else:
            act.groups_data = []
            act.groups_json = '[]'
            act.my_group = None

    filter_act_id = (request.GET.get('activity_id') or '').strip()
    if filter_act_id == 'all':
        selected_filter_activity_id = 0
        if request.user.is_student_role:
            save_user_ui_preferences(request.user, {'activities_activity_id': 0})
    elif filter_act_id.isdigit():
        selected_filter_activity_id = int(filter_act_id)
        if request.user.is_student_role:
            save_user_ui_preferences(request.user, {'activities_activity_id': selected_filter_activity_id})
    elif request.user.is_student_role:
        saved_aid = get_user_ui_preferences(request.user).get('activities_activity_id') or 0
        selected_filter_activity_id = int(saved_aid) if str(saved_aid).isdigit() else 0
    else:
        selected_filter_activity_id = 0
    if selected_filter_activity_id:
        selected_list = [a for a in activities if a.id == selected_filter_activity_id]
        if selected_list:
            activities = selected_list + [a for a in activities if a.id != selected_filter_activity_id]

    return render(request, 'student/activities.html', {
        'activities': activities,
        'my_subs': my_subs,
        'pending_activities': pending_activities,
        'selected_filter_activity_id': selected_filter_activity_id,
        'student_room_total': student_room_total,
        'room_display_label': request.user.room_display if user_room else 'ทุกห้องเรียน',
    })


@login_required
def student_attendance_view(request):
    """เช็คชื่อเข้าเรียน 1 คลิก ("ยืนยันการเข้าเรียน") — ปิดอัตโนมัติใน 25 นาที และไม่แสดงปุ่มหากห้องยังไม่เปิด"""
    student = request.user
    cur_yr = (student.academic_year if student.is_student_role else '') or get_current_academic_year()
    auto_close_expired_attendance_sessions(student.classroom if student.classroom_id else None)

    open_qs = AttendanceSession.objects.filter(academic_year=cur_yr, is_open=True).select_related('classroom').order_by('-opened_at', '-created_at', '-id')
    if student.classroom:
        open_session = open_qs.filter(classroom=student.classroom).first()
        display_session = open_session or AttendanceSession.objects.filter(academic_year=cur_yr, classroom=student.classroom).select_related('classroom').order_by('-opened_at', '-created_at', '-id').first()
    else:
        open_session = open_qs.first()
        display_session = open_session

    can_check_in = bool(open_session and open_session.is_open and not open_session.is_expired)
    current_session = open_session

    client_ip = get_client_ip(request)
    if request.method == 'POST':
        if not can_check_in or not open_session:
            messages.error(request, 'ขณะนี้ห้องเรียนของคุณยังไม่ได้เปิดการเช็คชื่อ หรือหมดเวลาเช็คชื่อแล้ว')
            return redirect('core:student_attendance')

        code = (request.POST.get('check_in_code') or '').strip()
        if code and code.upper() != open_session.check_in_code.upper():
            messages.error(request, 'รหัสเช็คชื่อไม่ถูกต้อง กรุณาตรวจสอบโค้ดจากหน้าจอครูผู้สอน')
            return redirect('core:student_attendance')

        room_obj = open_session.classroom
        disabled_set = room_obj.get_disabled_seats_set()
        taken_set = set(
            open_session.records.exclude(student=student).values_list('seat_row', 'seat_col')
        )

        raw_r = (request.POST.get('seat_row') or '').strip()
        raw_c = (request.POST.get('seat_col') or '').strip()
        if raw_r.isdigit() and raw_c.isdigit():
            seat_row = int(raw_r)
            seat_col = int(raw_c)
        else:
            my_assigned = room_obj.get_seat_for_class_number(student.class_number)
            if my_assigned and f'{my_assigned[0]}_{my_assigned[1]}' not in disabled_set and my_assigned not in taken_set:
                seat_row, seat_col = my_assigned
            else:
                existing_rec = AttendanceRecord.objects.filter(session=open_session, student=student).first()
                if existing_rec:
                    seat_row, seat_col = existing_rec.seat_row, existing_rec.seat_col
                else:
                    picked = None
                    for rr in room_obj.get_rows_sequence():
                        for cc in range(1, room_obj.get_effective_cols() + 1):
                            if f'{rr}_{cc}' not in disabled_set and (rr, cc) not in taken_set:
                                picked = (rr, cc)
                                break
                        if picked:
                            break
                    seat_row, seat_col = picked or (1, 1)

        if f'{seat_row}_{seat_col}' in disabled_set:
            messages.error(request, f'ตำแหน่งแถว {seat_row} คอลัมน์ {seat_col} เป็นทางเดินหรือที่นั่งที่ปิดใช้งาน กรุณาเลือกที่นั่งอื่น')
            return redirect('core:student_attendance')

        # ตรวจ IP หากครูกำหนด Prefix IP ห้องเรียนไว้
        allowed_ip = (open_session.allowed_ip_prefix or SystemSetting.get().attendance_check_ip_prefix or '').strip()
        if allowed_ip:
            prefixes = [p.strip() for p in allowed_ip.split(',') if p.strip()]
            if prefixes and not any(client_ip.startswith(p) for p in prefixes) and client_ip not in ('127.0.0.1', '::1'):
                messages.error(request, f'ไม่อนุญาตให้เช็คชื่อนอกเครือข่ายห้องเรียน (IP ของคุณ: {client_ip})')
                return redirect('core:student_attendance')

        taken = AttendanceRecord.objects.filter(
            session=open_session, seat_row=seat_row, seat_col=seat_col
        ).exclude(student=student).first()
        if taken:
            messages.error(request, f'ที่นั่งแถว {seat_row} คอลัมน์ {seat_col} มีเพื่อนเช็คชื่อนั่งไปแล้ว ({taken.student.get_full_name()})')
            return redirect('core:student_attendance')

        rec, created = AttendanceRecord.objects.update_or_create(
            session=open_session,
            student=student,
            defaults={
                'seat_row': seat_row,
                'seat_col': seat_col,
                'ip_address': client_ip,
                'status': AttendanceRecord.STATUS_PRESENT,
            }
        )
        if created and open_session.attendance_points > 0:
            award_points_by_rule(
                student=student,
                rule_code='attendance_on_time',
                default_name='เข้าเรียนตรงเวลาและเช็คชื่อตามแผนผังที่นั่ง',
                default_points=open_session.attendance_points,
                source_module='attendance',
                custom_reason=f'เช็คชื่อคาบ "{open_session.title}" ที่นั่ง {rec.seat_code} (IP: {client_ip})',
            )
        messages.success(request, f'เช็คชื่อสำเร็จ! ที่นั่งของคุณคือ {rec.seat_code} (IP: {client_ip})')
        return redirect('core:student_attendance')

    seat_map = {}
    my_record = None
    rows_range, cols_range = list(range(6, 0, -1)), list(range(1, 8))
    disabled_seats = set()
    custom_labels = {}
    board_position = 'bottom'
    row_order = 'bottom_to_top'
    seat_numbers = {}
    my_assigned_seat = None
    default_seat_row, default_seat_col = 1, 1
    lab_room = None
    room_obj = display_session.classroom if display_session else student.classroom
    if room_obj:
        lab_room = room_obj.lab_room
        rows_range = room_obj.get_rows_sequence()
        cols_range = list(range(1, room_obj.get_effective_cols() + 1))
        disabled_seats = room_obj.get_disabled_seats_set()
        custom_labels = room_obj.get_custom_labels_dict()
        valid_class_nos = set(
            get_active_students_qs(cur_yr, room_code=room_obj.code, class_number__gt=0)
            .values_list('class_number', flat=True)
        )
        seat_numbers = room_obj.get_seat_numbers_dict(valid_class_numbers=valid_class_nos)
        board_position = room_obj.get_effective_board_position()
        row_order = room_obj.get_row_order()
        my_assigned_seat = room_obj.get_seat_for_class_number(student.class_number)
        if my_assigned_seat:
            default_seat_row, default_seat_col = my_assigned_seat
        if display_session:
            for r in display_session.records.select_related('student'):
                seat_map[f'{r.seat_row}_{r.seat_col}'] = r
                if r.student_id == student.id:
                    my_record = r
                    default_seat_row, default_seat_col = r.seat_row, r.seat_col

    return render(request, 'student/attendance.html', {
        'current_session': display_session,
        'open_session': open_session,
        'can_check_in': can_check_in,
        'remaining_seconds': open_session.remaining_seconds if can_check_in else 0,
        'student_classroom': room_obj,
        'lab_room': lab_room,
        'seat_map': seat_map,
        'seat_numbers': seat_numbers,
        'my_assigned_seat': my_assigned_seat,
        'default_seat_row': default_seat_row,
        'default_seat_col': default_seat_col,
        'my_record': my_record,
        'rows_range': rows_range,
        'cols_range': cols_range,
        'disabled_seats': disabled_seats,
        'custom_labels': custom_labels,
        'board_position': board_position,
        'row_order': row_order,
        'client_ip': client_ip,
    })


@login_required
def student_scores_view(request):
    """หน้าดูถ้วยรางวัล 2 ช่วงคะแนน, เกณฑ์ 7 ระดับ และสถิติแยกตาม 2 ช่วงคะแนน (ไม่เปิดเผยชื่อเพื่อน)"""
    student = request.user
    student.recalculate_total_score()
    cur_yr = (student.academic_year if student.is_student_role else '') or get_current_academic_year()
    my_logs = student.score_logs.filter(academic_year=cur_yr).select_related('rule', 'score_period').all()
    if student.is_student_role:
        room_code = student.room_code or '01'
    else:
        room_code = request.GET.get('room') or student.room_code or '01'
    leaderboard = list(
        get_active_students_qs(cur_yr, room_code=room_code).order_by('class_number', 'student_id')
    )
    classrooms = Classroom.objects.all()

    tiers = ensure_default_score_rank_tiers()
    periods = list(ScorePeriod.objects.filter(academic_year=cur_yr).order_by('order_no', 'id'))
    rank_overview = compute_student_rank_overview(student, periods=periods, tiers=tiers)
    period_summaries = rank_overview['period_entries']
    active_period_id = _resolve_saved_period_id(
        student, request.GET.get('period_id'), periods, 'scores_period_id', fallback_pref_key='dashboard_period_id'
    )
    period_tabs = build_room_period_stats_tabs(student, room_code, periods=periods, tiers=tiers)

    for st in leaderboard:
        if st.id == student.id:
            st.rank_percent = rank_overview['overall_percent']
            st.rank_tier = rank_overview['overall_rank']
        else:
            st.rank_percent = None
            st.rank_tier = None

    room_obj = Classroom.objects.filter(code=room_code).first()
    selected_room_display = room_obj.name if room_obj else (student.room_display or f'4/{room_code}')

    return render(request, 'student/scores.html', {
        'my_logs': my_logs,
        'leaderboard': leaderboard,
        'selected_room': room_code,
        'selected_room_display': selected_room_display,
        'classrooms': classrooms,
        'period_summaries': period_summaries,
        'rank_overview': rank_overview,
        'period_tabs': period_tabs,
        'active_period_id': active_period_id,
    })


@login_required
@require_POST
def api_student_ui_pref(request):
    """บันทึกการตั้งค่าการใช้งานล่าสุดของนักเรียนลง UserProfile (ใช้ร่วมกันทุกครั้งที่ login ใหม่)"""
    payload = {}
    if request.body and (request.content_type or '').startswith('application/json'):
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            payload = {}
    else:
        payload = request.POST.dict()

    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'error': 'ข้อมูลไม่ถูกต้อง'}, status=400)

    updates = {}
    for int_key in ('scores_period_id', 'dashboard_period_id', 'lessons_lesson_id', 'activities_activity_id', 'sorting_activity_id'):
        if int_key in payload and str(payload.get(int_key)).strip() != '':
            raw_v = str(payload.get(int_key)).strip()
            if raw_v.isdigit():
                updates[int_key] = int(raw_v)

    nav_id = str(payload.get('sidebar_nav_id') or '').strip()
    allowed_navs = {'studentMainNav', 'studentLearnNav', 'studentLessonsNav', 'studentActivitiesNav', 'studentGamesNav'}
    if nav_id in allowed_navs and 'sidebar_is_open' in payload:
        raw_open = payload.get('sidebar_is_open')
        if isinstance(raw_open, bool):
            is_open = raw_open
        else:
            is_open = str(raw_open).strip().lower() in ('1', 'true', 'yes', 'on')
        updates['sidebar_open'] = {nav_id: is_open}

    prefs = save_user_ui_preferences(request.user, updates) if updates else get_user_ui_preferences(request.user)
    return JsonResponse({'ok': True, 'preferences': prefs})


# =============================================================================
# (ฝั่งนักเรียน & API) ระบบกิจกรรมเรียงลำดับ (DSBasicSorting แบบ Form1/Form2 เดิม)
# =============================================================================
SORTING_SHORT_MAP = {
    'bubble': 'BUB',
    'selection': 'SEL',
    'insertion': 'INS',
}


def _sorting_short_code(mode_or_sort, order_str=''):
    m = (mode_or_sort or 'bubble').lower()
    if m.startswith('sel'):
        s_code = 'SEL'
    elif m.startswith('ins'):
        s_code = 'INS'
    else:
        s_code = 'BUB'
    if order_str:
        o_code = 'M' if order_str.lower() == 'desc' else 'L'
    else:
        o_code = 'M' if 'desc' in m else 'L'
    return s_code, o_code


def _build_sorting_live_data(activity, room_filter='', session_no=None):
    sess_no = int(session_no or activity.session_no or 1)
    rf = normalize_sorting_room_filter(room_filter)
    qs = SortingScore.objects.filter(activity=activity, session_no=sess_no).select_related('student', 'student__classroom')
    qs = filter_sorting_qs_by_room(qs, rf)

    now = timezone.now()
    per_student = {}
    for sc in qs.order_by('created_at', 'id'):
        sid = sc.student_id
        bucket = per_student.setdefault(sid, {
            'student': sc.student,
            'online': None,
            'playing': None,
            'completed': [],
            'last_touch': sc.updated_at,
        })
        if sc.updated_at and (not bucket['last_touch'] or sc.updated_at > bucket['last_touch']):
            bucket['last_touch'] = sc.updated_at
        if sc.status == 'online':
            bucket['online'] = sc
        elif sc.status == 'playing':
            bucket['playing'] = sc
        elif sc.status == 'completed':
            bucket['completed'].append(sc)

    players = []
    playing_legacy = []
    lb_entries = []

    for sid, b in per_student.items():
        st = b['student']
        comp_list = b['completed']
        latest_comp = max(comp_list, key=lambda x: (x.play_round_no, x.id)) if comp_list else None
        cum_score = max(float(latest_comp.cumulative_score or 0.0), float(latest_comp.score or 0.0)) if latest_comp else 0.0
        play_round_no = latest_comp.play_round_no if latest_comp else 0
        is_recent = bool(b['last_touch'] and (now - b['last_touch']) <= timedelta(seconds=90))

        if b['playing']:
            p = b['playing']
            state = 'playing'
            status_text = f'กำลังทำรอบ {p.current_round}/{p.total_rounds} (ผิด {p.attempts})'
            cur_r, tot_r, att = p.current_round, p.total_rounds, p.attempts
            playing_legacy.append({
                'student_pk': st.pk,
                'student_id': st.student_id or st.username,
                'name': st.get_full_name(),
                'room': st.room_display,
                'room_code': st.room_code or '',
                'number': st.class_number,
                'avatar': st.avatar_url,
                'initial': st.avatar_initial,
                'current_round': cur_r,
                'total_rounds': tot_r,
                'attempts': att,
            })
        elif latest_comp:
            state = 'completed'
            status_text = f'[เสร็จแล้ว รอบที่ {play_round_no} คะแนนสะสม {cum_score:.2f}]'
            cur_r, tot_r, att = latest_comp.right_rounds, latest_comp.total_rounds, latest_comp.attempts
        else:
            o = b['online']
            state = 'online'
            status_text = 'ออนไลน์ (พร้อมเล่น)'
            cur_r, tot_r, att = 0, activity.unit_count, 0

        players.append({
            'student_pk': st.pk,
            'student_id': st.student_id or st.username,
            'name': st.get_full_name(),
            'room': st.room_display,
            'room_code': st.room_code or '',
            'number': st.class_number,
            'avatar': st.avatar_url,
            'initial': st.avatar_initial,
            'state': state,
            'status_text': status_text,
            'current_round': cur_r,
            'total_rounds': tot_r,
            'attempts': att,
            'cumulative_score': round(cum_score, 2),
            'play_round_no': play_round_no,
            'is_recent_online': is_recent,
        })

        if latest_comp:
            any_rewarded = any(c.is_top_rewarded for c in comp_list)
            total_wrong = sum(c.attempts for c in comp_list)
            total_time = round(sum(c.time_used for c in comp_list), 2)
            lb_entries.append({
                'student_pk': st.pk,
                'student_id': st.student_id or st.username,
                'name': st.get_full_name(),
                'room': st.room_display,
                'room_code': st.room_code or '',
                'number': st.class_number,
                'avatar': st.avatar_url,
                'initial': st.avatar_initial,
                'play_round_no': play_round_no,
                'last_score': round(latest_comp.score, 2),
                'cumulative_score': round(cum_score, 2),
                'score': round(cum_score, 2),
                'time_used': round(latest_comp.time_used, 2),
                'total_time': total_time,
                'attempts': latest_comp.attempts,
                'total_attempts': total_wrong,
                'right_rounds': latest_comp.right_rounds,
                'algorithm_detail': latest_comp.algorithm_detail or '-',
                'is_exam_mode': latest_comp.is_exam_mode,
                'is_top_rewarded': any_rewarded,
            })

    players.sort(key=lambda x: (
        int(x['room_code']) if str(x['room_code']).isdigit() else 99,
        int(x['number'] or 0),
        str(x['student_id']),
    ))
    lb_entries.sort(key=lambda x: (
        -float(x['cumulative_score']),
        -int(x['play_round_no']),
        int(x['room_code']) if str(x['room_code']).isdigit() else 99,
        int(x['number'] or 0),
    ))
    for idx, item in enumerate(lb_entries, start=1):
        item['rank'] = idx

    return {
        'session_no': sess_no,
        'room_filter': rf,
        'room_filter_label': get_sorting_room_filter_label(rf),
        'broadcast_seq': activity.broadcast_seq,
        'broadcast_sort': activity.broadcast_sort,
        'broadcast_order': activity.broadcast_order,
        'broadcast_unit': activity.broadcast_unit,
        'broadcast_room_code': activity.broadcast_room_code,
        'is_exam_mode': activity.is_exam_mode,
        'top_x_reward': activity.top_x_reward,
        'players': players,
        'leaderboard': lb_entries,
        'playing': playing_legacy,
        'completed': lb_entries,
        'online_count': len(players),
        'playing_count': len(playing_legacy),
        'completed_count': len(lb_entries),
    }


@login_required
def student_sorting_view(request):
    setting = SystemSetting.get()
    if not setting.sorting_menu_open and request.user.is_student_role:
        messages.warning(request, 'ขณะนี้ผู้ดูแลปิดเมนูกิจกรรมเรียงลำดับชั่วคราว')
        return redirect('core:student_dashboard')

    cur_yr = (request.user.academic_year if request.user.is_student_role else '') or get_current_academic_year()
    activities = SortingActivity.objects.filter(academic_year=cur_yr, is_active=True)
    act_id = (request.GET.get('activity_id') or '').strip()
    if act_id.isdigit():
        current_activity = activities.filter(pk=int(act_id)).first()
        if request.user.is_student_role and current_activity:
            save_user_ui_preferences(request.user, {'sorting_activity_id': current_activity.id})
    elif request.user.is_student_role:
        saved_sid = get_user_ui_preferences(request.user).get('sorting_activity_id')
        current_activity = activities.filter(pk=saved_sid).first() if saved_sid else None
        if current_activity is None:
            current_activity = activities.first()
    else:
        current_activity = activities.first()

    initial_nums = []
    expected_steps = []
    my_session_rounds = []
    my_cumulative_score = 0.0
    my_next_round_no = 1
    broadcast_state = {}

    student_in_exam = False
    if current_activity:
        initial_nums = current_activity.get_numbers_list()
        expected_steps = build_sorting_steps(initial_nums, current_activity.mode)
        st_grade = request.user.classroom.grade_level if request.user.classroom_id else ''
        student_in_exam = bool(
            current_activity.is_exam_mode
            and room_matches_sorting_filter(request.user.room_code, st_grade, current_activity.broadcast_room_code)
        )
        my_session_rounds = list(
            SortingScore.objects.filter(
                activity=current_activity,
                student=request.user,
                session_no=current_activity.session_no,
                status='completed',
            ).order_by('play_round_no', 'id')
        )
        if my_session_rounds:
            last_r = my_session_rounds[-1]
            my_cumulative_score = round(max(float(last_r.cumulative_score or 0.0), float(last_r.score or 0.0)), 2)
            my_next_round_no = len(my_session_rounds) + 1
        broadcast_state = {
            'session_no': current_activity.session_no,
            'broadcast_seq': current_activity.broadcast_seq,
            'broadcast_sort': current_activity.broadcast_sort,
            'broadcast_order': current_activity.broadcast_order,
            'broadcast_unit': current_activity.broadcast_unit,
            'broadcast_room_code': current_activity.broadcast_room_code,
            'is_exam_mode': current_activity.is_exam_mode,
            'student_in_exam': student_in_exam,
        }

    my_history = SortingScore.objects.filter(
        student=request.user, status='completed'
    ).select_related('activity').order_by('-created_at')[:15]

    top_scores = []
    if current_activity:
        qs_top = SortingScore.objects.filter(
            activity=current_activity, status='completed'
        ).select_related('student')
        if request.user.is_student_role:
            qs_top = qs_top.filter(student=request.user)
        top_scores = qs_top.order_by('-cumulative_score', '-score', 'time_used')[:6 if request.user.is_student_role else 10]

    return render(request, 'games/student_sorting.html', {
        'activities': activities,
        'current_activity': current_activity,
        'student_in_exam': student_in_exam,
        'initial_nums_json': json.dumps(initial_nums),
        'expected_steps_json': json.dumps(expected_steps),
        'my_session_rounds': my_session_rounds,
        'my_cumulative_score': my_cumulative_score,
        'my_next_round_no': my_next_round_no,
        'broadcast_state_json': json.dumps(broadcast_state),
        'my_history': my_history,
        'top_scores': top_scores,
    })


@login_required
@require_POST
def api_sorting_heartbeat(request, activity_id):
    """แจ้งสถานะออนไลน์ของนักเรียนในกิจกรรม Sorting และรับคำสั่ง PLAY จากครูแบบ Real-time"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    if request.user.is_student_role:
        sess_no = activity.session_no
        playing = SortingScore.objects.filter(
            activity=activity, student=request.user, session_no=sess_no, status='playing'
        ).first()
        if playing:
            playing.save(update_fields=['updated_at'])
        else:
            online_obj = SortingScore.objects.filter(
                activity=activity, student=request.user, session_no=sess_no, status='online'
            ).first()
            if online_obj:
                online_obj.save(update_fields=['updated_at'])
            else:
                SortingScore.objects.create(
                    activity=activity,
                    student=request.user,
                    session_no=sess_no,
                    status='online',
                    total_rounds=activity.unit_count,
                    current_round=0,
                    is_exam_mode=activity.is_exam_mode,
                )

    st_grade = request.user.classroom.grade_level if request.user.classroom_id else ''
    student_in_exam = bool(
        activity.is_exam_mode
        and room_matches_sorting_filter(request.user.room_code, st_grade, activity.broadcast_room_code)
    )

    return JsonResponse({
        'ok': True,
        'session_no': activity.session_no,
        'broadcast_seq': activity.broadcast_seq,
        'broadcast_sort': activity.broadcast_sort,
        'broadcast_order': activity.broadcast_order,
        'broadcast_unit': activity.broadcast_unit,
        'broadcast_room_code': activity.broadcast_room_code,
        'is_exam_mode': activity.is_exam_mode,
        'student_in_exam': student_in_exam,
        'student_room_code': request.user.room_code or '',
        'student_grade_level': st_grade,
    })


@login_required
@require_POST
def api_sorting_progress(request, activity_id):
    """รับอัปเดตสถานะความคืบหน้าสดขณะนักเรียนกำลังเรียงลำดับแต่ละรอบ (เหมือน PROGRESS ใน Form1.cs)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    cur_round = int(request.POST.get('current_round') or 1)
    total_rounds = int(request.POST.get('total_rounds') or activity.unit_count)
    wrong_attempts = int(request.POST.get('attempts') or 0)
    time_used = float(request.POST.get('time_used') or 0)
    sess_no = int(request.POST.get('session_no') or activity.session_no or 1)
    algo_detail = (request.POST.get('algorithm_detail') or '').strip()

    SortingScore.objects.filter(
        activity=activity, student=request.user, session_no=sess_no, status='online'
    ).delete()

    prog = SortingScore.objects.filter(
        activity=activity, student=request.user, session_no=sess_no, status='playing'
    ).first()
    if not prog:
        prog = SortingScore.objects.create(
            activity=activity,
            student=request.user,
            session_no=sess_no,
            status='playing',
            current_round=cur_round,
            total_rounds=total_rounds,
            attempts=wrong_attempts,
            time_used=time_used,
            algorithm_detail=algo_detail,
            is_exam_mode=activity.is_exam_mode,
        )
    else:
        prog.current_round = cur_round
        prog.total_rounds = total_rounds
        prog.attempts = wrong_attempts
        prog.time_used = time_used
        if algo_detail:
            prog.algorithm_detail = algo_detail
        prog.save(update_fields=['current_round', 'total_rounds', 'attempts', 'time_used', 'algorithm_detail', 'updated_at'])

    return JsonResponse({'ok': True, 'session_no': sess_no})


@login_required
@require_POST
def api_sorting_submit(request, activity_id):
    """บันทึกคะแนนเมื่อนักเรียนเรียงลำดับจบรอบ (รองรับคะแนนสะสมและประวัติทีละรอบเหมือน Form1.cs)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id, is_active=True)
    right_rounds = int(request.POST.get('right_rounds') or activity.unit_count)
    wrong_attempts = int(request.POST.get('attempts') or 0)
    time_used = float(request.POST.get('time_used') or 0.0)
    unit_count = max(4, min(10, int(request.POST.get('unit_count') or activity.unit_count)))
    sess_no = int(request.POST.get('session_no') or activity.session_no or 1)
    sort_type = (request.POST.get('sort_type') or '').strip()
    sort_order = (request.POST.get('sort_order') or '').strip()
    passed_count = int(request.POST.get('passed_count') or unit_count)

    final_score = calculate_sorting_score(right_rounds, wrong_attempts, time_used, unit_count)
    prev_qs = SortingScore.objects.filter(
        activity=activity, student=request.user, session_no=sess_no, status='completed'
    )
    play_round_no = prev_qs.count() + 1
    prev_total = prev_qs.aggregate(s=Sum('score'))['s'] or 0.0
    cumulative_score = round(float(prev_total) + float(final_score), 2)

    s_code, o_code = _sorting_short_code(sort_type or activity.mode, sort_order)
    algo_detail = (request.POST.get('algorithm_detail') or '').strip()
    if not algo_detail:
        algo_detail = (
            f'{play_round_no} {s_code} {unit_count} ({passed_count}) {o_code} : '
            f'ถูก {right_rounds} ผิด {wrong_attempts} เวลา {int(round(time_used))} = {final_score:g} คะแนน'
        )

    SortingScore.objects.filter(
        activity=activity, student=request.user, session_no=sess_no, status__in=['playing', 'online']
    ).delete()
    sc = SortingScore.objects.create(
        activity=activity,
        student=request.user,
        session_no=sess_no,
        play_round_no=play_round_no,
        score=final_score,
        cumulative_score=cumulative_score,
        time_used=round(time_used, 2),
        attempts=wrong_attempts,
        right_rounds=right_rounds,
        current_round=right_rounds,
        total_rounds=unit_count,
        status='completed',
        algorithm_detail=algo_detail[:100],
        is_exam_mode=activity.is_exam_mode,
    )
    return JsonResponse({
        'ok': True,
        'score': sc.score,
        'cumulative_score': sc.cumulative_score,
        'play_round_no': sc.play_round_no,
        'time_used': sc.time_used,
        'attempts': sc.attempts,
        'right_rounds': sc.right_rounds,
        'algorithm_detail': sc.algorithm_detail,
        'is_exam_mode': sc.is_exam_mode,
        'session_no': sc.session_no,
    })


@teacher_required
@require_POST
def api_sorting_broadcast_play(request, activity_id):
    """คำสั่ง PLAY จากผู้ดูแล -> เปิดรอบสอบใหม่ทันทีตามห้อง/ระดับชั้นที่เลือก และสร้างบันทึกสถิติรอบสอบ"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    sort_type = (request.POST.get('sort_type') or 'bubble').strip().lower()
    if sort_type not in ('bubble', 'selection', 'insertion'):
        sort_type = 'bubble'
    sort_order = (request.POST.get('sort_order') or 'asc').strip().lower()
    if sort_order not in ('asc', 'desc'):
        sort_order = 'asc'
    unit_val = int(request.POST.get('unit_count') or 7)
    if unit_val < 4 or unit_val > 10:
        unit_val = 7
    room_code = normalize_sorting_room_filter(request.POST.get('room_code'))

    # ปิดบันทึกรอบสอบที่กำลังค้างอยู่เดิมก่อนเริ่มรอบใหม่
    if activity.exam_records.filter(status=SortingExamRecord.STATUS_ACTIVE).exists():
        finalize_sorting_exam_record(activity, room_filter=activity.broadcast_room_code or '', teacher_user=request.user, status=SortingExamRecord.STATUS_FINISHED)

    activity.broadcast_sort = sort_type
    activity.broadcast_order = sort_order
    activity.broadcast_unit = unit_val
    activity.broadcast_room_code = room_code
    activity.broadcast_seq = int(activity.broadcast_seq or 0) + 1
    activity.session_no = int(activity.session_no or 1) + 1
    activity.mode = f'{sort_type}_{sort_order}'
    if unit_val >= 5:
        activity.unit_count = unit_val
    activity.is_exam_mode = True
    activity.exam_closed_rewarded = False
    activity.save()

    sort_label = {
        'bubble': 'Bubble Sort (การเรียงแบบฟองสบู่)',
        'selection': 'Selection Sort (การเรียงแบบเลือก)',
        'insertion': 'Insertion Sort (การเรียงแบบแทรก)',
    }[sort_type]
    order_label = 'น้อย >> มาก' if sort_order == 'asc' else 'มาก >> น้อย'
    unit_label = 'สุ่ม (5-9 ตัว)' if unit_val == 4 else f'{unit_val} ตัว'
    room_label = get_sorting_room_filter_label(room_code)

    exam_rec = SortingExamRecord.objects.create(
        activity=activity,
        session_no=activity.session_no,
        title=f'รอบสอบ #{activity.session_no}: {sort_label} ({order_label})',
        room_filter=room_code,
        room_label=room_label,
        sort_type=sort_type,
        sort_order=sort_order,
        unit_count=unit_val,
        status=SortingExamRecord.STATUS_ACTIVE,
        created_by=request.user,
    )

    return JsonResponse({
        'ok': True,
        'broadcast_seq': activity.broadcast_seq,
        'session_no': activity.session_no,
        'exam_record_id': exam_rec.id,
        'is_exam_mode': activity.is_exam_mode,
        'summary_text': f'ส่งคำสั่ง PLAY: {sort_label} ({order_label}, {unit_label}) ไปยัง {room_label} สำเร็จ!',
    })


@login_required
def api_sorting_live_board(request, activity_id):
    """คืนค่าข้อมูลผู้เล่นทุกคนและตารางอันดับคะแนนสะสม Real-time สำหรับหน้าจอควบคุมครู (Form2.cs)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    room_filter = request.GET.get('room', '')
    session_no = request.GET.get('session_no')
    data = _build_sorting_live_data(activity, room_filter=room_filter, session_no=session_no)
    data['ok'] = True
    return JsonResponse(data)


@teacher_required
def api_sorting_student_history(request, activity_id, student_id):
    """คืนประวัติการเรียงทุกรอบของนักเรียนที่เลือกในเซสชันปัจจุบัน (listBox3 ใน Form2.cs)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    student = get_object_or_404(User, pk=student_id)
    sess_no = int(request.GET.get('session_no') or activity.session_no or 1)
    rounds = list(
        SortingScore.objects.filter(
            activity=activity, student=student, session_no=sess_no, status='completed'
        ).order_by('play_round_no', 'id')
    )
    cum = round(max([max(float(r.cumulative_score or 0.0), float(r.score or 0.0)) for r in rounds], default=0.0), 2)
    return JsonResponse({
        'ok': True,
        'session_no': sess_no,
        'student': {
            'id': student.pk,
            'student_id': student.student_id or student.username,
            'name': student.get_full_name(),
            'room': student.room_display,
            'number': student.class_number,
            'avatar': student.avatar_url,
            'initial': student.avatar_initial,
        },
        'cumulative_score': cum,
        'rounds': [
            {
                'play_round_no': r.play_round_no,
                'detail': r.algorithm_detail or f'รอบที่ {r.play_round_no}',
                'score': round(r.score, 2),
                'cumulative_score': round(max(float(r.cumulative_score or 0.0), float(r.score or 0.0)), 2),
                'time_used': r.time_used,
                'attempts': r.attempts,
                'right_rounds': r.right_rounds,
                'created_at': r.created_at.strftime('%H:%M:%S'),
            }
            for r in rounds
        ],
    })


@teacher_required
def teacher_sorting_export_csv_view(request, activity_id):
    """Export ผลคะแนนกิจกรรมเรียงลำดับเป็น CSV (UTF-8 BOM เหมือนปุ่ม Export ใน Form2.cs)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    room_filter = normalize_sorting_room_filter(request.GET.get('room', ''))
    data = _build_sorting_live_data(activity, room_filter=room_filter)
    room_tag = room_filter.replace(':', '_') if room_filter else 'all'

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="DSBasicSorting_Act{activity.id}_Session{data["session_no"]}_{room_tag}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['อันดับ', 'ห้อง', 'เลขที่', 'รหัสประจำตัว', 'ชื่อ-นามสกุล', 'จำนวนรอบที่เล่น', 'รายละเอียดรอบล่าสุด', 'จำนวนที่ผิดล่าสุด', 'เวลาล่าสุด(วินาที)', 'คะแนนสะสมรวม'])
    for item in data['leaderboard']:
        writer.writerow([
            item['rank'],
            item['room'],
            item['number'],
            item['student_id'],
            item['name'],
            item['play_round_no'],
            item['algorithm_detail'],
            item['attempts'],
            item['time_used'],
            item['cumulative_score'],
        ])
    return response


# =============================================================================
# (ฝั่งนักเรียน) หน้าเกม Bingo
# =============================================================================
@login_required
def student_bingo_list_view(request):
    setting = SystemSetting.get()
    if not setting.bingo_menu_open and request.user.is_student_role:
        messages.warning(request, 'ขณะนี้ผู้ดูแลปิดเมนูเกม Bingo ชั่วคราว')
        return redirect('core:student_dashboard')

    cur_yr = (request.user.academic_year if request.user.is_student_role else '') or get_current_academic_year()
    rooms = BingoRoom.objects.filter(academic_year=cur_yr).exclude(status=BingoRoom.STATUS_CLOSED).order_by('-created_at')
    return render(request, 'games/student_bingo_list.html', {'rooms': rooms})


@login_required
def student_bingo_play_view(request, room_id):
    room = get_object_or_404(BingoRoom, pk=room_id)
    card = BingoCard.objects.filter(room=room, student=request.user).first()
    mn, mx = room.get_min_max_range()
    corners, centers, special_indices = room.get_corner_and_center_indices()

    my_room_code = normalize_bingo_room_filter(request.user.room_code or '01')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action in ('auto_create_card', 'manual_create_card'):
            if get_effective_drawn_qs(room, my_room_code).exists() and card:
                messages.warning(request, 'เกมเริ่มออกเลขไปแล้ว ไม่สามารถเปลี่ยนตาราง Bingo ได้')
                return redirect('core:student_bingo_play', room_id=room.id)
            try:
                if action == 'manual_create_card':
                    raw_vals = request.POST.getlist('cell_num')
                    custom_nums = [int(v) for v in raw_vals if str(v).strip()]
                    grid = generate_student_bingo_grid(room, custom_numbers=custom_nums)
                else:
                    grid = generate_student_bingo_grid(room)

                BingoCard.objects.update_or_create(
                    room=room,
                    student=request.user,
                    defaults={'card_data': json.dumps(grid), 'is_locked': True}
                )
                messages.success(request, f'บันทึกตาราง Bingo ขนาด {room.size}×{room.size} เรียบร้อยแล้ว!')
            except Exception as ex:
                messages.error(request, f'ไม่สามารถสร้างตารางได้: {ex}')
            return redirect('core:student_bingo_play', room_id=room.id)

    grid_numbers = card.get_grid_list() if card else [None] * (room.size * room.size)
    pool_numbers = list(range(mn, mx + 1))
    drawn_list, _ = get_effective_drawn_list_and_set(room, my_room_code)
    winners = room.winners.select_related('student').order_by('rank_order')
    my_win = room.winners.filter(student=request.user).first()
    my_room_winners = winners.filter(student__room_code=my_room_code).order_by('room_rank_order', 'won_at')

    winners_by_room_dict = {}
    for w in winners:
        rc = w.room_code or w.student.room_code or '99'
        r_label = w.student.room_display
        winners_by_room_dict.setdefault((int(rc) if rc.isdigit() else 99, r_label), []).append(w)
    winners_by_room = [(k[1], v) for k, v in sorted(winners_by_room_dict.items(), key=lambda x: x[0])]

    is_my_pick_turn = bool(
        card and room.allow_student_pick and room.active_picker_student_id == request.user.id
    )
    my_valid_pick_numbers = get_valid_student_bingo_picks(room, request.user) if is_my_pick_turn else []
    active_picker_info = None
    if room.active_picker_student_id and room.active_picker_student:
        ps = room.active_picker_student
        active_picker_info = {
            'id': ps.id,
            'name': ps.get_full_name(),
            'class_number': ps.class_number,
            'room_display': ps.room_display,
            'room_code': ps.room_code,
        }

    return render(request, 'games/student_bingo_play.html', {
        'room': room,
        'card': card,
        'my_room_code': my_room_code,
        'grid_numbers': grid_numbers,
        'grid_json': json.dumps(grid_numbers),
        'pool_numbers': pool_numbers,
        'pool_json': json.dumps(pool_numbers),
        'drawn_json': json.dumps(drawn_list),
        'drawn_scope_json': json.dumps(_build_bingo_drawn_scope_map(room, my_room_code)),
        'corners_json': json.dumps(sorted(list(corners))),
        'centers_json': json.dumps(sorted(list(centers))),
        'min_num': mn,
        'max_num': mx,
        'total_cells': room.size * room.size,
        'winners': winners,
        'my_win': my_win,
        'my_room_winners': my_room_winners,
        'winners_by_room': winners_by_room,
        'is_my_pick_turn': is_my_pick_turn,
        'active_picker_accepted': bool(room.active_picker_accepted) if is_my_pick_turn else False,
        'my_valid_pick_numbers_json': json.dumps(my_valid_pick_numbers),
        'active_picker': active_picker_info,
    })


def _build_bingo_drawn_scope_map(room, room_filter):
    """คืน dict {str(number): 'all' | 'room'} สำหรับแยกสีเลขที่ออกทั้งระดับ (เขียว) vs เฉพาะห้อง (เหลือง)"""
    scope_map = {}
    for d in get_effective_drawn_qs(room, room_filter):
        key = str(d.number)
        norm = normalize_bingo_room_filter(d.target_room_code)
        if norm == 'all':
            scope_map[key] = 'all'
        elif key not in scope_map:
            scope_map[key] = 'room'
    return scope_map


def _build_bingo_drawn_by_room(room):
    """สรุปรายการตัวเลขที่ออกไปแล้วแยกตาม 'ทั้งระดับ (all)' และแต่ละห้องเรียน (01-14)"""
    all_drawn = list(room.drawn_numbers.order_by('created_at', 'id').values_list('target_room_code', 'number'))
    own_map = {}
    for t_rc, num in all_drawn:
        norm = normalize_bingo_room_filter(t_rc)
        own_map.setdefault(norm, []).append(num)

    cards_by_rc = {}
    for c_rc, c_cnt in room.cards.values('student__room_code').annotate(cnt=Count('id')).values_list('student__room_code', 'cnt'):
        cards_by_rc[normalize_bingo_room_filter(c_rc or '01')] = c_cnt

    winners_by_rc = {}
    for w_rc, w_cnt in room.winners.values('room_code').annotate(cnt=Count('id')).values_list('room_code', 'cnt'):
        winners_by_rc[normalize_bingo_room_filter(w_rc or '01')] = w_cnt

    all_nums = own_map.get('all', [])
    all_set = set(all_nums)
    summary = [{
        'room_code': 'all',
        'room_label': 'ทุกห้องพร้อมกัน',
        'own_numbers': all_nums,
        'effective_numbers': all_nums,
        'effective_items': [{'number': n, 'scope': 'all'} for n in all_nums],
        'drawn_count': len(all_nums),
        'cards_count': room.cards.count(),
        'winners_count': room.winners.count(),
    }]
    for c in Classroom.objects.all().order_by('code'):
        rc = normalize_bingo_room_filter(c.code)
        own = own_map.get(rc, [])
        eff, _ = get_effective_drawn_list_and_set(room, rc)
        summary.append({
            'room_code': rc,
            'room_label': c.name,
            'own_numbers': own,
            'effective_numbers': eff,
            'effective_items': [{'number': n, 'scope': ('all' if n in all_set else 'room')} for n in eff],
            'drawn_count': len(eff),
            'own_count': len(own),
            'cards_count': cards_by_rc.get(rc, 0),
            'winners_count': winners_by_rc.get(rc, 0),
        })
    return summary


@login_required
def api_bingo_state(request, room_id):
    room = get_object_or_404(BingoRoom, pk=room_id)
    raw_rf = request.GET.get('room_filter')
    if raw_rf:
        rf = normalize_bingo_room_filter(raw_rf)
    elif request.user.is_student_role:
        rf = normalize_bingo_room_filter(request.user.room_code or '01')
    elif room.classroom:
        rf = normalize_bingo_room_filter(room.classroom.code)
    else:
        rf = 'all'

    drawn_qs = get_effective_drawn_qs(room, rf).select_related('drawn_by_student')
    drawn_nums, _ = get_effective_drawn_list_and_set(room, rf)
    last_drawn = drawn_qs.last()

    cards_count_in_scope = room.cards.count() if rf == 'all' else room.cards.filter(student__room_code=rf).count()
    drawn_history = []
    for idx, d in enumerate(drawn_qs, start=1):
        elig = d.eligible_cards_count
        hits = d.hit_count
        pct = round((hits * 100.0 / elig), 1) if elig else 0.0
        drawn_history.append({
            'order': idx,
            'number': d.number,
            'target_room_code': d.target_room_code,
            'target_room_label': get_bingo_room_label(d.target_room_code),
            'mode_display': d.get_drawn_by_mode_display(),
            'picker': d.drawn_by_student.get_full_name() if d.drawn_by_student else '',
            'hit_count': hits,
            'eligible_cards_count': elig,
            'hit_percent': pct,
            'time': d.created_at.strftime('%H:%M:%S'),
        })

    latest_hit_stats = compute_bingo_number_hits(room, last_drawn.number, rf) if last_drawn else None

    winners_qs = room.winners.select_related('student', 'student__classroom').order_by('rank_order')
    if rf != 'all':
        winners_qs_filtered = winners_qs.filter(room_code=rf)
    else:
        winners_qs_filtered = winners_qs

    winners = []
    grouped = {}
    for w in winners_qs_filtered:
        r_label = w.student.room_display
        rc = w.room_code or w.student.room_code or '99'
        item = {
            'rank': w.rank_order,
            'room_rank': w.room_rank_order,
            'student_id': w.student.student_id or w.student.username,
            'name': w.student.get_full_name(),
            'room': r_label,
            'room_code': rc,
            'avatar': w.student.avatar_url,
            'initial': w.student.avatar_initial,
            'pattern': w.win_pattern,
            'winning_number': w.winning_number,
            'score_awarded': w.score_awarded,
            'won_at': w.won_at.strftime('%H:%M:%S'),
        }
        winners.append(item)
        grouped.setdefault((int(rc) if rc.isdigit() else 99, r_label), []).append(item)

    winners_by_room = {k[1]: v for k, v in sorted(grouped.items(), key=lambda x: x[0])}

    is_my_pick_turn = bool(
        room.allow_student_pick and room.active_picker_student_id == request.user.id
        and BingoCard.objects.filter(room=room, student=request.user).exists()
    )
    my_valid_pick_numbers = get_valid_student_bingo_picks(room, request.user) if is_my_pick_turn else []
    active_picker_payload = None
    if room.active_picker_student_id and room.active_picker_student:
        ps = room.active_picker_student
        active_picker_payload = {
            'id': ps.id,
            'student_id': ps.student_id or ps.username,
            'name': ps.get_full_name(),
            'class_number': ps.class_number,
            'room_display': ps.room_display,
            'room_code': room.active_picker_room_code or ps.room_code,
            'accepted': bool(room.active_picker_accepted),
        }

    return JsonResponse({
        'ok': True,
        'room_status': room.status,
        'room_filter': rf,
        'room_filter_label': get_bingo_room_label(rf),
        'is_grade_wide': room.is_grade_wide,
        'continuous_play': room.continuous_play,
        'allow_student_pick': room.allow_student_pick,
        'active_picker': active_picker_payload,
        'is_my_pick_turn': is_my_pick_turn,
        'active_picker_accepted': bool(room.active_picker_accepted) if is_my_pick_turn else False,
        'my_valid_pick_numbers': my_valid_pick_numbers,
        'last_picker_event': room.last_picker_event or '',
        'drawn_numbers': drawn_nums,
        'drawn_scope_map': _build_bingo_drawn_scope_map(room, rf),
        'drawn_history': drawn_history,
        'drawn_by_room': _build_bingo_drawn_by_room(room),
        'last_number': last_drawn.number if last_drawn else None,
        'last_mode': last_drawn.get_drawn_by_mode_display() if last_drawn else '',
        'last_picker': last_drawn.drawn_by_student.get_full_name() if (last_drawn and last_drawn.drawn_by_student) else '',
        'latest_hit_stats': latest_hit_stats,
        'cards_count': room.cards.count(),
        'cards_count_in_scope': cards_count_in_scope,
        'winners': winners,
        'winners_by_room': winners_by_room,
    })


@teacher_required
@require_POST
def api_bingo_draw(request, room_id):
    """ครูผู้สอนกดหมุนวงล้อสุ่มเลข Bingo (รองรับเลือกออกเลขรายห้องหรือทั้งระดับ + คืนสถิติจำนวนคนที่ได้รับเลข)"""
    room = get_object_or_404(BingoRoom, pk=room_id)
    mode = request.POST.get('mode', BingoDrawnNumber.MODE_NORMAL)
    manual_num = request.POST.get('number')
    target_room_code = normalize_bingo_room_filter(
        request.POST.get('target_room_code') or (room.classroom.code if room.classroom else 'all')
    )
    try:
        drawn_obj, new_winners = draw_next_bingo_number(
            room,
            mode=mode,
            chosen_number=int(manual_num) if (manual_num and manual_num.isdigit()) else None,
            teacher=request.user,
            target_room_code=target_room_code,
        )
        return JsonResponse({
            'ok': True,
            'number': drawn_obj.number,
            'target_room_code': drawn_obj.target_room_code,
            'target_room_label': get_bingo_room_label(drawn_obj.target_room_code),
            'mode_display': drawn_obj.get_drawn_by_mode_display(),
            'hit_stats': getattr(drawn_obj, 'hit_stats', None),
            'new_winners': [
                {
                    'name': w.student.get_full_name(),
                    'student_id': w.student.student_id,
                    'room': w.student.room_display,
                    'room_rank': w.room_rank_order,
                    'pattern': w.win_pattern,
                    'score_awarded': w.score_awarded,
                }
                for w in new_winners
            ],
        })
    except Exception as ex:
        return JsonResponse({'ok': False, 'error': str(ex)}, status=400)


@teacher_required
@require_POST
def api_bingo_grant_picker(request, room_id):
    """ครูผู้สอนกดให้สิทธิ์นักเรียนเลขที่ X ในห้องที่เลือกเป็นผู้ออกเลขวงล้อ Bingo (หรือยกเลิกสิทธิ์)"""
    room = get_object_or_404(BingoRoom, pk=room_id)
    action = (request.POST.get('action') or 'grant').strip()
    if action == 'revoke':
        room.active_picker_student = None
        room.active_picker_room_code = ''
        room.active_picker_granted_at = None
        room.active_picker_accepted = False
        room.last_picker_event = 'ครูผู้สอนยกเลิกสิทธิ์การออกเลขแล้ว'
        room.save(update_fields=['active_picker_student', 'active_picker_room_code', 'active_picker_granted_at', 'active_picker_accepted', 'last_picker_event'])
        return JsonResponse({'ok': True, 'active_picker': None, 'last_picker_event': room.last_picker_event})

    student_pk = (request.POST.get('student_id') or '').strip()
    raw_rc = (request.POST.get('room_code') or '').strip()
    raw_no = (request.POST.get('class_number') or '').strip()

    student = None
    room_yr = (getattr(room, 'academic_year', '') or get_current_academic_year()).strip()
    if student_pk.isdigit():
        student = User.objects.filter(pk=int(student_pk), role=User.ROLE_STUDENT, academic_year=room_yr, is_active=True).first()
    elif raw_no.isdigit():
        st_qs = User.objects.filter(role=User.ROLE_STUDENT, academic_year=room_yr, is_active=True, class_number=int(raw_no))
        if raw_rc and raw_rc.lower() != 'all':
            norm_rc = raw_rc.zfill(2) if raw_rc.isdigit() else raw_rc
            st_qs = st_qs.filter(room_code=norm_rc)
        student = st_qs.filter(bingo_cards__room=room).first() or st_qs.first()

    if not student:
        return JsonResponse({'ok': False, 'error': 'ไม่พบนักเรียนตามเลขที่/ห้องที่ระบุ'}, status=404)

    if not BingoCard.objects.filter(room=room, student=student).exists():
        return JsonResponse(
            {'ok': False, 'error': f'{student.room_display} เลขที่ {student.class_number} {student.get_full_name()} ยังไม่ได้สร้างตาราง Bingo ในรอบนี้'},
            status=400,
        )

    st_rc = normalize_bingo_room_filter(student.room_code or '01')
    room.active_picker_student = student
    room.active_picker_room_code = st_rc
    room.active_picker_granted_at = timezone.now()
    room.active_picker_accepted = False
    room.last_picker_event = f'ครูให้สิทธิ์ {student.room_display} เลขที่ {student.class_number} {student.get_full_name()} เป็นผู้ออกเลข'
    if not room.allow_student_pick:
        room.allow_student_pick = True
    room.save(update_fields=[
        'active_picker_student', 'active_picker_room_code', 'active_picker_granted_at',
        'active_picker_accepted', 'last_picker_event', 'allow_student_pick',
    ])
    valid_nums = get_valid_student_bingo_picks(room, student)
    return JsonResponse({
        'ok': True,
        'active_picker': {
            'id': student.id,
            'student_id': student.student_id or student.username,
            'name': student.get_full_name(),
            'class_number': student.class_number,
            'room_display': student.room_display,
            'room_code': st_rc,
            'accepted': False,
        },
        'valid_pick_count': len(valid_nums),
        'last_picker_event': room.last_picker_event,
        'message': f'เปิดสิทธิ์ให้ {student.room_display} เลขที่ {student.class_number} ({student.get_full_name()}) ออกเลขแล้ว (เลขที่เลือกได้ {len(valid_nums)} เลข)',
    })


@login_required
@require_POST
def api_bingo_student_accept(request, room_id):
    """นักเรียนกดรับสิทธิ์ออกเลขหลังจากครูเลือกให้สิทธิ์ เพื่อปลดล็อกปุ่มเลือกเลขบนกระดาน"""
    room = get_object_or_404(BingoRoom, pk=room_id)
    if room.active_picker_student_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'ไม่พบสิทธิ์การออกเลขของคุณในรอบนี้'}, status=403)
    room.active_picker_accepted = True
    room.last_picker_event = f'{request.user.room_display} เลขที่ {request.user.class_number} {request.user.get_full_name()} กดรับสิทธิ์ออกเลขแล้ว'
    room.save(update_fields=['active_picker_accepted', 'last_picker_event'])
    valid_nums = get_valid_student_bingo_picks(room, request.user)
    return JsonResponse({
        'ok': True,
        'active_picker_accepted': True,
        'my_valid_pick_numbers': valid_nums,
        'last_picker_event': room.last_picker_event,
        'message': 'รับสิทธิ์ออกเลขเรียบร้อยแล้ว สามารถเลือกตัวเลขที่ต้องการได้ทันที',
    })


@login_required
@require_POST
def api_bingo_student_pick(request, room_id):
    """
    นักเรียนที่ได้รับสิทธิ์และกดรับสิทธิ์จากครูกดเลือกตัวเลขที่ต้องการให้ออก
    ผ่านระบบตรวจสอบเงื่อนไข:
    1) ต้องได้รับสิทธิ์และกดรับสิทธิ์แล้ว
    2) เลขต้องอยู่ในตารางของตนเองและยังไม่ถูกเปิด
    3) ต้องไม่ใช่ตำแหน่งมุมหรือตรงกลางตาราง
    4) จำลองการออกเลข (Simulation) หากทำให้ตัวเอง Bingo ทันที ระบบจะปฏิเสธ
    """
    room = get_object_or_404(BingoRoom, pk=room_id)
    if not room.allow_student_pick:
        return JsonResponse({'ok': False, 'error': 'ผู้ดูแลปิดโหมดให้นักเรียนเลือกตัวเลขในห้องนี้'}, status=400)
    if room.active_picker_student_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'ยังไม่ได้รับสิทธิ์ออกเลขในรอบนี้'}, status=403)
    if not room.active_picker_accepted:
        return JsonResponse({'ok': False, 'error': 'กรุณากดปุ่มรับสิทธิ์ออกเลขก่อนเลือกตัวเลข'}, status=403)

    chosen = request.POST.get('number')
    if not chosen or not str(chosen).lstrip('-').isdigit():
        return JsonResponse({'ok': False, 'error': 'กรุณาระบุตัวเลขที่ต้องการเลือก'}, status=400)

    st_rc = normalize_bingo_room_filter(room.active_picker_room_code or request.user.room_code or '01')
    try:
        drawn_obj, new_winners = draw_next_bingo_number(
            room,
            mode=BingoDrawnNumber.MODE_STUDENT,
            chosen_number=int(chosen),
            student=request.user,
            target_room_code=st_rc,
        )
        room.active_picker_student = None
        room.active_picker_room_code = ''
        room.active_picker_granted_at = None
        room.active_picker_accepted = False
        room.last_picker_event = f'{request.user.room_display} เลขที่ {request.user.class_number} {request.user.get_full_name()} ออกเลข {drawn_obj.number}'
        room.save(update_fields=['active_picker_student', 'active_picker_room_code', 'active_picker_granted_at', 'active_picker_accepted', 'last_picker_event'])
        return JsonResponse({
            'ok': True,
            'number': drawn_obj.number,
            'hit_stats': getattr(drawn_obj, 'hit_stats', None),
            'message': f'ออกหมายเลข {drawn_obj.number} ตามที่คุณเลือกเรียบร้อยแล้ว!',
            'new_winners': [w.student.get_full_name() for w in new_winners],
        })
    except Exception as ex:
        return JsonResponse({'ok': False, 'error': str(ex)}, status=400)


@login_required
@require_POST
def api_bingo_student_forfeit(request, room_id):
    """นักเรียนที่ได้รับสิทธิ์ออกเลขกดสละสิทธิ์เมื่อไม่มีตัวเลขที่เลือกได้ตามกติกา"""
    room = get_object_or_404(BingoRoom, pk=room_id)
    if room.active_picker_student_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'คุณไม่ได้อยู่ในสถานะผู้ได้รับสิทธิ์ออกเลข'}, status=403)
    room.active_picker_student = None
    room.active_picker_room_code = ''
    room.active_picker_granted_at = None
    room.active_picker_accepted = False
    room.last_picker_event = f'{request.user.room_display} เลขที่ {request.user.class_number} {request.user.get_full_name()} สละสิทธิ์การออกเลข'
    room.save(update_fields=['active_picker_student', 'active_picker_room_code', 'active_picker_granted_at', 'active_picker_accepted', 'last_picker_event'])
    return JsonResponse({
        'ok': True,
        'last_picker_event': room.last_picker_event,
        'message': 'สละสิทธิ์ออกเลขในรอบนี้เรียบร้อยแล้ว',
    })


# =============================================================================
# (ฝั่งนักเรียน) หน้าดูผลสุ่ม ID LOTTO
# =============================================================================
@login_required
def student_lotto_view(request):
    setting = SystemSetting.get()
    if not setting.lotto_menu_open and request.user.is_student_role:
        messages.warning(request, 'ขณะนี้ผู้ดูแลปิดเมนู ID LOTTO ชั่วคราว')
        return redirect('core:student_dashboard')

    cur_yr = (request.user.academic_year if request.user.is_student_role else '') or get_current_academic_year()
    sessions = LottoSession.objects.filter(academic_year=cur_yr, is_open=True).prefetch_related('results__student')
    current_session = sessions.first()
    my_wins = LottoResult.objects.filter(student=request.user, session__academic_year=cur_yr, removed=False).select_related('session')
    return render(request, 'games/student_lotto.html', {
        'sessions': sessions,
        'current_session': current_session,
        'my_wins': my_wins,
    })


# =============================================================================
# หน้าสำหรับผู้ดูแล / ครูผู้สอน (Teacher Views)
# =============================================================================
@teacher_required
def teacher_dashboard_view(request):
    cur_yr = get_current_academic_year()
    total_students = get_active_students_qs(cur_yr).count()
    total_classrooms = Classroom.objects.count()
    active_sorting = SortingActivity.objects.filter(academic_year=cur_yr, is_active=True).count()
    active_bingo = BingoRoom.objects.filter(academic_year=cur_yr, status__in=['open', 'playing']).count()
    recent_score_logs = ScoreLog.objects.filter(academic_year=cur_yr).select_related('student', 'rule')[:10]
    top_students = get_active_students_qs(cur_yr).order_by('-total_score', 'room_code', 'class_number')[:10]
    recent_lotto = LottoResult.objects.filter(session__academic_year=cur_yr, removed=False).select_related('student', 'session')[:8]
    online_sessions = list(
        UserSession.objects.filter(
            user__role=User.ROLE_STUDENT, user__academic_year=cur_yr, user__is_active=True, is_active=True
        )
        .select_related('user', 'user__classroom')
        .order_by('-last_active')[:40]
    )

    return render(request, 'teacher/dashboard.html', {
        'total_students': total_students,
        'total_classrooms': total_classrooms,
        'active_sorting': active_sorting,
        'active_bingo': active_bingo,
        'recent_score_logs': recent_score_logs,
        'top_students': top_students,
        'recent_lotto': recent_lotto,
        'online_sessions': online_sessions,
        'online_students_count': len(online_sessions),
    })


@teacher_required
def teacher_students_view(request):
    """จัดการโปรไฟล์นักเรียน: ดู ค้นหา เพิ่มนักเรียน และแก้ไขรูป/ข้อมูลนักเรียน (แยกตามปีการศึกษา)"""
    cur_yr = get_current_academic_year()
    room_filter = request.GET.get('room', '01')
    q = (request.GET.get('q') or '').strip()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_student':
            sid = (request.POST.get('student_id') or '').strip()
            prefix = (request.POST.get('title_prefix') or 'นาย').strip()
            fname = (request.POST.get('first_name') or '').strip()
            lname = (request.POST.get('last_name') or '').strip()
            rcode = (request.POST.get('room_code') or '01').strip()
            cno = int(request.POST.get('class_number') or 1)
            gname = (request.POST.get('group_name') or 'กลุ่ม A').strip()
            if not re.match(r'^\d{5}$', sid):
                messages.error(request, 'เลขประจำตัวนักเรียนต้องเป็นตัวเลข 5 หลัก')
            elif User.objects.filter(role=User.ROLE_STUDENT, academic_year=cur_yr, student_id=sid).exists():
                messages.error(request, f'เลขประจำตัว {sid} มีอยู่ในระบบปีการศึกษา {cur_yr} แล้ว')
            else:
                room_obj = Classroom.objects.filter(code=rcode).first()
                st = User(
                    username=generate_unique_student_username(sid, cur_yr),
                    student_id=sid,
                    academic_year=cur_yr,
                    email=f's{sid}@debsirin.ac.th',
                    title_prefix=prefix,
                    first_name=fname,
                    last_name=lname,
                    classroom=room_obj,
                    room_code=rcode,
                    class_number=cno,
                    group_name=gname,
                    role=User.ROLE_STUDENT,
                    is_active=True,
                    total_score=0.0,
                )
                st.set_password(sid)
                st.save()
                messages.success(request, f'เพิ่มนักเรียน {sid} ({st.get_full_name()}) ปีการศึกษา {cur_yr} อีเมล {st.email} เรียบร้อยแล้ว')
                return redirect(f'/teacher/students/?room={rcode}')

    qs = get_active_students_qs(cur_yr).select_related('classroom')
    if room_filter and room_filter != 'all':
        qs = qs.filter(room_code=room_filter)
    if q:
        qs = qs.filter(
            Q(student_id__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
        )

    if request.GET.get('export') == 'xlsx':
        wb = students_export_workbook(qs.order_by('room_code', 'class_number', 'student_id'))
        return workbook_response(wb, f'รายชื่อนักเรียน_COMSC4_{room_filter}.xlsx')

    students = qs.order_by('room_code', 'class_number', 'student_id')[:200]
    classrooms = Classroom.objects.all()

    return render(request, 'teacher/students.html', {
        'students': students,
        'classrooms': classrooms,
        'selected_room': room_filter,
        'q': q,
    })


@teacher_required
def teacher_import_students_view(request):
    """นำเข้ารายชื่อนักเรียนตามกระบวนการแบบโปรเจกต์ DSCOMPOSN (ตรวจสอบคอลัมน์ + Preview ก่อนนำเข้าจริง แยกตามปีการศึกษา)"""
    from pathlib import Path
    from django.conf import settings

    cur_yr = get_current_academic_year()
    result = None
    preview = None
    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'confirm_import' and request.session.get('comsc4_import_students_preview'):
            pdata = request.session.pop('comsc4_import_students_preview')
            result = import_students_from_preview(pdata, academic_year=pdata.get('academic_year') or cur_yr)
            messages.success(
                request,
                f'Import สำเร็จ (ปีการศึกษา {result.get("academic_year", cur_yr)}): เพิ่มใหม่ {result["created"]} คน, ทับข้อมูลเดิม {result["updated"]} คน'
            )
        elif action == 'load_default_csv':
            csv_path = Path(settings.BASE_DIR) / 'students_data.csv'
            if not csv_path.exists():
                csv_path = Path(settings.BASE_DIR) / 'student_data.csv'
            if csv_path.exists():
                with open(csv_path, 'rb') as fp:
                    result = import_students_file(fp, academic_year=cur_yr)
                messages.success(
                    request,
                    f'โหลดข้อมูลเริ่มต้นจาก {csv_path.name} (ปีการศึกษา {cur_yr}) สำเร็จ: เพิ่มใหม่ {result["created"]} คน, อัปเดต {result["updated"]} คน'
                )
            else:
                messages.error(request, 'ไม่พบไฟล์ students_data.csv ในโฟลเดอร์โปรเจกต์')
        elif 'file' in request.FILES:
            uploaded = request.FILES['file']
            if action == 'preview':
                try:
                    preview = preview_students(uploaded, academic_year=cur_yr)
                    request.session['comsc4_import_students_preview'] = {
                        'academic_year': cur_yr,
                        'filename': uploaded.name,
                        'rows': preview['rows'],
                        'present_fields': preview.get('present_fields', []),
                    }
                except ValueError as e:
                    messages.error(request, str(e))
            else:
                try:
                    result = import_students_file(uploaded, academic_year=cur_yr)
                    messages.success(
                        request,
                        f'Import สำเร็จ (ปีการศึกษา {cur_yr}): เพิ่มใหม่ {result["created"]} คน, ทับข้อมูลเดิม {result["updated"]} คน'
                    )
                except ValueError as e:
                    messages.error(request, str(e))

    return render(request, 'teacher/import_students.html', {
        'title': f'Import ข้อมูลนักเรียน (CSV / Excel) · ปีการศึกษา {cur_yr}',
        'current_academic_year': cur_yr,
        'result': result,
        'preview': preview,
        'columns': STUDENT_COLUMNS_TH,
        'columns_en': STUDENT_COLUMNS,
    })


@teacher_required
def teacher_students_template_view(request):
    lang = 'en' if request.GET.get('lang') == 'en' else 'th'
    wb = students_template_workbook(lang=lang)
    fname = 'template_students_en.xlsx' if lang == 'en' else 'ตัวอย่างไฟล์นำเข้านักเรียน_COMSC4.xlsx'
    return workbook_response(wb, fname)


@teacher_required
def teacher_student_edit_view(request, user_id):
    student = get_object_or_404(User, pk=user_id, role=User.ROLE_STUDENT)
    classrooms = Classroom.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action', 'save_profile')
        if action == 'upload_photo':
            cropped_b64 = request.POST.get('cropped_image_data', '')
            uploaded_file = request.FILES.get('avatar_file')
            rotate_deg = int(request.POST.get('rotate_deg') or 0)
            try:
                process_cropped_or_rotated_avatar(student, uploaded_file=uploaded_file, cropped_base64=cropped_b64, rotate_deg=rotate_deg)
                messages.success(request, f'อัปเดตรูปประจำตัวของ {student.get_full_name()} เรียบร้อยแล้ว')
            except Exception as ex:
                messages.error(request, f'เกิดข้อผิดพลาด: {ex}')
        elif action == 'save_profile':
            student.title_prefix = (request.POST.get('title_prefix') or '').strip()
            student.first_name = (request.POST.get('first_name') or '').strip()
            student.last_name = (request.POST.get('last_name') or '').strip()
            rcode = (request.POST.get('room_code') or student.room_code).strip()
            student.room_code = rcode
            student.classroom = Classroom.objects.filter(code=rcode).first()
            student.class_number = int(request.POST.get('class_number') or student.class_number)
            student.group_name = (request.POST.get('group_name') or 'กลุ่ม A').strip()
            student.email = f's{student.student_id}@debsirin.ac.th'
            student.save()
            messages.success(request, 'บันทึกข้อมูลนักเรียนเรียบร้อยแล้ว')
            return redirect(f'/teacher/students/?room={student.room_code}')

    return render(request, 'teacher/student_edit.html', {
        'student': student,
        'classrooms': classrooms,
    })


@teacher_required
def teacher_lessons_quizzes_view(request):
    """(ฝั่งครู) สร้างเนื้อหาบทเรียน และสั่ง AI สร้างแบบทดสอบอัตโนมัติ (แยกตามปีการศึกษา)"""
    cur_yr = get_current_academic_year()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_lesson':
            LessonContent.objects.create(
                academic_year=cur_yr,
                unit_number=int(request.POST.get('unit_number') or 1),
                title=(request.POST.get('title') or '').strip(),
                summary=(request.POST.get('summary') or '').strip(),
                content_html=(request.POST.get('content_html') or '').strip(),
                created_by=request.user,
            )
            messages.success(request, 'สร้างเนื้อหาบทเรียนใหม่เรียบร้อยแล้ว')
        elif action == 'generate_ai_quiz':
            title = (request.POST.get('title') or '').strip()
            topic = (request.POST.get('topic_prompt') or title).strip()
            q_count = int(request.POST.get('question_count') or 5)
            pts = float(request.POST.get('points_per_question') or 1.0)
            raw_lid = (request.POST.get('lesson_id') or '').strip()
            linked_lesson = (
                LessonContent.objects.filter(pk=int(raw_lid), academic_year=cur_yr).first()
                if raw_lid.isdigit()
                else LessonContent.objects.filter(academic_year=cur_yr).order_by('unit_number', 'id').first()
            )
            q_items, engine_name = generate_ai_quiz_questions(topic, count=q_count)
            quiz = AIQuiz.objects.create(
                lesson=linked_lesson,
                academic_year=cur_yr,
                title=title or f'แบบทดสอบ AI: {topic}',
                topic_prompt=topic,
                ai_engine_used=engine_name,
                points_per_question=pts,
                sync_to_total_score=True,
                is_active=True,
                created_by=request.user,
            )
            for idx, item in enumerate(q_items, start=1):
                AIQuizQuestion.objects.create(
                    quiz=quiz,
                    order_no=idx,
                    question_text=item['q'],
                    choice_a=item['a'],
                    choice_b=item['b'],
                    choice_c=item['c'],
                    choice_d=item['d'],
                    correct_choice=item['ans'],
                    explanation=item.get('exp', ''),
                )
            messages.success(request, f'AI ({engine_name}) สร้างข้อสอบ "{quiz.title}" จำนวน {len(q_items)} ข้อสำเร็จ!')
        return redirect('core:teacher_lessons_quizzes')

    lessons = LessonContent.objects.filter(academic_year=cur_yr)
    quizzes = AIQuiz.objects.filter(academic_year=cur_yr).prefetch_related('questions', 'attempts__student')
    attempts = AIQuizAttempt.objects.filter(quiz__academic_year=cur_yr).select_related('student', 'quiz').order_by('-created_at')[:30]
    return render(request, 'teacher/lessons_quizzes.html', {
        'lessons': lessons,
        'quizzes': quizzes,
        'attempts': attempts,
    })


@teacher_required
def teacher_activities_view(request):
    """(ฝั่งครู) จัดการโครงสร้างใบงาน/กิจกรรม และโครงสร้างคะแนนแต่ละช่วง (รวมตรงไม่หาร vs เฉลี่ยสัดส่วน) โดยไม่ปนรายชื่อนักเรียน"""
    cur_yr = get_current_academic_year()
    if not ScorePeriod.objects.filter(academic_year=cur_yr).exists():
        get_active_score_period(cur_yr)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'seed_room01_samples':
            res = seed_sample_activities_room01(teacher_user=request.user, force_refresh_samples=True)
            messages.success(
                request,
                f'อัปเดตโจทย์และผลงานตัวอย่างห้อง ม.4/1 เรียบร้อยแล้ว '
                f'(กิจกรรมที่ 1 ตอบแล้ว 28/{res["total_r01"]} คน · กิจกรรมที่ 2 ภาพผลไม้ {res["total_r01"]}/{res["total_r01"]} คน)'
            )
            return redirect('core:teacher_activities')

        elif action == 'create_period':
            max_ord = ScorePeriod.objects.filter(academic_year=cur_yr).aggregate(m=Max('order_no'))['m'] or 0
            calc_mode = (request.POST.get('score_calc_mode') or ScorePeriod.CALC_DIRECT).strip()
            if calc_mode not in (ScorePeriod.CALC_DIRECT, ScorePeriod.CALC_PROPORTIONAL):
                calc_mode = ScorePeriod.CALC_DIRECT
            new_p = ScorePeriod.objects.create(
                name=(request.POST.get('name') or f'ช่วงคะแนนที่ {max_ord + 1}').strip(),
                academic_year=cur_yr,
                order_no=max(1, int(request.POST.get('order_no') or (max_ord + 1))),
                max_score=max(1.0, float(request.POST.get('max_score') or 20.0)),
                score_calc_mode=calc_mode,
                note=(request.POST.get('note') or '').strip(),
                is_finalized=False,
            )
            messages.success(request, f'สร้างช่วงคะแนน "{new_p.name}" (คะแนนเต็ม {new_p.max_score:g}) เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

        elif action == 'update_period_config':
            period = get_object_or_404(ScorePeriod, pk=request.POST.get('target_period_id'))
            if period.is_finalized:
                messages.error(request, f'ช่วงคะแนน "{period.name}" สรุปคะแนนและล็อกไว้แล้ว ไม่สามารถแก้ไขโครงสร้างได้')
                return redirect('core:teacher_activities')
            calc_mode = (request.POST.get('score_calc_mode') or period.score_calc_mode or ScorePeriod.CALC_DIRECT).strip()
            if calc_mode not in (ScorePeriod.CALC_DIRECT, ScorePeriod.CALC_PROPORTIONAL):
                calc_mode = ScorePeriod.CALC_DIRECT
            period.name = (request.POST.get('name') or period.name).strip()
            period.order_no = max(1, int(request.POST.get('order_no') or period.order_no))
            period.max_score = max(1.0, float(request.POST.get('max_score') or period.max_score))
            period.score_calc_mode = calc_mode
            if 'note' in request.POST:
                period.note = (request.POST.get('note') or '').strip()
            period.save()
            recalculate_period_students_scores(period)
            mode_lbl = 'รวมแบบไม่เฉลี่ยสัดส่วน (บวกตรง)' if calc_mode == ScorePeriod.CALC_DIRECT else 'รวมแบบเฉลี่ยสัดส่วน'
            messages.success(request, f'อัปเดตโครงสร้าง "{period.name}" (เต็ม {period.max_score:g} คะแนน · {mode_lbl}) เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

        elif action == 'create_activity':
            p_id = request.POST.get('score_period_id')
            period = ScorePeriod.objects.filter(pk=p_id).first() if p_id else get_active_score_period()
            if period and period.is_finalized:
                messages.error(request, f'ช่วงคะแนน "{period.name}" สรุปคะแนนแล้ว ไม่สามารถสร้างกิจกรรมลงในช่วงนี้ได้')
                return redirect('core:teacher_activities')
            raw_ms = (request.POST.get('max_score') or '').strip()
            ms_val = max(1.0, float(raw_ms)) if raw_ms else get_default_activity_max_score()
            new_act = LearningActivity.objects.create(
                title=(request.POST.get('title') or '').strip(),
                description=(request.POST.get('description') or '').strip(),
                activity_type=request.POST.get('activity_type', LearningActivity.TYPE_GENERAL),
                academic_year=getattr(period, 'academic_year', '') or cur_yr,
                score_period=period,
                max_score=ms_val,
                is_active=True,
                is_group_activity=(request.POST.get('is_group_activity') == '1'),
                max_group_size=max(2, min(30, int(request.POST.get('max_group_size') or 4))),
                group_selection_open=(request.POST.get('group_selection_open', '1') == '1'),
                created_by=request.user,
            )
            remember_activity_max_score(new_act.max_score)
            ensure_activity_default_grading_tiers(new_act)
            if period:
                recalculate_period_students_scores(period)
            messages.success(request, f'สร้างกิจกรรม "{new_act.title}" ใน {period.name if period else "-"} เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

        elif action == 'toggle_group_selection':
            act = get_object_or_404(LearningActivity, pk=request.POST.get('target_activity_id'))
            act.group_selection_open = not act.group_selection_open
            act.save(update_fields=['group_selection_open'])
            state_lbl = 'เปิดระบบจับกลุ่ม' if act.group_selection_open else 'ปิดระบบจับกลุ่ม (ล็อกสมาชิกกลุ่ม)'
            messages.success(request, f'{state_lbl} สำหรับ "{act.title}" แล้ว')
            return redirect('core:teacher_activities')

        elif action == 'edit_activity':
            act = get_object_or_404(LearningActivity, pk=request.POST.get('target_activity_id'))
            old_period = act.score_period
            p_id = request.POST.get('score_period_id')
            new_period = ScorePeriod.objects.filter(pk=p_id).first() if p_id else old_period
            if (old_period and old_period.is_finalized) or (new_period and new_period.is_finalized):
                messages.error(request, 'ไม่สามารถแก้ไขหรือย้ายกิจกรรมที่อยู่ในช่วงคะแนนที่สรุปและล็อกแล้วได้')
                return redirect('core:teacher_activities')
            act.title = (request.POST.get('title') or act.title).strip()
            act.description = (request.POST.get('description') or '').strip()
            atype = (request.POST.get('activity_type') or act.activity_type).strip()
            if atype in (LearningActivity.TYPE_QA, LearningActivity.TYPE_IMAGE, LearningActivity.TYPE_GENERAL):
                act.activity_type = atype
            act.max_score = max(1.0, float(request.POST.get('max_score') or act.max_score or 10))
            act.score_period = new_period
            act.is_active = (request.POST.get('is_active', '1') == '1')
            act.save()
            if old_period:
                recalculate_period_students_scores(old_period)
            if new_period and new_period != old_period:
                recalculate_period_students_scores(new_period)
            messages.success(request, f'แก้ไขกิจกรรม "{act.title}" เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

        elif action == 'update_activity_period':
            act = get_object_or_404(LearningActivity, pk=request.POST.get('target_activity_id'))
            old_period = act.score_period
            new_p = get_object_or_404(ScorePeriod, pk=request.POST.get('score_period_id'))
            if (old_period and old_period.is_finalized) or new_p.is_finalized:
                messages.error(request, 'ไม่สามารถย้ายกิจกรรมที่อยู่ในช่วงคะแนนที่สรุปและล็อกแล้วได้')
                return redirect('core:teacher_activities')
            act.score_period = new_p
            if request.POST.get('title'):
                act.title = request.POST.get('title').strip()
            if request.POST.get('max_score'):
                act.max_score = max(1.0, float(request.POST.get('max_score')))
            act.save()
            if old_period:
                recalculate_period_students_scores(old_period)
            recalculate_period_students_scores(new_p)
            messages.success(request, f'ปรับช่วงคะแนนของ "{act.title}" เป็น "{new_p.name}" เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

        elif action == 'toggle_activity_active':
            act = get_object_or_404(LearningActivity, pk=request.POST.get('target_activity_id'))
            act.is_active = not act.is_active
            act.save(update_fields=['is_active'])
            return redirect('core:teacher_activities')

        elif action == 'delete_activity':
            act = get_object_or_404(LearningActivity, pk=request.POST.get('target_activity_id'))
            old_period = act.score_period
            if old_period and old_period.is_finalized:
                messages.error(request, 'ไม่สามารถลบกิจกรรมที่อยู่ในช่วงคะแนนที่สรุปและล็อกแล้วได้')
                return redirect('core:teacher_activities')
            t_name = act.title
            act.delete()
            if old_period:
                recalculate_period_students_scores(old_period)
            messages.info(request, f'ลบกิจกรรม "{t_name}" เรียบร้อยแล้ว')
            return redirect('core:teacher_activities')

    periods_all = list(ScorePeriod.objects.filter(academic_year=cur_yr).order_by('order_no', 'id'))
    period_structures = [compute_period_structure(p) for p in periods_all]
    unassigned_activities = list(
        LearningActivity.objects.filter(academic_year=cur_yr, score_period__isnull=True)
        .annotate(submission_count=Count('submissions'))
        .order_by('id')
    )
    total_course_max_score = round(sum(ps['target_max_score'] for ps in period_structures), 2)
    total_activities_count = LearningActivity.objects.filter(academic_year=cur_yr).count()

    return render(request, 'teacher/activities.html', {
        'period_structures': period_structures,
        'unassigned_activities': unassigned_activities,
        'total_course_max_score': total_course_max_score,
        'total_activities_count': total_activities_count,
        'all_periods': periods_all,
        'open_periods': ScorePeriod.objects.filter(academic_year=cur_yr, is_finalized=False).order_by('order_no', 'id'),
        'activity_types': LearningActivity.TYPE_CHOICES,
        'calc_mode_choices': ScorePeriod.CALC_MODE_CHOICES,
        'active_period': get_active_score_period(cur_yr),
        'default_activity_max_score': get_default_activity_max_score(),
    })


@teacher_required
@require_POST
def api_activity_upload_media(request):
    """อัปโหลดรูปภาพหรือ PDF จากตัวแก้ไขแบบ Word เพื่อแทรกลงในใบงานหรือเฉลย"""
    up_file = request.FILES.get('file')
    kind = (request.POST.get('kind') or 'image').strip().lower()
    if not up_file:
        return JsonResponse({'ok': False, 'error': 'กรุณาเลือกไฟล์ที่ต้องการอัปโหลด'}, status=400)

    ext = Path(up_file.name).suffix.lower() if hasattr(up_file, 'name') else ''
    if kind == 'pdf':
        if ext != '.pdf':
            return JsonResponse({'ok': False, 'error': 'รองรับเฉพาะไฟล์ .pdf เท่านั้น'}, status=400)
        folder = 'activities/pdfs'
    else:
        if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'):
            return JsonResponse({'ok': False, 'error': 'รองรับเฉพาะไฟล์รูปภาพ (.png, .jpg, .webp, .gif)'}, status=400)
        folder = 'activities/editor'

    rel_path = default_storage.save(f'{folder}/{uuid.uuid4().hex[:12]}{ext}', up_file)
    return JsonResponse({
        'ok': True,
        'url': default_storage.url(rel_path),
        'name': up_file.name,
        'kind': kind,
    })


@teacher_required
@require_POST
def api_activity_toggle_answer_item(request, activity_id):
    """เปิด/ปิด เฉลยใบงานทีละข้อ หรือทั้งหมด จากหน้า นำเสนอใบงาน"""
    act = get_object_or_404(LearningActivity, pk=activity_id)
    mode = (request.POST.get('mode') or 'single').strip().lower()
    now = timezone.now()
    if mode == 'reveal_all':
        act.answer_key_items.update(is_revealed=True, revealed_at=now)
    elif mode == 'hide_all':
        act.answer_key_items.update(is_revealed=False)
    else:
        item = get_object_or_404(ActivityAnswerKeyItem, pk=request.POST.get('item_id'), activity=act)
        item.is_revealed = not item.is_revealed
        item.revealed_at = now if item.is_revealed else None
        item.save(update_fields=['is_revealed', 'revealed_at'])

    items = [
        {
            'id': it.id,
            'order_no': it.order_no,
            'title': it.title,
            'is_revealed': it.is_revealed,
            'content_html': it.content_html,
            'pdf_url': it.pdf_file.url if it.pdf_file else '',
            'pdf_display_mode': it.pdf_display_mode,
        }
        for it in act.answer_key_items.all()
    ]
    return JsonResponse({
        'ok': True,
        'revealed_count': sum(1 for it in items if it['is_revealed']),
        'total_count': len(items),
        'items': items,
    })


def _parse_datetime_local(raw_val):
    s = (raw_val or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            dt_obj = datetime.strptime(s, fmt)
            if timezone.is_naive(dt_obj):
                return timezone.make_aware(dt_obj, timezone.get_current_timezone())
            return dt_obj
        except ValueError:
            continue
    return None


@login_required
@require_POST
def api_activity_ai_check(request, activity_id):
    """ให้ AI ตรวจประเมินคำตอบเบื้องต้นระหว่างนักเรียนกำลังจะส่ง และเตือนเมื่อคล้ายคำตอบของเพื่อน"""
    act = get_object_or_404(LearningActivity, pk=activity_id)
    work_title = (request.POST.get('work_title') or '').strip()
    work_text = (request.POST.get('work_text') or '').strip()
    has_file = (
        request.POST.get('has_file') == '1'
        or bool(request.FILES.get('work_file'))
        or ActivitySubmission.objects.filter(activity=act, student=request.user).exclude(work_file='').exists()
    )
    res = evaluate_student_submission_with_ai(
        act, request.user, work_title=work_title, work_text=work_text, has_file=has_file
    )
    return JsonResponse(res)


@teacher_required
@require_POST
def api_activity_verify_gdrive(request):
    """AJAX: ตรวจสอบว่า Google Drive Folder ที่ระบุสามารถเข้าถึงและใช้งานได้จริงก่อนอนุมัติ"""
    folder_url = (request.POST.get('gdrive_folder_url') or '').strip()
    res = verify_gdrive_folder_access(folder_url)
    return JsonResponse(res)


@teacher_required
def teacher_activity_editor_view(request, activity_id=None):
    """ตัวจัดการใบงานแบบเต็มหน้า 3 แท็บ: 1) สร้าง/ตบแต่งใบงาน & PDF 2) เฉลยใบงานรายข้อ 3) กำหนดคะแนนแต่ละครั้งที่ตรวจ"""
    activity = get_object_or_404(LearningActivity, pk=activity_id) if activity_id else None
    active_tab = (request.GET.get('tab') or request.POST.get('active_tab') or 'worksheet').strip()
    if active_tab not in ('worksheet', 'answer', 'scoring'):
        active_tab = 'worksheet'

    if request.method == 'POST':
        old_period = activity.score_period if activity else None
        p_id = request.POST.get('score_period_id')
        new_period = ScorePeriod.objects.filter(pk=p_id).first() if p_id else (old_period or get_active_score_period())
        if (old_period and old_period.is_finalized) or (new_period and new_period.is_finalized):
            messages.error(request, 'ช่วงคะแนนที่เกี่ยวข้องถูกสรุปและล็อกไว้แล้ว ไม่สามารถบันทึกการเปลี่ยนแปลงได้')
            if activity:
                return redirect(f'/teacher/activities/{activity.id}/edit/?tab={active_tab}')
            return redirect('core:teacher_activity_create')

        title = (request.POST.get('title') or '').strip()
        if not title:
            messages.error(request, 'กรุณาระบุชื่อใบงาน / กิจกรรม')
            return redirect(request.path)

        content_html = (request.POST.get('content_html') or '').strip()
        desc = (request.POST.get('description') or '').strip()
        if not desc and content_html:
            desc = re.sub(r'\s+', ' ', strip_tags(content_html)).strip()[:250]

        atype = (request.POST.get('activity_type') or LearningActivity.TYPE_GENERAL).strip()
        if atype not in (
            LearningActivity.TYPE_QA,
            LearningActivity.TYPE_IMAGE,
            LearningActivity.TYPE_OTHER_FILE,
            LearningActivity.TYPE_GENERAL,
        ):
            atype = LearningActivity.TYPE_GENERAL

        academic_year = (
            request.POST.get('academic_year')
            or (activity.academic_year if activity else '')
            or get_current_academic_year()
        ).strip()[:20]
        allowed_file_extensions = (request.POST.get('allowed_file_extensions') or '').strip()[:255]
        storage_dest = (request.POST.get('storage_destination') or LearningActivity.STORAGE_LOCAL).strip()
        if storage_dest not in (LearningActivity.STORAGE_LOCAL, LearningActivity.STORAGE_GDRIVE):
            storage_dest = LearningActivity.STORAGE_LOCAL
        gdrive_url_raw = (request.POST.get('gdrive_folder_url') or '').strip()[:500]

        pdf_mode = (request.POST.get('pdf_display_mode') or LearningActivity.PDF_EMBED).strip()
        if pdf_mode not in (LearningActivity.PDF_EMBED, LearningActivity.PDF_DOWNLOAD):
            pdf_mode = LearningActivity.PDF_EMBED

        raw_max_sc = (request.POST.get('max_score') or '').strip()
        max_score_val = max(1.0, float(raw_max_sc)) if raw_max_sc else get_default_activity_max_score()
        due_dt = _parse_datetime_local(request.POST.get('due_date'))
        is_active = (request.POST.get('is_active') == '1')
        is_group_act = (request.POST.get('is_group_activity') == '1')
        grp_count_raw = (request.POST.get('group_count') or '').strip()
        grp_count = max(2, min(30, int(grp_count_raw))) if (is_group_act and grp_count_raw.isdigit()) else (10 if is_group_act else 0)
        max_grp_size = max(2, min(30, int(request.POST.get('max_group_size') or 4)))
        grp_sel_open = (request.POST.get('group_selection_open') == '1')

        target_rooms_json_val = activity.target_rooms_json if activity else '[]'
        if request.POST.get('target_rooms_present') == '1':
            if request.POST.get('target_all_rooms') == '1':
                target_rooms_json_val = '[]'
            else:
                all_valid_codes = set(Classroom.objects.values_list('code', flat=True))
                chosen_codes = []
                for rc in request.POST.getlist('target_room_codes'):
                    rc_clean = rc.strip().zfill(2) if rc.strip().isdigit() else rc.strip()
                    if rc_clean and (not all_valid_codes or rc_clean in all_valid_codes):
                        chosen_codes.append(rc_clean)
                chosen_set = sorted(set(chosen_codes))
                if not chosen_set or (all_valid_codes and set(chosen_set) >= all_valid_codes):
                    target_rooms_json_val = '[]'
                else:
                    target_rooms_json_val = json.dumps(chosen_set)

        if not activity:
            activity = LearningActivity(created_by=request.user)

        activity.title = title[:200]
        activity.description = desc
        activity.content_html = content_html
        activity.activity_type = atype
        activity.academic_year = academic_year or get_current_academic_year()
        activity.allowed_file_extensions = allowed_file_extensions
        activity.score_period = new_period
        activity.max_score = max_score_val
        activity.due_date = due_dt
        activity.is_active = is_active
        activity.is_group_activity = is_group_act
        activity.group_count = grp_count
        activity.max_group_size = max_grp_size
        activity.group_selection_open = grp_sel_open
        activity.target_rooms_json = target_rooms_json_val
        activity.pdf_display_mode = pdf_mode

        if storage_dest == LearningActivity.STORAGE_GDRIVE:
            v_res = verify_gdrive_folder_access(gdrive_url_raw)
            activity.gdrive_folder_url = v_res.get('folder_url') or gdrive_url_raw
            activity.gdrive_folder_id = v_res.get('folder_id') or parse_gdrive_folder_id(gdrive_url_raw)
            activity.gdrive_verify_message = (v_res.get('message') or '')[:300]
            if v_res.get('ok'):
                activity.storage_destination = LearningActivity.STORAGE_GDRIVE
                activity.gdrive_verified = True
                activity.gdrive_verified_at = timezone.now()
            else:
                activity.storage_destination = LearningActivity.STORAGE_LOCAL
                activity.gdrive_verified = False
                messages.error(request, f'ไม่อนุมัติให้ใช้ Google Drive Folder: {activity.gdrive_verify_message} (ระบบสลับกลับเป็นเก็บในเซิร์ฟเวอร์ชั่วคราว)')
        else:
            activity.storage_destination = LearningActivity.STORAGE_LOCAL
            activity.gdrive_folder_url = gdrive_url_raw
            parsed_fid = parse_gdrive_folder_id(gdrive_url_raw)
            if parsed_fid != activity.gdrive_folder_id:
                activity.gdrive_folder_id = parsed_fid
                activity.gdrive_verified = False
                activity.gdrive_verify_message = ''
        # ตัวเลือก "พิจารณาแบบกลุ่ม" และ "พิจารณาตรวจสอบ" ใช้เฉพาะในหน้าตรวจสอบการส่งงานเท่านั้น (ไม่มีในหน้ากำหนดคะแนน/สร้างใบงาน)
        activity.require_group_complete = False
        activity.require_material_check = False
        activity.group_source_activity = None

        if request.POST.get('clear_worksheet_pdf') == '1' and activity.worksheet_pdf:
            activity.worksheet_pdf.delete(save=False)
            activity.worksheet_pdf = None
        if request.FILES.get('worksheet_pdf'):
            activity.worksheet_pdf = request.FILES['worksheet_pdf']
        activity.save()

        # Tab 2: เฉลยใบงานแต่ละข้อ
        existing_ans_map = {a.id: a for a in activity.answer_key_items.all()}
        ans_slots = request.POST.getlist('ans_slot')
        kept_ids = []
        now = timezone.now()
        for seq_idx, slot in enumerate(ans_slots, start=1):
            raw_id = (request.POST.get(f'ans_id_{slot}') or '').strip()
            ans_title = (request.POST.get(f'ans_title_{slot}') or f'ข้อที่ {seq_idx}').strip()[:200]
            ans_html = (request.POST.get(f'ans_html_{slot}') or '').strip()
            ans_pdf_mode = (request.POST.get(f'ans_pdf_mode_{slot}') or LearningActivity.PDF_EMBED).strip()
            if ans_pdf_mode not in (LearningActivity.PDF_EMBED, LearningActivity.PDF_DOWNLOAD):
                ans_pdf_mode = LearningActivity.PDF_EMBED
            ans_rev = (request.POST.get(f'ans_revealed_{slot}') == '1')
            ans_clear_pdf = (request.POST.get(f'ans_clear_pdf_{slot}') == '1')
            ans_pdf_file = request.FILES.get(f'ans_pdf_file_{slot}')
            raw_order = (request.POST.get(f'ans_order_{slot}') or '').strip()
            order_no = int(raw_order) if raw_order.isdigit() and int(raw_order) > 0 else seq_idx

            if not ans_title and not ans_html and not ans_pdf_file and not raw_id:
                continue

            item = existing_ans_map.get(int(raw_id)) if raw_id.isdigit() else None
            if not item:
                item = ActivityAnswerKeyItem(activity=activity)
            item.order_no = order_no
            item.title = ans_title
            item.content_html = ans_html
            item.pdf_display_mode = ans_pdf_mode
            if ans_rev and not item.is_revealed:
                item.revealed_at = now
            elif not ans_rev:
                item.revealed_at = None
            item.is_revealed = ans_rev
            if ans_clear_pdf and item.pdf_file:
                item.pdf_file.delete(save=False)
                item.pdf_file = None
            if ans_pdf_file:
                item.pdf_file = ans_pdf_file
            item.save()
            kept_ids.append(item.id)

        if request.POST.get('ans_form_present') == '1':
            activity.answer_key_items.exclude(id__in=kept_ids).delete()

        # Tab 3: กำหนดคะแนนแต่ละครั้งที่ตรวจ (ค่าเริ่มต้นอัตโนมัติ 3 รอบ และรอบที่ x เป็นต้นไปได้คะแนนรอบสุดท้าย)
        if request.POST.get('tiers_form_present') == '1':
            tier_rounds = request.POST.getlist('tier_round')
            tier_scores = request.POST.getlist('tier_score')
            tier_labels = request.POST.getlist('tier_label')
            activity.grading_tiers.all().delete()
            seen_rounds = set()
            for r_raw, s_raw, lbl_raw in zip(tier_rounds, tier_scores, tier_labels):
                r_str = (r_raw or '').strip()
                s_str = (s_raw or '').strip()
                if not s_str:
                    continue
                try:
                    r_no = int(r_str) if r_str.isdigit() else (len(seen_rounds) + 1)
                    sc_val = max(0.0, float(s_str))
                except ValueError:
                    continue
                if r_no < 1 or r_no in seen_rounds:
                    continue
                seen_rounds.add(r_no)
                ActivityGradingTier.objects.create(
                    activity=activity,
                    round_no=r_no,
                    score=sc_val,
                    label=(lbl_raw or f'ตรวจครั้งที่ {r_no}').strip()[:150],
                )
        if not activity.grading_tiers.exists():
            ensure_activity_default_grading_tiers(activity)
        remember_activity_max_score(activity.max_score)

        if old_period:
            recalculate_period_students_scores(old_period)
        if new_period and new_period != old_period:
            recalculate_period_students_scores(new_period)

        messages.success(request, f'บันทึกใบงาน "{activity.title}" เรียบร้อยแล้ว')
        return redirect(f'/teacher/activities/{activity.id}/edit/?tab={active_tab}')

    default_max_score = get_default_activity_max_score()
    answer_items = list(activity.answer_key_items.all()) if activity else []
    grading_tiers = list(activity.grading_tiers.all()) if activity else []
    tiers_are_auto_preview = False
    if not grading_tiers:
        grading_tiers = build_default_grading_tiers(activity.max_score if activity else default_max_score)
        tiers_are_auto_preview = True

    selected_target_rooms = set(activity.get_target_rooms_list()) if activity else set()
    grade_groups_dict = {}
    for c in Classroom.objects.all().order_by('grade_level', 'code'):
        g_key = str(c.grade_level or 'อื่นๆ')
        grade_groups_dict.setdefault(g_key, []).append(c)
    classrooms_by_grade = sorted(grade_groups_dict.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 99, x[0]))

    act_year = (activity.academic_year if activity else '') or get_current_academic_year()

    return render(request, 'teacher/activity_editor.html', {
        'activity': activity,
        'active_tab': active_tab,
        'answer_items': answer_items,
        'grading_tiers': grading_tiers,
        'tiers_are_auto_preview': tiers_are_auto_preview,
        'default_max_score': default_max_score,
        'classrooms_by_grade': classrooms_by_grade,
        'selected_target_rooms': selected_target_rooms,
        'target_all_rooms': (not selected_target_rooms),
        'open_periods': ScorePeriod.objects.filter(academic_year=act_year, is_finalized=False).order_by('order_no', 'id'),
        'all_periods': ScorePeriod.objects.filter(academic_year=act_year).order_by('order_no', 'id'),
        'activity_types': LearningActivity.TYPE_CHOICES,
        'storage_choices': LearningActivity.STORAGE_CHOICES,
        'default_academic_year': get_current_academic_year(),
        'default_gdrive_url': SystemSetting.get().gdrive_default_folder_url,
        'pdf_display_choices': LearningActivity.PDF_DISPLAY_CHOICES,
        'due_date_value': (
            timezone.localtime(activity.due_date).strftime('%Y-%m-%dT%H:%M')
            if activity and activity.due_date else ''
        ),
    })


@teacher_required
def teacher_presentations_view(request):
    """เมนูใหม่: นำเสนอใบงาน (เลือกห้อง/กิจกรรม, สุ่มระบุจำนวนคน, สุ่มต่อ/เริ่มใหม่, เวทีใหญ่ zoom ได้, ให้คะแนนทั้งหมด -1)"""
    classrooms = Classroom.objects.all()
    room_code = (request.GET.get('room') or request.POST.get('room') or '01').strip()
    if room_code != 'all' and room_code.isdigit():
        room_code = room_code.zfill(2)

    draw_date = _parse_check_date(request.GET.get('draw_date') or request.POST.get('draw_date'))
    draw_date_str = draw_date.isoformat()
    only_registered = (
        (request.POST.get('only_registered') if request.method == 'POST' else request.GET.get('only_registered')) == '1'
    )
    registered_student_ids = get_registered_student_ids(room_code=room_code, target_date=draw_date)

    cur_yr = get_current_academic_year()
    activities_all = list(LearningActivity.objects.filter(academic_year=cur_yr).select_related('score_period').order_by('id'))
    act_id_param = request.GET.get('activity_id') or request.POST.get('activity_id')
    selected_activity = (
        next((a for a in activities_all if str(a.id) == str(act_id_param)), None)
        if act_id_param
        else (activities_all[0] if activities_all else None)
    )

    draws_day_qs = ActivityPresentationDraw.objects.filter(draw_date=draw_date, activity__academic_year=cur_yr)
    if room_code != 'all':
        draws_day_qs = draws_day_qs.filter(student__room_code=room_code)
    max_db_round = draws_day_qs.aggregate(m=Max('round_no'))['m'] or 1
    sess_round_key = f'pres_active_round_{room_code}_{draw_date_str}'
    active_round = max(max_db_round, int(request.session.get(sess_round_key) or 1))

    def _pres_redirect(aid=None, sel_draw=None):
        target_aid = aid if aid is not None else (selected_activity.id if selected_activity else '')
        qs = f'room={room_code}&activity_id={target_aid}&draw_date={draw_date_str}'
        if only_registered:
            qs += '&only_registered=1'
        if sel_draw:
            qs += f'&selected_draw_id={sel_draw}'
        return redirect(f'/teacher/presentations/?{qs}')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reset_presentation_round':
            if draws_day_qs.filter(round_no=active_round).exists():
                active_round = max_db_round + 1
            request.session[sess_round_key] = active_round
            messages.info(request, f'เริ่มรอบการสุ่มใหม่ (รอบที่ {active_round}) เรียบร้อยแล้ว นักเรียนที่ส่งงานทุกคนพร้อมถูกสุ่มอีกครั้ง')
            return _pres_redirect()

        elif action == 'random_present':
            if not selected_activity:
                messages.error(request, 'ไม่พบกิจกรรมที่เลือก')
                return _pres_redirect()

            draw_mode = (request.POST.get('draw_mode') or 'continue').strip().lower()
            if draw_mode not in ('continue', 'reset'):
                draw_mode = 'continue'

            if draw_mode == 'reset':
                if draws_day_qs.filter(round_no=max_db_round).exists():
                    target_round = max_db_round + 1
                else:
                    target_round = active_round
                request.session[sess_round_key] = target_round
                excluded_sids = set()
            else:
                target_round = active_round
                excluded_sids = set(
                    draws_day_qs.filter(round_no=target_round).values_list('student_id', flat=True)
                )

            subs_qs = ActivitySubmission.objects.filter(activity=selected_activity).select_related(
                'student', 'student__classroom'
            )
            if room_code != 'all':
                subs_qs = subs_qs.filter(student__room_code=room_code)
            pool = [
                s for s in subs_qs
                if s.student_id not in excluded_sids
                and (not only_registered or s.student_id in registered_student_ids)
            ]

            raw_count = (request.POST.get('draw_count') or '').strip()
            try:
                draw_count = int(raw_count)
            except (TypeError, ValueError):
                messages.error(request, 'กรุณาระบุจำนวนคนที่จะสุ่มเป็นตัวเลขจำนวนเต็ม')
                return _pres_redirect()

            if draw_count < 1:
                messages.error(request, 'กรุณาระบุจำนวนคนที่จะสุ่มตั้งแต่ 1 คนขึ้นไป')
                return _pres_redirect()

            if draw_count > len(pool):
                reg_note = 'ที่ลงทะเบียนแล้วและ' if only_registered else ''
                messages.error(
                    request,
                    f'จำนวนที่ระบุ ({draw_count} คน) เกินจำนวนนักเรียน{reg_note}ส่งงานและคงเหลือให้สุ่ม ({len(pool)} คน)'
                )
                return _pres_redirect()

            picked_subs = random.sample(pool, draw_count)
            cur_max_order = draws_day_qs.filter(round_no=target_round).aggregate(m=Max('order_index'))['m'] or 0
            next_order = cur_max_order + 1
            first_new_draw = None
            for sub in picked_subs:
                d_obj = ActivityPresentationDraw.objects.create(
                    activity=selected_activity,
                    submission=sub,
                    student=sub.student,
                    classroom=sub.student.classroom,
                    draw_date=draw_date,
                    round_no=target_round,
                    order_index=next_order,
                )
                if not first_new_draw:
                    first_new_draw = d_obj
                sub.is_presented = True
                sub.save(update_fields=['is_presented'])
                next_order += 1

            names = ', '.join(f'#{s.student.class_number} {s.student.first_name}' for s in picked_subs)
            mode_txt = 'เริ่มสุ่มใหม่' if draw_mode == 'reset' else 'สุ่มต่อจากเดิม'
            messages.success(request, f'{mode_txt} ({len(picked_subs)} คน · รอบที่ {target_round}): {names}')
            return _pres_redirect(selected_activity.id, first_new_draw.id if first_new_draw else None)

        elif action == 'remove_draw':
            draw_obj = get_object_or_404(ActivityPresentationDraw, pk=request.POST.get('draw_id'))
            reason = (request.POST.get('removed_reason') or 'ไม่อยู่ในห้อง / ข้ามรอบนำเสนอ').strip()
            draw_obj.is_removed = True
            draw_obj.removed_reason = reason[:255]
            draw_obj.save(update_fields=['is_removed', 'removed_reason'])
            messages.info(request, f'นำรายชื่อ {draw_obj.student.get_full_name()} ออกจากรายการนำเสนอแล้ว ({draw_obj.removed_reason})')
            return _pres_redirect(draw_obj.activity_id)

        elif action == 'restore_draw':
            draw_obj = get_object_or_404(ActivityPresentationDraw, pk=request.POST.get('draw_id'))
            draw_obj.is_removed = False
            draw_obj.removed_reason = ''
            draw_obj.save(update_fields=['is_removed', 'removed_reason'])
            messages.success(request, f'คืนรายชื่อ {draw_obj.student.get_full_name()} กลับเข้าสู่รายการนำเสนอแล้ว')
            return _pres_redirect(draw_obj.activity_id, draw_obj.id)

        elif action == 'grade_draw':
            draw_obj = get_object_or_404(ActivityPresentationDraw, pk=request.POST.get('draw_id'))
            target_period = draw_obj.activity.score_period or get_active_score_period()
            if target_period.is_finalized:
                messages.error(request, f'ช่วงคะแนน "{target_period.name}" สรุปคะแนนและล็อกไว้แล้ว ไม่สามารถให้คะแนนเพิ่มได้')
                return _pres_redirect(draw_obj.activity_id, draw_obj.id)
            score_val = float(request.POST.get('score_awarded') or draw_obj.activity.max_score or 5)
            feedback = (request.POST.get('feedback') or '').strip()
            draw_obj.is_graded = True
            draw_obj.score_awarded = score_val
            draw_obj.feedback = feedback[:300]
            draw_obj.save(update_fields=['is_graded', 'score_awarded', 'feedback'])
            if draw_obj.submission:
                draw_obj.submission.score_awarded = score_val
                draw_obj.submission.feedback = feedback[:300]
                draw_obj.submission.is_presented = True
                draw_obj.submission.save(update_fields=['score_awarded', 'feedback', 'is_presented'])
            ScoreLog.objects.create(
                student=draw_obj.student,
                score_period=target_period,
                academic_year=(getattr(target_period, 'academic_year', '') or draw_obj.activity.academic_year or get_current_academic_year()),
                points=score_val,
                reason=f'คะแนนนำเสนอใบงาน "{draw_obj.activity.title}"' + (f' ({feedback})' if feedback else ''),
                source_module='activity',
                awarded_by=request.user,
            )
            messages.success(request, f'ให้คะแนน {draw_obj.student.get_full_name()} (+{score_val:g} คะแนน) เรียบร้อยแล้ว')
            return _pres_redirect(draw_obj.activity_id, draw_obj.id)

        elif action == 'grade_all_submitted':
            if not selected_activity:
                messages.error(request, 'ไม่พบกิจกรรมที่เลือก')
                return _pres_redirect()
            try:
                batch, graded_subs = bulk_grade_activity_submissions(
                    selected_activity, room_code=room_code, teacher_user=request.user
                )
                if batch is None:
                    messages.warning(request, 'นักเรียนที่ส่งใบงานในกิจกรรมนี้ได้รับคะแนนครบทุกคนแล้ว (ไม่มีรายการใหม่ให้คะแนน)')
                else:
                    messages.success(
                        request,
                        f'ให้คะแนนทั้งหมดครั้งที่ {batch.batch_no} สำเร็จ! มอบ +{batch.score_given:g} คะแนน ให้ผู้ส่งงาน {batch.graded_count} คน'
                    )
            except ValueError as e:
                messages.error(request, str(e))
            return _pres_redirect(selected_activity.id, request.POST.get('selected_draw_id'))

        elif action == 'toggle_answer_item':
            if selected_activity:
                item = get_object_or_404(ActivityAnswerKeyItem, pk=request.POST.get('item_id'), activity=selected_activity)
                item.is_revealed = not item.is_revealed
                item.revealed_at = timezone.now() if item.is_revealed else None
                item.save(update_fields=['is_revealed', 'revealed_at'])
                state_lbl = 'เปิดเฉลย' if item.is_revealed else 'ปิดเฉลย'
                messages.success(request, f'{state_lbl} "{item.title}" เรียบร้อยแล้ว')
            return _pres_redirect()

        elif action == 'reveal_all_answers':
            if selected_activity:
                selected_activity.answer_key_items.update(is_revealed=True, revealed_at=timezone.now())
                messages.success(request, f'เปิดเฉลยทั้งหมดของ "{selected_activity.title}" เรียบร้อยแล้ว')
            return _pres_redirect()

        elif action == 'hide_all_answers':
            if selected_activity:
                selected_activity.answer_key_items.update(is_revealed=False)
                messages.info(request, f'ซ่อนเฉลยทั้งหมดของ "{selected_activity.title}" แล้ว')
            return _pres_redirect()

    students_in_room_qs = get_active_students_qs(cur_yr)
    if room_code != 'all':
        students_in_room_qs = students_in_room_qs.filter(room_code=room_code)
    total_room_students = students_in_room_qs.count()

    activity_cards = []
    for act in activities_all:
        sub_q = ActivitySubmission.objects.filter(activity=act)
        if room_code != 'all':
            sub_q = sub_q.filter(student__room_code=room_code)
        act.room_submitted_count = sub_q.count()
        act.room_total_students = total_room_students
        activity_cards.append(act)

    submissions_for_selected = []
    submitted_student_ids = set()
    ungraded_submitted_count = 0
    if selected_activity:
        sub_qs = ActivitySubmission.objects.filter(activity=selected_activity).select_related('student', 'student__classroom')
        if room_code != 'all':
            sub_qs = sub_qs.filter(student__room_code=room_code)
        submissions_for_selected = list(sub_qs.order_by('student__room_code', 'student__class_number'))
        submitted_student_ids = {s.student_id for s in submissions_for_selected}
        ungraded_submitted_count = sum(1 for s in submissions_for_selected if s.score_awarded is None)

    draws_round_qs = draws_day_qs.filter(round_no=active_round).select_related(
        'activity', 'submission', 'student', 'student__classroom'
    )
    active_draws = list(draws_round_qs.filter(is_removed=False).order_by('order_index', 'id'))
    removed_draws = list(draws_round_qs.filter(is_removed=True).order_by('-order_index', '-id'))

    drawn_in_round_ids = set(draws_day_qs.filter(round_no=active_round).values_list('student_id', flat=True))
    other_activity_drawn_count = (
        draws_day_qs.filter(round_no=active_round).exclude(activity=selected_activity).count()
        if selected_activity else 0
    )
    eligible_continue_count = sum(1 for sid in submitted_student_ids if sid not in drawn_in_round_ids)
    eligible_reset_count = len(submitted_student_ids)
    reg_submitted_ids = submitted_student_ids & registered_student_ids
    registered_submitted_count = len(reg_submitted_ids)
    eligible_reg_continue_count = sum(1 for sid in reg_submitted_ids if sid not in drawn_in_round_ids)
    eligible_reg_reset_count = registered_submitted_count

    sel_draw_id = request.GET.get('selected_draw_id')
    selected_draw = None
    if sel_draw_id and active_draws:
        selected_draw = next((d for d in active_draws if str(d.id) == str(sel_draw_id)), None)
    if not selected_draw and active_draws:
        if selected_activity:
            selected_draw = next((d for d in active_draws if d.activity_id == selected_activity.id), None)
        if not selected_draw:
            selected_draw = active_draws[0]

    image_exts = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')
    act_index_map = {a.id: idx for idx, a in enumerate(activities_all, start=1)}

    def _short_activity_label(act):
        m = re.search(r'กิจกรรมที่\s*\d+', act.title or '')
        if m:
            return re.sub(r'\s+', ' ', m.group(0)).strip()
        return f'กิจกรรมที่ {act_index_map.get(act.id, act.id)}'

    def _clean_student_answer(sub):
        if not sub:
            return ''
        txt = (sub.work_text or sub.work_title or '').strip()
        txt = re.sub(r'\s*(?:—\s*โดย|\(โดย)[^)]*\)?\s*$', '', txt).strip()
        return txt

    presenters_payload = []
    for d in active_draws:
        sub = d.submission
        file_url = ''
        is_image = False
        file_name = ''
        if sub and sub.work_file:
            file_url = sub.work_file.url
            file_name = sub.work_file.name or ''
            is_image = file_name.lower().endswith(image_exts)
        short_act_lbl = _short_activity_label(d.activity)
        short_fullname = f'{d.student.first_name} {d.student.last_name}'.strip() or d.student.username
        answer_text = _clean_student_answer(sub)
        d.short_activity_label = short_act_lbl
        d.student_short_fullname = short_fullname
        presenters_payload.append({
            'draw_id': d.id,
            'order_index': d.order_index,
            'activity_id': d.activity_id,
            'short_activity_label': short_act_lbl,
            'activity_title': d.activity.title,
            'activity_type': d.activity.activity_type,
            'max_score': float(d.activity.max_score or 5),
            'student_name': short_fullname,
            'student_id': d.student.student_id or d.student.username,
            'class_number': d.student.class_number,
            'room_display': d.student.room_display,
            'avatar_url': d.student.avatar_url or '',
            'avatar_initial': d.student.avatar_initial or '',
            'answer_text': answer_text,
            'file_url': file_url,
            'file_name': file_name,
            'is_image': is_image,
            'is_graded': d.is_graded or (sub is not None and sub.score_awarded is not None),
            'score_awarded': d.score_awarded if d.is_graded else (sub.score_awarded if sub else None),
            'feedback': d.feedback or (sub.feedback if sub else ''),
            'period_finalized': bool(d.activity.score_period and d.activity.score_period.is_finalized),
        })

    selected_draw_is_image = False
    selected_draw_answer_text = ''
    selected_draw_short_act_label = ''
    if selected_draw:
        selected_draw_short_act_label = getattr(selected_draw, 'short_activity_label', _short_activity_label(selected_draw.activity))
        selected_draw_answer_text = _clean_student_answer(selected_draw.submission)
        if selected_draw.submission and selected_draw.submission.work_file:
            selected_draw_is_image = (selected_draw.submission.work_file.name or '').lower().endswith(image_exts)

    bulk_grade_info = get_next_activity_bulk_grade_info(selected_activity, room_code=room_code)
    selected_room_label = 'ทุกห้อง' if room_code == 'all' else (f'ม.4/{int(room_code)}' if room_code.isdigit() else room_code)
    answer_key_items = list(selected_activity.answer_key_items.all()) if selected_activity else []
    revealed_answers_count = sum(1 for it in answer_key_items if it.is_revealed)
    answer_items_payload = [
        {
            'id': it.id,
            'order_no': it.order_no,
            'title': it.title,
            'is_revealed': it.is_revealed,
            'content_html': it.content_html,
            'pdf_url': it.pdf_file.url if it.pdf_file else '',
            'pdf_display_mode': it.pdf_display_mode,
        }
        for it in answer_key_items
    ]

    return render(request, 'teacher/presentations.html', {
        'classrooms': classrooms,
        'selected_room': room_code,
        'selected_room_label': selected_room_label,
        'draw_date_str': draw_date_str,
        'active_round': active_round,
        'activities': activity_cards,
        'selected_activity': selected_activity,
        'total_room_students': total_room_students,
        'submitted_count': len(submissions_for_selected),
        'only_registered': only_registered,
        'registered_submitted_count': registered_submitted_count,
        'ungraded_submitted_count': ungraded_submitted_count,
        'eligible_continue_count': eligible_continue_count,
        'eligible_reset_count': eligible_reset_count,
        'eligible_reg_continue_count': eligible_reg_continue_count,
        'eligible_reg_reset_count': eligible_reg_reset_count,
        'has_other_activity_draws': other_activity_drawn_count > 0,
        'other_activity_drawn_count': other_activity_drawn_count,
        'active_draws': active_draws,
        'removed_draws': removed_draws,
        'selected_draw': selected_draw,
        'selected_draw_is_image': selected_draw_is_image,
        'selected_draw_answer_text': selected_draw_answer_text,
        'selected_draw_short_act_label': selected_draw_short_act_label,
        'presenters_json': json.dumps(presenters_payload, ensure_ascii=False).replace('</', '<\\/'),
        'answer_key_items': answer_key_items,
        'revealed_answers_count': revealed_answers_count,
        'answer_items_json': json.dumps(answer_items_payload, ensure_ascii=False).replace('</', '<\\/'),
        'bulk_grade_info': bulk_grade_info,
    })


def _build_filtered_submissions_data(request):
    """รวบรวมข้อมูลสำหรับหน้า ตรวจสอบการส่งงาน & Export ตาม filter ห้อง/ช่วงคะแนน/กิจกรรม/สถานะ/ค้นหา"""
    params = request.GET if request.method == 'GET' else request.POST
    raw_room = (params.get('room') or '01').strip()
    if raw_room.lower() == 'all':
        room_filter = ''
    else:
        room_filter = normalize_sorting_room_filter(raw_room)

    cur_yr = get_current_academic_year()
    period_id = (params.get('period_id') or 'all').strip()
    activity_id = (params.get('activity_id') or 'all').strip()
    status_filter = (params.get('status') or 'all').strip()
    search_q = (params.get('q') or '').strip()

    students_qs = get_active_students_qs(cur_yr).select_related('classroom')
    if room_filter.startswith('grade:'):
        students_qs = students_qs.filter(classroom__grade_level=room_filter.split(':', 1)[1])
    elif room_filter:
        students_qs = students_qs.filter(room_code=room_filter)
    if search_q:
        q_filter = Q(first_name__icontains=search_q) | Q(last_name__icontains=search_q) | Q(student_id__icontains=search_q)
        if search_q.isdigit():
            q_filter |= Q(class_number=int(search_q))
        students_qs = students_qs.filter(q_filter)
    students = list(students_qs.order_by('room_code', 'class_number', 'student_id'))

    acts_qs = LearningActivity.objects.filter(academic_year=cur_yr).select_related('score_period').order_by('id')
    if period_id != 'all' and period_id.isdigit():
        acts_qs = acts_qs.filter(score_period_id=int(period_id))
    if activity_id != 'all' and activity_id.isdigit():
        acts_qs = acts_qs.filter(id=int(activity_id))
    filtered_activities = list(acts_qs)

    subs_map = {}
    if students and filtered_activities:
        for s in ActivitySubmission.objects.filter(
            activity__in=filtered_activities, student__in=students
        ).select_related('activity', 'activity__score_period', 'student'):
            subs_map[(s.activity_id, s.student_id)] = s

    now = timezone.now()
    ext_map = {}
    if students and filtered_activities:
        for ex in (
            ActivityExtensionRequest.objects.filter(
                activity__in=filtered_activities,
                student__in=students,
                status=ActivityExtensionRequest.STATUS_APPROVED,
                approved_until__gte=now,
            ).order_by('approved_until', 'id')
        ):
            ext_map[(ex.activity_id, ex.student_id)] = ex

    rows = []
    ungraded_subs = []
    submitted_count = 0
    ungraded_count = 0
    graded_count = 0
    missing_count = 0
    late_count = 0
    round_info_cache = {}
    rc_summary_arg = room_filter if (room_filter and not room_filter.startswith('grade:')) else 'all'

    def _get_round_info(act_obj, rc_code):
        key = (act_obj.id, rc_code or 'all')
        if key not in round_info_cache:
            round_info_cache[key] = get_next_activity_bulk_grade_info(act_obj, rc_code or 'all')
        return round_info_cache[key]

    activity_round_summaries = []
    for act in filtered_activities:
        r_info = _get_round_info(act, rc_summary_arg)
        activity_round_summaries.append({
            'activity': act,
            'room_code': r_info['room_code'],
            'graded_rounds_count': r_info['graded_rounds_count'],
            'next_round_no': r_info['batch_no'],
            'next_score': r_info['next_score'],
            'last_tier_round': r_info.get('last_tier_round'),
        })

    total_applicable_rows = 0
    for st in students:
        for act in filtered_activities:
            if not act.applies_to_room(st.room_code):
                continue
            total_applicable_rows += 1
            sub = subs_map.get((act.id, st.id))
            active_ext = ext_map.get((act.id, st.id))
            r_info = _get_round_info(act, st.room_code or 'all')
            graded_round_no = None
            is_late = False
            if sub:
                submitted_count += 1
                if act.due_date and sub.submitted_at and sub.submitted_at > act.due_date:
                    is_late = True
                    late_count += 1
                if sub.score_awarded is None:
                    ungraded_count += 1
                    status_code = 'ungraded'
                    status_label = 'ส่งแล้ว (ยังไม่ตรวจ)' if not is_late else 'ส่งช้า (ยังไม่ตรวจ)'
                else:
                    graded_count += 1
                    status_code = 'graded'
                    status_label = f'ตรวจแล้ว ({sub.score_awarded:g})' if not is_late else f'ส่งช้า-ตรวจแล้ว ({sub.score_awarded:g})'
                    if sub.graded_round_no:
                        graded_round_no = sub.graded_round_no
                    else:
                        m_round = re.search(r'ครั้งที่\s*(\d+)', sub.feedback or '')
                        graded_round_no = int(m_round.group(1)) if m_round else 1
            else:
                missing_count += 1
                if act.due_date and now > act.due_date:
                    is_late = True
                    late_count += 1
                    status_code = 'missing'
                    status_label = 'เลยกำหนดยังไม่ส่ง'
                else:
                    status_code = 'missing'
                    status_label = 'ยังไม่ส่ง'

            if status_filter == 'submitted' and not sub:
                continue
            if status_filter == 'ungraded' and not (sub and sub.score_awarded is None):
                continue
            if status_filter == 'graded' and not (sub and sub.score_awarded is not None):
                continue
            if status_filter == 'missing' and sub is not None:
                continue
            if status_filter == 'late' and not is_late:
                continue

            if sub and sub.score_awarded is None:
                ungraded_subs.append(sub)

            rows.append({
                'student': st,
                'activity': act,
                'submission': sub,
                'active_ext': active_ext,
                'is_late': is_late,
                'status_code': status_code,
                'status_label': status_label,
                'graded_round_no': graded_round_no,
                'activity_graded_rounds': r_info['graded_rounds_count'],
                'activity_next_round': r_info['batch_no'],
                'activity_next_score': r_info['next_score'],
            })

    return {
        'room_filter': room_filter or 'all',
        'room_filter_label': get_sorting_room_filter_label(room_filter),
        'period_id': period_id,
        'activity_id': activity_id,
        'status_filter': status_filter,
        'search_q': search_q,
        'rows': rows,
        'ungraded_subs': ungraded_subs,
        'activity_round_summaries': activity_round_summaries,
        'summary': {
            'total_rows': total_applicable_rows,
            'filtered_rows': len(rows),
            'submitted_count': submitted_count,
            'ungraded_count': ungraded_count,
            'graded_count': graded_count,
            'missing_count': missing_count,
            'late_count': late_count,
        },
    }


@teacher_required
def teacher_submissions_view(request):
    """เมนูใหม่: ตรวจสอบการส่งงาน (กรองชั้น/ห้อง/ช่วงคะแนน/กิจกรรม/สถานะ + ให้คะแนนที่ยังไม่ตรวจลดตามสัดส่วน + อนุมัติคำร้อง + Export)"""
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        back_qs = (request.POST.get('back_qs') or '').strip()
        redir_url = f'/teacher/submissions/?{back_qs}' if back_qs else '/teacher/submissions/'

        if action == 'approve_extension':
            ext_req = get_object_or_404(ActivityExtensionRequest, pk=request.POST.get('request_id'))
            custom_until = _parse_datetime_local(request.POST.get('approved_until'))
            note = (request.POST.get('teacher_note') or '').strip()
            approve_extension_request(ext_req, request.user, approved_until=custom_until, teacher_note=note)
            until_str = timezone.localtime(ext_req.approved_until).strftime('%d/%m/%Y %H:%M')
            messages.success(
                request,
                f'อนุมัติให้ {ext_req.student.get_full_name()} ส่ง "{ext_req.activity.title}" เพิ่มได้ถึง {until_str}'
            )
            return redirect(redir_url)

        elif action == 'reject_extension':
            ext_req = get_object_or_404(ActivityExtensionRequest, pk=request.POST.get('request_id'))
            note = (request.POST.get('teacher_note') or '').strip()
            reject_extension_request(ext_req, request.user, teacher_note=note)
            messages.info(request, f'ปฏิเสธคำร้องของ {ext_req.student.get_full_name()} แล้ว')
            return redirect(redir_url)

        elif action == 'grade_single_submission':
            sub = get_object_or_404(ActivitySubmission, pk=request.POST.get('submission_id'))
            target_period = sub.activity.score_period or get_active_score_period()
            if target_period.is_finalized:
                messages.error(request, f'ช่วงคะแนน "{target_period.name}" สรุปคะแนนและล็อกไว้แล้ว')
                return redirect(redir_url)
            r_info = get_next_activity_bulk_grade_info(sub.activity, sub.student.room_code or 'all')
            raw_sc = (request.POST.get('score_awarded') or '').strip()
            score_val = float(raw_sc) if raw_sc else float(r_info['next_score'])
            feedback = (request.POST.get('feedback') or '').strip()
            sub.score_awarded = score_val
            if not sub.graded_round_no:
                sub.graded_round_no = r_info['batch_no']
            sub.feedback = feedback[:300]
            sub.save(update_fields=['score_awarded', 'graded_round_no', 'feedback'])
            ScoreLog.objects.create(
                student=sub.student,
                score_period=target_period,
                academic_year=(getattr(target_period, 'academic_year', '') or sub.activity.academic_year or get_current_academic_year()),
                points=score_val,
                reason=f'ให้คะแนนผลงาน "{sub.activity.title}"' + (f' ({feedback})' if feedback else ''),
                source_module='activity',
                awarded_by=request.user,
            )
            messages.success(request, f'ให้คะแนน {sub.student.get_full_name()} (+{score_val:g} คะแนน) เรียบร้อยแล้ว')
            return redirect(redir_url)

        elif action == 'grade_all_ungraded':
            fdata = _build_filtered_submissions_data(request)
            ungraded = fdata['ungraded_subs']
            if not ungraded:
                messages.warning(request, 'ไม่มีรายการส่งงานที่รอการตรวจในตัวกรองปัจจุบัน')
                return redirect(redir_url)
            grade_mode = (request.POST.get('grade_mode') or 'tier_auto').strip()
            pct_val = float(request.POST.get('percent_value') or 80.0)
            override_sc = None
            if request.POST.get('use_override_score') == '1':
                raw_ov = (request.POST.get('override_score') or '').strip()
                if raw_ov != '':
                    try:
                        override_sc = max(0.0, float(raw_ov))
                    except ValueError:
                        override_sc = None
            consider_grp = (request.POST.get('consider_group') == '1')
            consider_mat = (request.POST.get('consider_material_check') == '1')
            grp_src_id = (request.POST.get('group_source_activity_id') or '').strip() or None
            c_date = _parse_check_date(request.POST.get('check_date'))
            rc_arg = fdata['room_filter'] if fdata['room_filter'] != 'all' and not fdata['room_filter'].startswith('grade:') else 'all'
            res = bulk_grade_filtered_ungraded_submissions(
                ungraded,
                room_code=rc_arg,
                grade_mode=grade_mode,
                percent_value=pct_val,
                teacher_user=request.user,
                override_score=override_sc,
                consider_group=consider_grp,
                consider_material_check=consider_mat,
                group_source_activity_id=grp_src_id,
                check_date=c_date,
            )
            skip_notes = []
            if res.get('skipped_group_incomplete_count'):
                skip_notes.append(f'ข้ามกลุ่มที่สมาชิกส่งไม่ครบ {res["skipped_group_incomplete_count"]} คน')
            if res.get('skipped_unchecked_count'):
                skip_notes.append(f'ข้ามผู้ที่ยังไม่ได้รับการตรวจสอบ {res["skipped_unchecked_count"]} คน')
            if res.get('skipped_locked_count'):
                skip_notes.append(f'ช่วงคะแนนถูกล็อก {res["skipped_locked_count"]} คน')
            skip_txt = f' ({", ".join(skip_notes)})' if skip_notes else ''
            if res['graded_count'] > 0:
                messages.success(
                    request,
                    f'ให้คะแนนผลงานที่ยังไม่ตรวจจำนวน {res["graded_count"]} รายการเรียบร้อยแล้ว ({"; ".join(res["details"][:3])}){skip_txt}'
                )
            elif skip_notes:
                messages.warning(request, f'ไม่มีรายการที่ผ่านเงื่อนไขให้คะแนนในรอบนี้: {", ".join(skip_notes)}')
            else:
                messages.warning(request, 'ไม่สามารถให้คะแนนได้เนื่องจากช่วงคะแนนที่เกี่ยวข้องถูกสรุปล็อกไว้แล้ว')
            return redirect(redir_url)

    cur_yr = get_current_academic_year()
    fdata = _build_filtered_submissions_data(request)
    pending_reqs = list(
        ActivityExtensionRequest.objects.filter(
            status=ActivityExtensionRequest.STATUS_PENDING,
            activity__academic_year=cur_yr,
        )
        .select_related('activity', 'student', 'student__classroom')
        .order_by('-created_at')
    )
    recent_exts = list(
        ActivityExtensionRequest.objects.filter(activity__academic_year=cur_yr)
        .exclude(status=ActivityExtensionRequest.STATUS_PENDING)
        .select_related('activity', 'student', 'reviewed_by')
        .order_by('-reviewed_at')[:15]
    )
    grade_levels = [
        g for g in Classroom.objects.values_list('grade_level', flat=True).distinct().order_by('grade_level') if g
    ]
    room_for_groups = fdata['room_filter'] if fdata['room_filter'] != 'all' and not fdata['room_filter'].startswith('grade:') else 'all'
    group_source_activities = get_group_source_activities_for_room(room_for_groups)
    if not group_source_activities and room_for_groups != 'all':
        group_source_activities = get_group_source_activities_for_room('all')
    has_existing_groups = bool(group_source_activities)

    selected_activity_obj = None
    if fdata['activity_id'].isdigit():
        selected_activity_obj = LearningActivity.objects.filter(pk=int(fdata['activity_id'])).first()
    if selected_activity_obj:
        selected_full_score = int(round(selected_activity_obj.max_score or 5))
        matched_summary = next((s for s in fdata['activity_round_summaries'] if s['activity'].id == selected_activity_obj.id), None)
        selected_next_score = matched_summary['next_score'] if matched_summary else get_default_activity_max_score()
    elif fdata['activity_round_summaries']:
        first_sum = fdata['activity_round_summaries'][0]
        selected_full_score = int(round(first_sum['activity'].max_score or 5))
        selected_next_score = first_sum['next_score']
    else:
        selected_full_score = int(round(get_default_activity_max_score()))
        selected_next_score = float(selected_full_score)

    return render(request, 'teacher/submissions.html', {
        'classrooms': Classroom.objects.all(),
        'grade_levels': grade_levels,
        'periods': ScorePeriod.objects.filter(academic_year=cur_yr).order_by('order_no', 'id'),
        'all_activities': LearningActivity.objects.filter(academic_year=cur_yr).select_related('score_period').order_by('id'),
        'pending_requests': pending_reqs,
        'recent_extensions': recent_exts,
        'rows': fdata['rows'],
        'activity_round_summaries': fdata['activity_round_summaries'],
        'summary': fdata['summary'],
        'has_existing_groups': has_existing_groups,
        'group_source_activities': group_source_activities,
        'selected_activity_obj': selected_activity_obj,
        'selected_full_score': selected_full_score,
        'selected_next_score': selected_next_score,
        'selected_next_score_int': int(round(selected_next_score)),
        'selected_check_date': (request.GET.get('check_date') or timezone.localdate().strftime('%Y-%m-%d')),
        'selected_room': fdata['room_filter'],
        'selected_room_label': fdata['room_filter_label'],
        'selected_period_id': fdata['period_id'],
        'selected_activity_id': fdata['activity_id'],
        'selected_status': fdata['status_filter'],
        'search_q': fdata['search_q'],
        'current_qs': request.GET.urlencode() or f'room={fdata["room_filter"]}',
        'default_two_days': (timezone.localtime(timezone.now() + timedelta(days=2))).strftime('%Y-%m-%dT%H:%M'),
    })


@teacher_required
def teacher_submissions_export_view(request):
    """Export รายการส่งงานและคะแนนเป็น Excel (.xlsx) หรือ CSV ตามตัวกรองห้องหรือทั้งหมด"""
    fdata = _build_filtered_submissions_data(request)
    fmt = (request.GET.get('fmt') or 'xlsx').strip().lower()
    rows = fdata['rows']
    headers = ['ห้อง', 'เลขที่', 'รหัสประจำตัว', 'ชื่อ-นามสกุล', 'ช่วงคะแนน', 'ใบงาน/กิจกรรม', 'สถานะ', 'ครั้งที่ตรวจ', 'เวลาส่ง', 'คะแนนที่ได้', 'คะแนนเต็ม', 'ข้อเสนอแนะ']

    def _to_export_row(item):
        st = item['student']
        act = item['activity']
        sub = item['submission']
        sub_time = timezone.localtime(sub.submitted_at).strftime('%Y-%m-%d %H:%M') if sub and sub.submitted_at else '-'
        sc_got = f'{sub.score_awarded:g}' if sub and sub.score_awarded is not None else '-'
        round_str = f'ครั้งที่ {item["graded_round_no"]}' if item.get('graded_round_no') else f'รอตรวจ (รอบถัดไป: ครั้งที่ {item["activity_next_round"]})'
        fb = sub.feedback if sub else ''
        return [
            st.room_display,
            st.class_number,
            st.student_id or st.username,
            st.get_full_name(),
            act.score_period.name if act.score_period else '-',
            act.title,
            item['status_label'],
            round_str,
            sub_time,
            sc_got,
            f'{act.max_score:g}',
            fb,
        ]

    room_tag = fdata['room_filter'].replace(':', '_')
    if fmt == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="COMSC4_Submissions_{room_tag}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(headers)
        for item in rows:
            writer.writerow(_to_export_row(item))
        return response

    sheets = []
    if fdata['room_filter'] in ('all', '') or fdata['room_filter'].startswith('grade:'):
        all_export = [_to_export_row(it) for it in rows]
        sheets.append(('สรุปรวมทั้งหมด', headers, all_export))
        by_room = {}
        for it in rows:
            by_room.setdefault(it['student'].room_display, []).append(_to_export_row(it))
        for r_name, r_rows in by_room.items():
            sheets.append((r_name, headers, r_rows))
    else:
        sheets.append((fdata['room_filter_label'], headers, [_to_export_row(it) for it in rows]))

    wb = build_workbook(sheets)
    return workbook_response(wb, f'COMSC4_Submissions_{room_tag}.xlsx')


def _parse_check_date(raw_date):
    if raw_date:
        try:
            return datetime.strptime(str(raw_date).strip(), '%Y-%m-%d').date()
        except ValueError:
            pass
    return timezone.localdate()


def _serialize_student_seat_card(st, online_map, material_map, att_rec=None):
    m = material_map.get(st.id)
    sess_obj = online_map.get(st.id)
    online_info = None
    if sess_obj:
        online_info = {
            'ip': sess_obj.ip_address or '-',
            'device': sess_obj.device_info or '-',
            'login_at': sess_obj.created_at.strftime('%H:%M:%S'),
            'last_active': sess_obj.last_active.strftime('%H:%M:%S'),
        }
    return {
        'id': st.id,
        'student_id': st.student_id or st.username,
        'name': st.get_full_name(),
        'first_name': st.first_name or st.username,
        'class_number': st.class_number,
        'avatar': st.avatar_url,
        'initial': st.avatar_initial,
        'total_score': round(float(st.total_score or 0.0), 2),
        'is_online': bool(sess_obj),
        'online_info': online_info,
        'is_checked_in': bool(att_rec),
        'has_book': bool(m and m.has_book),
        'has_notebook': bool(m and m.has_notebook),
        'note': (m.note if m else ''),
    }


def _fmt_local_time(dt):
    if not dt:
        return '-'
    return (timezone.localtime(dt) if timezone.is_aware(dt) else dt).strftime('%H:%M:%S')


def _build_classroom_seating_snapshot(classroom, current_session=None, check_date=None):
    """สร้างข้อมูลแผนผังที่นั่ง สถานะ Login สด รายชื่อขาด/ไม่ลงทะเบียน และคะแนนย้อนหลังประจำวันของห้องเรียน"""
    cur_yr = get_current_academic_year()
    cdate = check_date or timezone.localdate()
    students = list(
        get_active_students_qs(cur_yr, room_code=classroom.code).order_by('class_number', 'student_id')
    )
    students_by_no = {int(st.class_number): st for st in students if st.class_number}

    online_map = {}
    for sess_obj in (
        UserSession.objects.filter(
            user__role=User.ROLE_STUDENT, user__academic_year=cur_yr, user__is_active=True,
            user__room_code=classroom.code, is_active=True
        )
        .select_related('user')
        .order_by('-last_active')
    ):
        online_map.setdefault(sess_obj.user_id, sess_obj)

    material_map = {
        m.student_id: m
        for m in StudentMaterialCheck.objects.filter(academic_year=cur_yr, classroom=classroom, check_date=cdate)
    }

    att_by_seat = {}
    att_by_student = {}
    if current_session and current_session.classroom_id == classroom.id:
        for r in current_session.records.select_related('student'):
            att_by_seat[f'{r.seat_row}_{r.seat_col}'] = r
            att_by_student[r.student_id] = r

    valid_class_nos = {int(st.class_number) for st in students if st.class_number}
    disabled_seats = classroom.get_disabled_seats_set()
    custom_labels = classroom.get_custom_labels_dict()
    seat_numbers = classroom.get_seat_numbers_dict(valid_class_numbers=valid_class_nos)
    seat_rows = classroom.get_effective_rows()
    seat_cols = classroom.get_effective_cols()

    seats_data = {}
    for r in range(1, seat_rows + 1):
        for c in range(1, seat_cols + 1):
            key = f'{r}_{c}'
            assigned_no = seat_numbers.get(key)
            att_rec = att_by_seat.get(key)
            st = att_rec.student if att_rec else (students_by_no.get(assigned_no) if assigned_no else None)
            seats_data[key] = {
                'seat_key': key,
                'row': r,
                'col': c,
                'disabled': key in disabled_seats,
                'label': custom_labels.get(key, f'แถว {r}, ที่ {c}'),
                'custom_label': custom_labels.get(key, ''),
                'assigned_number': assigned_no,
                'attendance_record_id': att_rec.id if att_rec else None,
                'student': _serialize_student_seat_card(st, online_map, material_map, att_rec=att_rec) if st else None,
            }

    online_students = []
    absent_students = []
    registered_students = []
    for st in students:
        att_r = att_by_student.get(st.id)
        sess_obj = online_map.get(st.id)
        assigned_rc = classroom.get_seat_for_class_number(st.class_number)
        sr = att_r.seat_row if att_r else (assigned_rc[0] if assigned_rc else 0)
        sc = att_r.seat_col if att_r else (assigned_rc[1] if assigned_rc else 0)
        if sess_obj:
            item = _serialize_student_seat_card(st, online_map, material_map, att_rec=att_r)
            item['seat_row'] = sr
            item['seat_col'] = sc
            online_students.append(item)
        if att_r or sess_obj:
            reg_dt = att_r.checked_at if att_r else sess_obj.created_at
            reg_source = 'checkin' if att_r else 'login'
            registered_students.append({
                'id': st.id,
                'class_number': st.class_number,
                'student_id': st.student_id or st.username,
                'name': st.get_full_name(),
                'first_name': st.first_name or st.username,
                'seat_row': sr,
                'seat_col': sc,
                'total_score': round(float(st.total_score or 0.0), 2),
                'registered_at': _fmt_local_time(reg_dt),
                'registered_ts': reg_dt.timestamp(),
                'reg_source': reg_source,
            })
        else:
            absent_students.append({
                'id': st.id,
                'class_number': st.class_number,
                'student_id': st.student_id or st.username,
                'name': st.get_full_name(),
                'first_name': st.first_name or st.username,
                'seat_row': sr,
                'seat_col': sc,
                'total_score': round(float(st.total_score or 0.0), 2),
            })

    registered_students.sort(key=lambda x: (x['registered_ts'], x['class_number'], x['id']))

    batch_prefix = f'ATT{classroom.code}_{cdate.strftime("%Y%m%d")}_'
    batch_logs = list(
        ScoreLog.objects.filter(
            academic_year=cur_yr,
            student__room_code=classroom.code,
            source_module='attendance',
            reason__contains=f'[BATCH:{batch_prefix}',
        )
        .select_related('student')
        .order_by('-created_at', 'student__class_number')
    )
    batches_map = {}
    batch_order = []
    for log in batch_logs:
        m_batch = re.search(r'\[BATCH:([^\]]+)\]', log.reason or '')
        if not m_batch:
            continue
        bid = m_batch.group(1)
        if bid not in batches_map:
            clean_label = re.sub(r'\s*\[BATCH:[^\]]+\]', '', log.reason or '').strip()
            batches_map[bid] = {
                'batch_id': bid,
                'label': clean_label,
                'points': round(float(log.points), 2),
                'student_count': 0,
                'students': [],
                'created_at': _fmt_local_time(log.created_at),
                '_seen_ids': set(),
            }
            batch_order.append(bid)
        b_entry = batches_map[bid]
        s = log.student
        if s.id not in b_entry['_seen_ids']:
            b_entry['_seen_ids'].add(s.id)
            b_entry['students'].append({
                'id': s.id,
                'class_number': s.class_number,
                'first_name': s.first_name or s.username,
            })
            b_entry['student_count'] += 1

    recent_score_batches = []
    for bid in batch_order:
        b = batches_map[bid]
        b.pop('_seen_ids', None)
        recent_score_batches.append(b)

    book_count = sum(1 for st in students if material_map.get(st.id) and material_map[st.id].has_book)
    notebook_count = sum(1 for st in students if material_map.get(st.id) and material_map[st.id].has_notebook)

    return {
        'classroom_id': classroom.id,
        'classroom_code': classroom.code,
        'classroom_name': classroom.name,
        'lab_room_id': classroom.lab_room_id,
        'lab_room_name': classroom.lab_room.name if classroom.lab_room_id else '',
        'check_date': cdate.isoformat(),
        'board_position': classroom.get_effective_board_position(),
        'seats': seats_data,
        'online_students': online_students,
        'absent_students': absent_students,
        'registered_students': registered_students,
        'recent_score_batches': recent_score_batches,
        'summary': {
            'total_students': len(students),
            'online_count': len(online_students),
            'checked_in_count': len(att_by_student),
            'absent_count': len(absent_students),
            'registered_count': len(registered_students),
            'book_count': book_count,
            'notebook_count': notebook_count,
        },
    }


@teacher_required
def teacher_attendance_view(request):
    """ฝั่งครู: จัดการแผนผังห้องกลาง (134, 135, 136...), ผูกห้องเรียนกับแผนผังห้อง, จัดที่นั่งรายห้อง และเช็คหนังสือ/สมุด"""
    auto_close_expired_attendance_sessions()
    if LabRoomLayout.objects.count() == 0:
        ensure_default_lab_rooms()
    classrooms = Classroom.objects.select_related('lab_room').all()
    lab_rooms = LabRoomLayout.objects.all()
    check_date = _parse_check_date(request.GET.get('check_date') or request.POST.get('check_date'))
    check_date_str = check_date.isoformat()

    if request.method == 'POST':
        action = request.POST.get('action')
        sess_id = request.POST.get('session_id') or ''
        cid = request.POST.get('classroom_id') or ''
        tab = request.POST.get('tab') or ''
        lab_id = request.POST.get('lab_room_id') or ''
        qs_parts = []
        if sess_id:
            qs_parts.append(f'session_id={sess_id}')
        elif cid:
            qs_parts.append(f'classroom_id={cid}')
        if check_date_str:
            qs_parts.append(f'check_date={check_date_str}')
        if tab:
            qs_parts.append(f'tab={tab}')
        if lab_id and tab == 'lab':
            qs_parts.append(f'lab_room_id={lab_id}')
        redir_qs = ('?' + '&'.join(qs_parts)) if qs_parts else ''

        if action == 'create_session':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            code = (request.POST.get('check_in_code') or f'DS{random.randint(1000, 9999)}').strip().upper()
            AttendanceSession.objects.filter(classroom=room, is_open=True).update(is_open=False)
            sess = AttendanceSession.objects.create(
                classroom=room,
                title=(request.POST.get('title') or f'คาบเรียน {room.name}').strip(),
                academic_year=get_current_academic_year(),
                check_in_code=code,
                allowed_ip_prefix=(request.POST.get('allowed_ip_prefix') or '').strip(),
                attendance_points=float(request.POST.get('attendance_points') or 1.0),
                is_open=True,
                opened_at=timezone.now(),
                auto_close_minutes=25,
                created_by=request.user,
            )
            messages.success(request, f'เปิดเช็คชื่อห้อง {room.name} (ปิดอัตโนมัติใน 25 นาที) เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?session_id={sess.id}')
        elif action == 'toggle_session':
            sess = get_object_or_404(AttendanceSession, pk=request.POST.get('session_id'))
            sess.is_open = not sess.is_open
            if sess.is_open:
                sess.opened_at = timezone.now()
                sess.auto_close_minutes = int(sess.auto_close_minutes or 25)
                sess.save(update_fields=['is_open', 'opened_at', 'auto_close_minutes'])
                messages.success(request, f'เปิดเช็คชื่อห้อง {sess.classroom.name} (ปิดอัตโนมัติใน 25 นาที) แล้ว')
            else:
                sess.save(update_fields=['is_open'])
                messages.info(request, f'ปิดเช็คชื่อห้อง {sess.classroom.name} เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?session_id={sess.id}')
        elif action == 'assign_classroom_lab_room':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('lab_room_id'))
            room.lab_room = lab
            room.seat_rows = lab.seat_rows
            room.seat_cols = lab.seat_cols
            room.board_position = lab.board_position
            room.save(update_fields=['lab_room', 'seat_rows', 'seat_cols', 'board_position'])
            messages.success(
                request,
                f'กำหนดให้ {room.name} ใช้แผนผัง "{lab.name}" ({lab.seat_rows}×{lab.seat_cols}) เรียบร้อยแล้ว'
            )
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'bulk_assign_classroom_lab_rooms':
            lab_map = {str(l.id): l for l in LabRoomLayout.objects.all()}
            updated_count = 0
            for room in classrooms:
                raw_lab_id = (request.POST.get(f'class_lab_{room.id}') or '').strip()
                if raw_lab_id in lab_map:
                    lab = lab_map[raw_lab_id]
                    if (
                        room.lab_room_id != lab.id
                        or room.seat_rows != lab.seat_rows
                        or room.seat_cols != lab.seat_cols
                        or room.board_position != lab.board_position
                    ):
                        room.lab_room = lab
                        room.seat_rows = lab.seat_rows
                        room.seat_cols = lab.seat_cols
                        room.board_position = lab.board_position
                        room.save(update_fields=['lab_room', 'seat_rows', 'seat_cols', 'board_position'])
                        updated_count += 1
            messages.success(
                request,
                f'บันทึกการจับคู่ห้องเรียนกับห้องคอมเรียบร้อยแล้ว (อัปเดต {updated_count} ห้อง)'
                if updated_count else 'บันทึกการจับคู่ห้องเรียนกับห้องคอมเรียบร้อยแล้ว'
            )
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'create_lab_room':
            lab_name = (request.POST.get('lab_name') or '').strip()
            if not lab_name:
                messages.error(request, 'กรุณาระบุชื่อแผนผังห้อง (เช่น ห้อง 137)')
                return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab')
            existing = LabRoomLayout.objects.filter(name=lab_name).first()
            if existing:
                messages.error(request, f'มีแผนผังชื่อ "{lab_name}" อยู่ในระบบแล้ว')
                return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={existing.id}')
            rows = max(1, min(15, int(request.POST.get('seat_rows') or 6)))
            cols = max(1, min(15, int(request.POST.get('seat_cols') or 7)))
            bpos = request.POST.get('board_position') or LabRoomLayout.BOARD_BOTTOM
            if bpos not in (LabRoomLayout.BOARD_BOTTOM, LabRoomLayout.BOARD_TOP):
                bpos = LabRoomLayout.BOARD_BOTTOM
            r_order = 'bottom_to_top' if bpos == LabRoomLayout.BOARD_BOTTOM else 'top_to_bottom'
            num_map = build_auto_seat_numbers(
                rows, cols, list(range(1, rows * cols + 1)), direction='col_front_to_back'
            )
            cfg = {
                'machine_labels': {k: f'{v:02d}' for k, v in num_map.items()},
                'disabled_seats': [],
                'row_order': r_order,
                'auto_direction': 'col_front_to_back',
            }
            new_lab = LabRoomLayout.objects.create(
                name=lab_name[:100],
                seat_rows=rows,
                seat_cols=cols,
                board_position=bpos,
                layout_config=json.dumps(cfg),
            )
            messages.success(request, f'สร้างแผนผัง "{new_lab.name}" ({rows}×{cols}) พร้อมเลขเครื่องแนวตั้งเริ่มต้นเรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={new_lab.id}')
        elif action == 'save_lab_room_design':
            lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('lab_room_id'))
            new_name = (request.POST.get('lab_name') or '').strip()
            if new_name and new_name != lab.name:
                if LabRoomLayout.objects.filter(name=new_name).exclude(pk=lab.pk).exists():
                    messages.error(request, f'มีแผนผังชื่อ "{new_name}" อยู่ในระบบแล้ว')
                    return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
                lab.name = new_name[:100]
            new_rows = max(1, min(15, int(request.POST.get('seat_rows') or lab.seat_rows or 6)))
            new_cols = max(1, min(15, int(request.POST.get('seat_cols') or lab.seat_cols or 7)))
            bpos = request.POST.get('board_position') or lab.board_position or LabRoomLayout.BOARD_BOTTOM
            if bpos not in (LabRoomLayout.BOARD_BOTTOM, LabRoomLayout.BOARD_TOP):
                bpos = LabRoomLayout.BOARD_BOTTOM
            lab.seat_rows = new_rows
            lab.seat_cols = new_cols
            lab.board_position = bpos
            ld = lab.get_layout_dict()
            row_order = request.POST.get('row_order') or (
                'bottom_to_top' if bpos == LabRoomLayout.BOARD_BOTTOM else 'top_to_bottom'
            )
            ld['row_order'] = row_order if row_order in ('bottom_to_top', 'top_to_bottom') else 'bottom_to_top'
            auto_dir = (request.POST.get('auto_direction') or '').strip()
            if auto_dir in VALID_AUTO_SEAT_DIRECTIONS:
                ld['auto_direction'] = auto_dir
            machine_prefix = (request.POST.get('machine_prefix') or '').strip()[:8]
            machine_start_raw = (request.POST.get('machine_start') or '1').strip()
            ld['machine_prefix'] = machine_prefix
            ld['machine_start'] = int(machine_start_raw) if machine_start_raw.isdigit() else 1
            if request.POST.get('resize_only') != '1':
                new_disabled = []
                new_labels = {}
                for r in range(1, new_rows + 1):
                    for c in range(1, new_cols + 1):
                        key = f'{r}_{c}'
                        if request.POST.get(f'seat_dis_{key}') == '1':
                            new_disabled.append(key)
                        raw_lbl = (request.POST.get(f'seat_lbl_{key}') or '').strip()
                        if raw_lbl:
                            new_labels[key] = raw_lbl[:30]
                ld['disabled_seats'] = sorted(new_disabled)
                ld['machine_labels'] = new_labels
            lab.layout_config = json.dumps(ld)
            lab.save()
            lab.sync_linked_classrooms()
            messages.success(request, f'บันทึกแผนผัง "{lab.name}" ({lab.seat_rows}×{lab.seat_cols}) เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
        elif action == 'copy_lab_room_layout':
            lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('lab_room_id'))
            src_lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('source_lab_room_id'))
            if lab.pk == src_lab.pk:
                messages.warning(request, 'กรุณาเลือกแผนผังห้องต้นทางอื่นที่ไม่ใช่ห้องปัจจุบัน')
                return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
            src_ld = src_lab.get_layout_dict()
            new_ld = dict(src_ld)
            new_ld['disabled_seats'] = sorted([str(x) for x in src_lab.get_disabled_seats_set()])
            new_ld['machine_labels'] = dict(src_lab.get_machine_labels_dict())
            lab.seat_rows = src_lab.seat_rows
            lab.seat_cols = src_lab.seat_cols
            lab.board_position = src_lab.board_position
            lab.layout_config = json.dumps(new_ld)
            lab.save()
            lab.sync_linked_classrooms()
            messages.success(
                request,
                f'คัดลอกแผนผังจาก "{src_lab.name}" มายัง "{lab.name}" ({lab.seat_rows}×{lab.seat_cols}) เรียบร้อยแล้ว'
            )
            return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
        elif action == 'quick_toggle_lab_board':
            lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('lab_room_id'))
            new_pos = LabRoomLayout.BOARD_TOP if lab.board_position == LabRoomLayout.BOARD_BOTTOM else LabRoomLayout.BOARD_BOTTOM
            lab.board_position = new_pos
            ld = lab.get_layout_dict()
            ld['row_order'] = 'bottom_to_top' if new_pos == LabRoomLayout.BOARD_BOTTOM else 'top_to_bottom'
            lab.layout_config = json.dumps(ld)
            lab.save()
            lab.sync_linked_classrooms()
            messages.success(request, f'สลับกระดานดำ "{lab.name}" เป็น "{lab.get_board_position_display()}" เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
        elif action == 'delete_lab_room':
            lab = get_object_or_404(LabRoomLayout, pk=request.POST.get('lab_room_id'))
            if LabRoomLayout.objects.count() <= 1:
                messages.error(request, 'ต้องมีแผนผังห้องอย่างน้อย 1 ห้องในระบบ')
                return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={lab.id}')
            fallback_lab = LabRoomLayout.objects.exclude(pk=lab.pk).order_by('id').first()
            lab.classrooms.update(
                lab_room=fallback_lab,
                seat_rows=fallback_lab.seat_rows,
                seat_cols=fallback_lab.seat_cols,
                board_position=fallback_lab.board_position,
            )
            deleted_name = lab.name
            lab.delete()
            messages.info(request, f'ลบแผนผัง "{deleted_name}" แล้ว (ห้องเรียนที่เคยใช้ถูกย้ายไป "{fallback_lab.name}")')
            return redirect(f'/teacher/attendance/?classroom_id={cid}&tab=lab&lab_room_id={fallback_lab.id}')
        elif action == 'reset_class_seats_to_lab_default':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            ld = room.get_layout_dict()
            overrides = ld.get('seat_overrides_by_lab')
            if isinstance(overrides, dict) and room.lab_room_id:
                overrides.pop(str(room.lab_room_id), None)
                ld['seat_overrides_by_lab'] = overrides
            ld.pop('seat_numbers', None)
            room.layout_config = json.dumps(ld)
            room.save(update_fields=['layout_config'])
            lab_name = room.lab_room.name if room.lab_room_id else 'ผังหลัก'
            messages.success(request, f'รีเซ็ตที่นั่งห้อง {room.name} ให้ตรงตามเลขเครื่องของ {lab_name} เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'update_classroom_layout':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            new_rows = max(1, min(15, int(request.POST.get('seat_rows') or room.get_effective_rows() or 6)))
            new_cols = max(1, min(15, int(request.POST.get('seat_cols') or room.get_effective_cols() or 7)))
            new_bpos = request.POST.get('board_position', Classroom.BOARD_BOTTOM)
            row_order = request.POST.get('row_order') or (
                'bottom_to_top' if new_bpos == Classroom.BOARD_BOTTOM else 'top_to_bottom'
            )
            disabled_raw = (request.POST.get('disabled_seats_csv') or '').strip()
            disabled_list = [x.strip() for x in disabled_raw.split(',') if x.strip()]
            clean_order = row_order if row_order in ('bottom_to_top', 'top_to_bottom') else 'bottom_to_top'
            if room.lab_room_id:
                lab = room.lab_room
                lab.seat_rows = new_rows
                lab.seat_cols = new_cols
                lab.board_position = new_bpos
                lab_ld = lab.get_layout_dict()
                lab_ld['disabled_seats'] = disabled_list
                lab_ld['row_order'] = clean_order
                lab.layout_config = json.dumps(lab_ld)
                lab.save()
                lab.sync_linked_classrooms()
                room.refresh_from_db()
            else:
                room.seat_rows = new_rows
                room.seat_cols = new_cols
                room.board_position = new_bpos
                layout_dict = room.get_layout_dict()
                layout_dict['disabled_seats'] = disabled_list
                layout_dict['row_order'] = clean_order
                room.layout_config = json.dumps(layout_dict)
                room.save()
            messages.success(
                request,
                f'บันทึกแผนผังห้องเรียน {room.name} ({room.seat_rows} แถว × {room.seat_cols} คอลัมน์, '
                f'ตำแหน่งกระดานดำ: {room.get_board_position_display()}) เรียบร้อยแล้ว'
            )
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'save_seat_design_bulk':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            eff_rows = room.get_effective_rows()
            eff_cols = room.get_effective_cols()
            has_lbl_or_dis = any(k.startswith(('seat_lbl_', 'seat_dis_')) for k in request.POST.keys())
            new_labels = {}
            if has_lbl_or_dis:
                new_disabled = []
                for r in range(1, eff_rows + 1):
                    for c in range(1, eff_cols + 1):
                        key = f'{r}_{c}'
                        if request.POST.get(f'seat_dis_{key}') == '1':
                            new_disabled.append(key)
                        raw_lbl = (request.POST.get(f'seat_lbl_{key}') or '').strip()
                        if raw_lbl:
                            new_labels[key] = raw_lbl[:30]
                disabled_set = set(new_disabled)
                if room.lab_room_id:
                    lab = room.lab_room
                    lab_ld = lab.get_layout_dict()
                    lab_ld['disabled_seats'] = sorted(new_disabled)
                    lab_ld['machine_labels'] = new_labels
                    lab.layout_config = json.dumps(lab_ld)
                    lab.save(update_fields=['layout_config'])
            else:
                disabled_set = room.get_disabled_seats_set()
                new_disabled = sorted(list(disabled_set))

            new_seat_nums = {}
            for r in range(1, eff_rows + 1):
                for c in range(1, eff_cols + 1):
                    key = f'{r}_{c}'
                    raw_num = (request.POST.get(f'seat_num_{key}') or '').strip()
                    if key not in disabled_set and raw_num.isdigit() and int(raw_num) > 0:
                        new_seat_nums[key] = int(raw_num)

            layout_dict = room.get_layout_dict()
            if has_lbl_or_dis and not room.lab_room_id:
                layout_dict['custom_labels'] = new_labels
                layout_dict['disabled_seats'] = sorted(new_disabled)
            if room.lab_room_id:
                overrides = layout_dict.get('seat_overrides_by_lab')
                if not isinstance(overrides, dict):
                    overrides = {}
                overrides[str(room.lab_room_id)] = new_seat_nums
                layout_dict['seat_overrides_by_lab'] = overrides
            layout_dict['seat_numbers'] = new_seat_nums
            auto_dir = (request.POST.get('auto_direction') or '').strip()
            if auto_dir in VALID_AUTO_SEAT_DIRECTIONS:
                layout_dict['auto_direction'] = auto_dir
            room.layout_config = json.dumps(layout_dict)
            room.save(update_fields=['layout_config'])
            unassigned_cnt = max(0, (eff_rows * eff_cols) - len(new_disabled) - len(new_seat_nums))
            messages.success(
                request,
                f'บันทึกที่นั่งนักเรียนห้อง {room.name} เรียบร้อยแล้ว '
                f'(ระบุเลขที่ {len(new_seat_nums)} ที่ · ว่าง {unassigned_cnt} ที่)'
            )
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'auto_assign_seat_numbers':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            eff_rows = room.get_effective_rows()
            eff_cols = room.get_effective_cols()
            disabled_set = room.get_disabled_seats_set()
            direction = (request.POST.get('direction') or 'col_front_to_back').strip()
            if direction not in VALID_AUTO_SEAT_DIRECTIONS:
                direction = 'col_front_to_back'
            per_col = (request.POST.get('per_col') or '').strip()
            st_nos = list(
                get_active_students_qs(get_current_academic_year(), room_code=room.code, class_number__gt=0)
                .order_by('class_number')
                .values_list('class_number', flat=True)
            )
            if not st_nos:
                enabled_count = max(0, (eff_rows * eff_cols) - len(disabled_set))
                st_nos = list(range(1, enabled_count + 1))
            new_seat_nums = build_auto_seat_numbers(
                eff_rows,
                eff_cols,
                st_nos,
                disabled_seats=disabled_set,
                direction=direction,
                per_col=per_col or None,
            )
            layout_dict = room.get_layout_dict()
            if room.lab_room_id:
                overrides = layout_dict.get('seat_overrides_by_lab')
                if not isinstance(overrides, dict):
                    overrides = {}
                overrides[str(room.lab_room_id)] = new_seat_nums
                layout_dict['seat_overrides_by_lab'] = overrides
            layout_dict['seat_numbers'] = new_seat_nums
            layout_dict['auto_direction'] = direction
            room.layout_config = json.dumps(layout_dict)
            room.save(update_fields=['layout_config'])
            messages.success(request, f'เรียงเลขที่ผู้นั่งอัตโนมัติในห้อง {room.name} จำนวน {len(new_seat_nums)} ที่นั่ง (ที่นั่งที่เหลือเว้นว่าง) เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'copy_classroom_layout':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            src_room = get_object_or_404(Classroom, pk=request.POST.get('source_classroom_id'))
            if room.pk == src_room.pk:
                messages.warning(request, 'กรุณาเลือกห้องต้นทางอื่นที่ไม่ใช่ห้องปัจจุบัน')
                return redirect(f'/teacher/attendance/{redir_qs}')
            copy_numbers_mode = (request.POST.get('copy_numbers_mode') or 'auto_vertical').strip()
            per_col = (request.POST.get('per_col') or '').strip()
            copy_classroom_layout(src_room, room, copy_numbers_mode=copy_numbers_mode, per_col=per_col)
            messages.success(
                request,
                f'คัดลอกโครงสร้างผังจาก {src_room.name} มายัง {room.name} ({room.seat_rows} แถว × {room.seat_cols} คอลัมน์) เรียบร้อยแล้ว'
            )
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'copy_layout_to_rooms':
            src_room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            target_ids = request.POST.getlist('target_classroom_ids')
            copy_numbers_mode = (request.POST.get('copy_numbers_mode') or 'auto_vertical').strip()
            per_col = (request.POST.get('per_col') or '').strip()
            copied_names = []
            for tid in target_ids:
                t_room = Classroom.objects.filter(pk=tid).first()
                if t_room and t_room.pk != src_room.pk:
                    copy_classroom_layout(src_room, t_room, copy_numbers_mode=copy_numbers_mode, per_col=per_col)
                    copied_names.append(t_room.name)
            if copied_names:
                messages.success(
                    request,
                    f'คัดลอกผังจาก {src_room.name} ไปยัง {len(copied_names)} ห้อง ({", ".join(copied_names)}) เรียบร้อยแล้ว'
                )
            else:
                messages.warning(request, 'กรุณาเลือกห้องปลายทางอย่างน้อย 1 ห้อง')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'clear_seat_numbers':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            layout_dict = room.get_layout_dict()
            if room.lab_room_id:
                overrides = layout_dict.get('seat_overrides_by_lab')
                if not isinstance(overrides, dict):
                    overrides = {}
                overrides[str(room.lab_room_id)] = {}
                layout_dict['seat_overrides_by_lab'] = overrides
            layout_dict['seat_numbers'] = {}
            room.layout_config = json.dumps(layout_dict)
            room.save(update_fields=['layout_config'])
            messages.info(request, f'ล้างการระบุเลขที่ผู้นั่งทั้งหมดของห้อง {room.name} แล้ว (ทุกเครื่องเป็นแบบไม่ระบุเลขที่)')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'quick_toggle_board':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            if room.lab_room_id:
                lab = room.lab_room
                new_pos = LabRoomLayout.BOARD_TOP if lab.board_position == LabRoomLayout.BOARD_BOTTOM else LabRoomLayout.BOARD_BOTTOM
                lab.board_position = new_pos
                lab_ld = lab.get_layout_dict()
                lab_ld['row_order'] = 'bottom_to_top' if new_pos == LabRoomLayout.BOARD_BOTTOM else 'top_to_bottom'
                lab.layout_config = json.dumps(lab_ld)
                lab.save()
                lab.sync_linked_classrooms()
                room.refresh_from_db()
            else:
                new_pos = Classroom.BOARD_TOP if room.board_position == Classroom.BOARD_BOTTOM else Classroom.BOARD_BOTTOM
                room.board_position = new_pos
                layout_dict = room.get_layout_dict()
                layout_dict['row_order'] = 'bottom_to_top' if new_pos == Classroom.BOARD_BOTTOM else 'top_to_bottom'
                room.layout_config = json.dumps(layout_dict)
                room.save()
            messages.success(request, f'ย้ายกระดานดำห้อง {room.name} เป็น "{room.get_board_position_display()}" เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'toggle_single_seat':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            seat_key = (request.POST.get('seat_key') or '').strip()
            if room.lab_room_id:
                lab = room.lab_room
                lab_ld = lab.get_layout_dict()
                disabled_set = set(str(x) for x in lab_ld.get('disabled_seats', []))
                if seat_key in disabled_set:
                    disabled_set.remove(seat_key)
                else:
                    disabled_set.add(seat_key)
                lab_ld['disabled_seats'] = sorted(list(disabled_set))
                lab.layout_config = json.dumps(lab_ld)
                lab.save()
            else:
                layout_dict = room.get_layout_dict()
                disabled_set = set(str(x) for x in layout_dict.get('disabled_seats', []))
                if seat_key in disabled_set:
                    disabled_set.remove(seat_key)
                else:
                    disabled_set.add(seat_key)
                layout_dict['disabled_seats'] = sorted(list(disabled_set))
                room.layout_config = json.dumps(layout_dict)
                room.save()
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'set_seat_label':
            room = get_object_or_404(Classroom, pk=request.POST.get('classroom_id'))
            seat_key = (request.POST.get('seat_key') or '').strip()
            label_text = (request.POST.get('seat_label') or '').strip()
            if room.lab_room_id:
                lab = room.lab_room
                lab_ld = lab.get_layout_dict()
                labels_dict = lab_ld.get('machine_labels') or lab_ld.get('custom_labels') or {}
                if not isinstance(labels_dict, dict):
                    labels_dict = {}
                if label_text:
                    labels_dict[seat_key] = label_text[:30]
                else:
                    labels_dict.pop(seat_key, None)
                lab_ld['machine_labels'] = labels_dict
                lab.layout_config = json.dumps(lab_ld)
                lab.save()
            else:
                layout_dict = room.get_layout_dict()
                labels_dict = layout_dict.get('custom_labels') or {}
                if not isinstance(labels_dict, dict):
                    labels_dict = {}
                if label_text:
                    labels_dict[seat_key] = label_text[:30]
                else:
                    labels_dict.pop(seat_key, None)
                layout_dict['custom_labels'] = labels_dict
                room.layout_config = json.dumps(layout_dict)
                room.save()
            messages.success(request, f'อัปเดตป้ายชื่อที่นั่ง {seat_key} เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/{redir_qs}')
        elif action == 'move_student_seat':
            sess = get_object_or_404(AttendanceSession, pk=request.POST.get('session_id'))
            rec = get_object_or_404(AttendanceRecord, pk=request.POST.get('record_id'), session=sess)
            new_r = max(1, min(sess.classroom.get_effective_rows(), int(request.POST.get('new_row') or rec.seat_row)))
            new_c = max(1, min(sess.classroom.get_effective_cols(), int(request.POST.get('new_col') or rec.seat_col)))
            if f'{new_r}_{new_c}' in sess.classroom.get_disabled_seats_set():
                messages.error(request, f'ตำแหน่งแถว {new_r} คอลัมน์ {new_c} ถูกตั้งเป็นทางเดิน/ปิดที่นั่งไว้')
                return redirect(f'/teacher/attendance/?session_id={sess.id}')
            other = AttendanceRecord.objects.filter(session=sess, seat_row=new_r, seat_col=new_c).exclude(pk=rec.pk).first()
            if other:
                old_r, old_c = rec.seat_row, rec.seat_col
                other.seat_row, other.seat_col = 99, 99
                other.save(update_fields=['seat_row', 'seat_col'])
                rec.seat_row, rec.seat_col = new_r, new_c
                rec.save(update_fields=['seat_row', 'seat_col'])
                other.seat_row, other.seat_col = old_r, old_c
                other.save(update_fields=['seat_row', 'seat_col'])
            else:
                rec.seat_row, rec.seat_col = new_r, new_c
                rec.save(update_fields=['seat_row', 'seat_col'])
            messages.success(request, f'ย้ายที่นั่งของ {rec.student.get_full_name()} ไปยังแถว {new_r} ที่ {new_c} เรียบร้อยแล้ว')
            return redirect(f'/teacher/attendance/?session_id={sess.id}')

    cur_yr = get_current_academic_year()
    sessions = AttendanceSession.objects.filter(academic_year=cur_yr).select_related('classroom').all()
    sel_id = request.GET.get('session_id')
    sel_cid = request.GET.get('classroom_id')
    current_session = sessions.filter(pk=sel_id).first() if sel_id else (
        sessions.filter(classroom_id=sel_cid).first() if sel_cid else sessions.first()
    )
    edit_classroom = (
        Classroom.objects.select_related('lab_room').filter(pk=sel_cid).first() if sel_cid
        else (current_session.classroom if current_session else classrooms.first())
    )

    all_student_nos_by_room = {}
    for r_code, c_no in (
        get_active_students_qs(cur_yr, class_number__gt=0)
        .values_list('room_code', 'class_number')
    ):
        all_student_nos_by_room.setdefault(r_code, set()).add(int(c_no))

    classrooms_list = list(classrooms)
    classroom_status_map = {}
    for c in classrooms_list:
        valid_nos = all_student_nos_by_room.get(c.code, set())
        seat_nums = c.get_seat_numbers_dict(valid_class_numbers=valid_nos)
        seated_nos = set(seat_nums.values()) & valid_nos
        missing_count = len(valid_nos) - len(seated_nos)
        c.seat_total_students = len(valid_nos)
        c.seat_assigned_count = len(seated_nos)
        c.seat_missing_count = missing_count
        c.seat_is_complete = (missing_count == 0)
        classroom_status_map[c.id] = (
            c.seat_total_students,
            c.seat_assigned_count,
            c.seat_missing_count,
            c.seat_is_complete,
        )

    if edit_classroom and edit_classroom.id in classroom_status_map:
        (
            edit_classroom.seat_total_students,
            edit_classroom.seat_assigned_count,
            edit_classroom.seat_missing_count,
            edit_classroom.seat_is_complete,
        ) = classroom_status_map[edit_classroom.id]

    sel_lab_id = request.GET.get('lab_room_id')
    edit_lab_room = (
        LabRoomLayout.objects.filter(pk=sel_lab_id).first() if sel_lab_id
        else (edit_classroom.lab_room if edit_classroom and edit_classroom.lab_room_id else lab_rooms.first())
    )

    seat_map = {}
    rows_range, cols_range = list(range(6, 0, -1)), list(range(1, 8))
    disabled_seats = set()
    custom_labels = {}
    seat_numbers = {}
    board_position = 'bottom'
    row_order = 'bottom_to_top'
    disabled_seats_csv = ''
    seating_snapshot = None
    students_by_no_map = {}
    student_numbers_list = []
    auto_direction = 'col_front_to_back'
    has_custom_seat_override = False
    if edit_classroom:
        rows_range = edit_classroom.get_rows_sequence()
        cols_range = list(range(1, edit_classroom.get_effective_cols() + 1))
        disabled_seats = edit_classroom.get_disabled_seats_set()
        disabled_seats_csv = ', '.join(sorted(list(disabled_seats)))
        custom_labels = edit_classroom.get_custom_labels_dict()
        room_students = list(
            get_active_students_qs(cur_yr, room_code=edit_classroom.code)
            .order_by('class_number', 'student_id')
        )
        students_by_no_map = {
            str(st.class_number): (st.first_name or st.username)
            for st in room_students if st.class_number
        }
        student_numbers_list = [int(st.class_number) for st in room_students if st.class_number]
        seat_numbers = edit_classroom.get_seat_numbers_dict(valid_class_numbers=set(student_numbers_list))
        has_custom_seat_override = edit_classroom.has_custom_seat_override()
        board_position = edit_classroom.get_effective_board_position()
        row_order = edit_classroom.get_row_order()
        seating_snapshot = _build_classroom_seating_snapshot(edit_classroom, current_session, check_date)
        ld_dir = edit_classroom.get_layout_dict().get('auto_direction', 'col_front_to_back')
        auto_direction = ld_dir if ld_dir in VALID_AUTO_SEAT_DIRECTIONS else 'col_front_to_back'
    if current_session and edit_classroom and current_session.classroom_id == edit_classroom.id:
        for r in current_session.records.select_related('student'):
            seat_map[f'{r.seat_row}_{r.seat_col}'] = r

    lab_rows_range = list(range(6, 0, -1))
    lab_cols_range = list(range(1, 8))
    lab_disabled_seats = set()
    lab_machine_labels = {}
    lab_board_position = 'bottom'
    lab_row_order = 'bottom_to_top'
    lab_auto_direction = 'col_front_to_back'
    lab_linked_classrooms = []
    lab_machine_prefix = ''
    lab_machine_start = 1
    if edit_lab_room:
        lab_rows_range = edit_lab_room.get_rows_sequence()
        lab_cols_range = list(range(1, edit_lab_room.seat_cols + 1))
        lab_disabled_seats = edit_lab_room.get_disabled_seats_set()
        lab_machine_labels = edit_lab_room.get_machine_labels_dict()
        lab_board_position = edit_lab_room.board_position or 'bottom'
        lab_row_order = edit_lab_room.get_row_order()
        lab_ld = edit_lab_room.get_layout_dict()
        lab_ld_dir = lab_ld.get('auto_direction', 'col_front_to_back')
        lab_auto_direction = lab_ld_dir if lab_ld_dir in VALID_AUTO_SEAT_DIRECTIONS else 'col_front_to_back'
        lab_machine_prefix = (lab_ld.get('machine_prefix') or '').strip()
        lab_machine_start = int(lab_ld.get('machine_start') or 1)
        lab_linked_classrooms = list(edit_lab_room.classrooms.order_by('code').values_list('name', flat=True))

    return render(request, 'teacher/attendance.html', {
        'classrooms': classrooms_list,
        'edit_classroom': edit_classroom,
        'lab_rooms': lab_rooms,
        'edit_lab_room': edit_lab_room,
        'lab_rows_range': lab_rows_range,
        'lab_cols_range': lab_cols_range,
        'lab_disabled_seats': lab_disabled_seats,
        'lab_machine_labels': lab_machine_labels,
        'lab_board_position': lab_board_position,
        'lab_row_order': lab_row_order,
        'lab_auto_direction': lab_auto_direction,
        'lab_machine_prefix': lab_machine_prefix,
        'lab_machine_start': lab_machine_start,
        'lab_linked_classrooms': lab_linked_classrooms,
        'has_custom_seat_override': has_custom_seat_override,
        'sessions': sessions,
        'current_session': current_session,
        'current_session_remaining_seconds': (
            current_session.remaining_seconds
            if current_session and current_session.is_open and (not edit_classroom or current_session.classroom_id == edit_classroom.id)
            else 0
        ),
        'seat_map': seat_map,
        'seat_numbers': seat_numbers,
        'seating_snapshot': seating_snapshot,
        'seating_snapshot_json': json.dumps(seating_snapshot or {}),
        'students_by_no_json': json.dumps(students_by_no_map, ensure_ascii=False),
        'student_numbers_json': json.dumps(student_numbers_list),
        'auto_direction': auto_direction,
        'check_date_str': check_date_str,
        'score_rules': ScoreRule.objects.filter(is_active=True),
        'room_group_activities': get_group_source_activities_for_room(edit_classroom.code) if edit_classroom else [],
        'active_tab': request.GET.get('tab', 'live'),
        'rows_range': rows_range,
        'cols_range': cols_range,
        'disabled_seats': disabled_seats,
        'disabled_seats_csv': disabled_seats_csv,
        'custom_labels': custom_labels,
        'board_position': board_position,
        'row_order': row_order,
    })


@teacher_required
def api_classroom_seating_live(request, classroom_id):
    """คืนสถานะแผนผังที่นั่ง นักเรียนที่ Login สด และสถานะเช็คหนังสือ/สมุดแบบ Real-time"""
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    auto_close_expired_attendance_sessions(classroom)
    cur_yr = get_current_academic_year()
    sess_id = request.GET.get('session_id')
    current_session = (
        AttendanceSession.objects.filter(pk=sess_id, classroom=classroom).first()
        if sess_id
        else AttendanceSession.objects.filter(academic_year=cur_yr, classroom=classroom, is_open=True).first()
    )
    check_date = _parse_check_date(request.GET.get('check_date'))
    snapshot = _build_classroom_seating_snapshot(classroom, current_session, check_date)
    snapshot['ok'] = True
    snapshot['session_is_open'] = bool(current_session and current_session.is_open and not current_session.is_expired)
    snapshot['session_remaining_seconds'] = current_session.remaining_seconds if snapshot['session_is_open'] else 0
    return JsonResponse(snapshot)


@teacher_required
@require_POST
def api_classroom_seat_check(request, classroom_id):
    """ผู้ดูแลคลิกที่ตำแหน่งที่นั่ง (หรือรายชื่อนักเรียนที่ Login) เพื่อเช็คหนังสือ/สมุดและเพิ่มคะแนนอัตโนมัติ"""
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    check_date = _parse_check_date(request.POST.get('check_date'))
    sess_id = request.POST.get('session_id')
    current_session = AttendanceSession.objects.filter(pk=sess_id, classroom=classroom).first() if sess_id else None

    seat_row = int(request.POST.get('seat_row') or 0)
    seat_col = int(request.POST.get('seat_col') or 0)
    student_pk = request.POST.get('student_id')

    student = None
    cur_yr = get_current_academic_year()
    if student_pk and str(student_pk).isdigit():
        student = User.objects.filter(pk=int(student_pk), role=User.ROLE_STUDENT, is_active=True).first()

    if not student and seat_row and seat_col:
        if current_session:
            rec = AttendanceRecord.objects.filter(session=current_session, seat_row=seat_row, seat_col=seat_col).select_related('student').first()
            if rec:
                student = rec.student
        if not student:
            assigned_no = classroom.get_seat_numbers_dict().get(f'{seat_row}_{seat_col}')
            if assigned_no:
                student = get_active_students_qs(cur_yr, room_code=classroom.code, class_number=assigned_no).first()

    if not student:
        return JsonResponse({
            'ok': False,
            'error': 'ตำแหน่งนี้ยังไม่ได้ระบุเลขที่ผู้นั่งและยังไม่มีนักเรียนเช็คชื่อ กรุณาระบุเลขที่ในหน้าออกแบบแผนผังหรือคลิกจากรายชื่อผู้ที่ Login',
        }, status=400)

    check_type = (request.POST.get('check_type') or 'toggle_book').strip()
    note = request.POST.get('note')
    has_book_arg = None
    has_notebook_arg = None
    if check_type == 'set':
        if 'has_book' in request.POST:
            has_book_arg = request.POST.get('has_book') in ('1', 'true', 'True', 'yes')
        if 'has_notebook' in request.POST:
            has_notebook_arg = request.POST.get('has_notebook') in ('1', 'true', 'True', 'yes')

    try:
        chk = toggle_student_material_check(
            classroom=classroom,
            student=student,
            check_type=check_type,
            teacher_user=request.user,
            check_date=check_date,
            seat_row=seat_row,
            seat_col=seat_col,
            session=current_session,
            has_book=has_book_arg,
            has_notebook=has_notebook_arg,
            note=note,
        )

        extra_rule_id = request.POST.get('extra_rule_id')
        extra_msg = ''
        if extra_rule_id and str(extra_rule_id).isdigit():
            rule = ScoreRule.objects.filter(pk=int(extra_rule_id), is_active=True).first()
            if rule:
                ScoreLog.objects.create(
                    student=student,
                    rule=rule,
                    academic_year=get_current_academic_year(),
                    points=rule.signed_points,
                    reason=f'{rule.name} (ให้จากแผนผังที่นั่ง {classroom.name})',
                    source_module='manual',
                    awarded_by=request.user,
                )
                extra_msg = f' และบันทึก "{rule.name}"'
    except ValueError as ex:
        return JsonResponse({'ok': False, 'error': str(ex)}, status=400)

    student.refresh_from_db()
    snapshot = _build_classroom_seating_snapshot(classroom, current_session, check_date)
    return JsonResponse({
        'ok': True,
        'student_id': student.id,
        'student_code': student.student_id or student.username,
        'student_name': student.get_full_name(),
        'class_number': student.class_number,
        'has_book': chk.has_book,
        'has_notebook': chk.has_notebook,
        'note': chk.note,
        'total_score': round(float(student.total_score or 0.0), 2),
        'summary': snapshot['summary'],
        'seats': snapshot['seats'],
        'online_students': snapshot['online_students'],
        'message': f'อัปเดตสถานะของ {student.get_full_name()} (หนังสือ: {"✓" if chk.has_book else "✕"} · สมุด: {"✓" if chk.has_notebook else "✕"}){extra_msg} (คะแนนรวม {student.total_score:.1f})',
    })


@teacher_required
@require_POST
def api_classroom_attendance_batch_score(request, classroom_id):
    """บันทึกคะแนนกลุ่มนักเรียน (เช่น X คนสุดท้ายที่ลงทะเบียน) และยกเลิกการบันทึกคะแนนย้อนหลังได้"""
    classroom = get_object_or_404(Classroom, pk=classroom_id)
    check_date = _parse_check_date(request.POST.get('check_date'))
    sess_id = (request.POST.get('session_id') or '').strip()
    current_session = (
        AttendanceSession.objects.filter(pk=sess_id, classroom=classroom).first()
        if sess_id.isdigit()
        else AttendanceSession.objects.filter(classroom=classroom, is_open=True).first()
    )
    action = (request.POST.get('action') or 'award').strip()

    if action == 'award':
        parsed_ids = []
        for raw_val in request.POST.getlist('student_ids'):
            for part in str(raw_val).split(','):
                s = part.strip()
                if s.isdigit():
                    parsed_ids.append(int(s))
        targets = list(
            User.objects.filter(role=User.ROLE_STUDENT, room_code=classroom.code, pk__in=parsed_ids)
            .order_by('class_number', 'student_id')
        )
        if not targets:
            return JsonResponse({'ok': False, 'error': 'กรุณาเลือกนักเรียนอย่างน้อย 1 คน'}, status=400)

        rule_id = (request.POST.get('rule_id') or '').strip()
        raw_points = (request.POST.get('points') or '').strip()
        custom_reason = (request.POST.get('reason') or '').strip()
        rule = None
        if rule_id.isdigit():
            rule = ScoreRule.objects.filter(pk=int(rule_id), is_active=True).first()
        if rule is not None:
            pts = rule.signed_points
            label = custom_reason or rule.name
        else:
            rule = None
            try:
                pts = float(raw_points or -1.0)
            except (TypeError, ValueError):
                return JsonResponse({'ok': False, 'error': 'รูปแบบคะแนนไม่ถูกต้อง'}, status=400)
            if pts == 0:
                return JsonResponse({'ok': False, 'error': 'กรุณาระบุค่าคะแนนที่ไม่เป็น 0'}, status=400)
            label = custom_reason or f'ลงทะเบียนช้า ({len(targets)} คนสุดท้าย)'

        batch_id = f'ATT{classroom.code}_{check_date.strftime("%Y%m%d")}_{uuid.uuid4().hex[:6].upper()}'
        reason_text = f'{label[:160]} ({classroom.name} {check_date.strftime("%d/%m/%Y")}) [BATCH:{batch_id}]'

        apply_to_group = (request.POST.get('apply_to_group') or '1').strip() != '0'
        group_act_id = (request.POST.get('group_activity_id') or '').strip() or None
        penalty_entries = [(st, reason_text) for st in targets]
        seen_ids = {st.id for st in targets}
        group_penalty_count = 0

        if pts < 0:
            if current_session:
                AttendanceRecord.objects.filter(session=current_session, student__in=targets).update(
                    status=AttendanceRecord.STATUS_LATE
                )
            if apply_to_group:
                grp_act, st_to_grp, grp_to_members = get_room_group_activity_and_members_map(
                    classroom.code, group_activity_id=group_act_id
                )
                if grp_act:
                    for st in targets:
                        grp = st_to_grp.get(st.id)
                        if not grp:
                            continue
                        for mate in grp_to_members.get(grp.id, []):
                            if mate.id in seen_ids:
                                continue
                            seen_ids.add(mate.id)
                            mate_reason = (
                                f'{label[:105]} (ร่วมกลุ่ม {grp.name} · {grp_act.title[:35]}) '
                                f'({classroom.name} {check_date.strftime("%d/%m/%Y")}) [GROUP_LATE_PENALTY] [BATCH:{batch_id}]'
                            )
                            penalty_entries.append((mate, mate_reason))
                            group_penalty_count += 1

        created_logs = []
        try:
            for st, r_txt in penalty_entries:
                log = ScoreLog.objects.create(
                    student=st,
                    rule=rule,
                    academic_year=get_current_academic_year(),
                    points=pts,
                    reason=r_txt,
                    source_module='attendance',
                    awarded_by=request.user,
                )
                created_logs.append(log)
        except ValueError as ex:
            for log in created_logs:
                try:
                    log.delete()
                except Exception:
                    pass
            return JsonResponse({'ok': False, 'error': str(ex)}, status=400)

        snapshot = _build_classroom_seating_snapshot(classroom, current_session, check_date)
        grp_msg = f' (รวมสมาชิกกลุ่มกิจกรรมอีก {group_penalty_count} คน)' if group_penalty_count else ''
        return JsonResponse({
            **snapshot,
            'ok': True,
            'batch_id': batch_id,
            'direct_count': len(targets),
            'group_penalty_count': group_penalty_count,
            'affected_count': len(penalty_entries),
            'message': f'บันทึกคะแนน ({pts:+g}) ให้นักเรียน {len(penalty_entries)} คนเรียบร้อยแล้ว{grp_msg}',
        })

    if action == 'cancel':
        batch_id = (request.POST.get('batch_id') or '').strip()
        if not batch_id or not batch_id.startswith(f'ATT{classroom.code}_'):
            return JsonResponse({'ok': False, 'error': 'รหัสชุดคะแนนไม่ถูกต้อง'}, status=400)
        logs = list(
            ScoreLog.objects.filter(
                student__room_code=classroom.code,
                source_module='attendance',
                reason__contains=f'[BATCH:{batch_id}]',
            ).select_related('student')
        )
        if not logs:
            return JsonResponse({'ok': False, 'error': 'ไม่พบรายการคะแนนชุดนี้ หรือถูกยกเลิกไปแล้ว'}, status=404)
        cancelled_count = 0
        try:
            for log in logs:
                log.delete()
                cancelled_count += 1
        except ValueError as ex:
            return JsonResponse({'ok': False, 'error': str(ex)}, status=400)

        snapshot = _build_classroom_seating_snapshot(classroom, current_session, check_date)
        return JsonResponse({
            **snapshot,
            'ok': True,
            'batch_id': batch_id,
            'cancelled_count': cancelled_count,
            'message': f'ยกเลิกการบันทึกคะแนน {cancelled_count} รายการเรียบร้อยแล้ว',
        })

    return JsonResponse({'ok': False, 'error': 'ไม่รองรับคำสั่งนี้'}, status=400)


@teacher_required
def teacher_scores_view(request):
    """(ฝั่งครู) จัดการช่วงคะแนน (สรุปแล้วล็อกห้ามบวก/ลบ + ย้ายกิจกรรมได้), CRUD กฎคะแนนครบทุกกฎ, และบวก/ลบคะแนนแยกตามช่วง"""
    room_code = request.GET.get('room') or request.POST.get('room') or '01'
    if room_code.isdigit():
        room_code = room_code.zfill(2)

    cur_yr = get_current_academic_year()
    active_period = get_active_score_period(cur_yr)
    periods = list(ScorePeriod.objects.filter(academic_year=cur_yr).prefetch_related('activities').all())
    sel_pid = request.GET.get('period_id') or request.POST.get('period_id')
    selected_period = (
        ScorePeriod.objects.filter(pk=sel_pid, academic_year=cur_yr).first()
        if sel_pid
        else active_period
    ) or (periods[0] if periods else active_period)

    def _scores_redir(pid=None):
        target_pid = pid or (selected_period.id if selected_period else '')
        return redirect(f'/teacher/scores/?room={room_code}&period_id={target_pid}')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_rule':
            code = (request.POST.get('code') or f'rule_{random.randint(1000, 9999)}').strip()
            ScoreRule.objects.create(
                code=code,
                name=(request.POST.get('name') or 'กฎคะแนนใหม่').strip(),
                rule_type=request.POST.get('rule_type', ScoreRule.TYPE_PLUS),
                points=abs(float(request.POST.get('points') or 1.0)),
                note=(request.POST.get('note') or '').strip(),
                is_auto=(request.POST.get('is_auto') == '1'),
                is_active=True,
            )
            messages.success(request, 'เพิ่มกฎคะแนนใหม่เรียบร้อยแล้ว')
            return _scores_redir()

        elif action == 'update_rule':
            rule = get_object_or_404(ScoreRule, pk=request.POST.get('rule_id'))
            rule.name = (request.POST.get('name') or rule.name).strip()
            rule.rule_type = request.POST.get('rule_type', rule.rule_type)
            rule.points = abs(float(request.POST.get('points') or rule.points or 1.0))
            rule.note = (request.POST.get('note') or '').strip()
            rule.is_auto = (request.POST.get('is_auto') == '1')
            rule.is_active = (request.POST.get('is_active', '1') == '1')
            rule.save()
            messages.success(request, f'แก้ไขกฎคะแนน "{rule.name}" เรียบร้อยแล้ว')
            return _scores_redir()

        elif action == 'delete_rule':
            rule = get_object_or_404(ScoreRule, pk=request.POST.get('rule_id'))
            r_name = rule.name
            rule.delete()
            messages.info(request, f'ลบกฎคะแนน "{r_name}" เรียบร้อยแล้ว')
            return _scores_redir()

        elif action == 'create_period':
            max_ord = ScorePeriod.objects.filter(academic_year=cur_yr).aggregate(m=Max('order_no'))['m'] or 0
            calc_mode = (request.POST.get('score_calc_mode') or ScorePeriod.CALC_DIRECT).strip()
            if calc_mode not in (ScorePeriod.CALC_DIRECT, ScorePeriod.CALC_PROPORTIONAL):
                calc_mode = ScorePeriod.CALC_DIRECT
            new_p = ScorePeriod.objects.create(
                name=(request.POST.get('name') or f'ช่วงคะแนนที่ {max_ord + 1}').strip(),
                academic_year=cur_yr,
                order_no=int(request.POST.get('order_no') or (max_ord + 1)),
                max_score=float(request.POST.get('max_score') or 20.0),
                score_calc_mode=calc_mode,
                note=(request.POST.get('note') or '').strip(),
                is_finalized=False,
            )
            messages.success(request, f'สร้าง "{new_p.name}" เรียบร้อยแล้ว (มีคะแนนบวก/ลบและกิจกรรมพร้อมใช้งาน)')
            return _scores_redir(new_p.id)

        elif action == 'update_period':
            period = get_object_or_404(ScorePeriod, pk=request.POST.get('target_period_id'))
            if period.is_finalized:
                messages.error(request, 'ช่วงคะแนนนี้สรุปคะแนนและล็อกไว้แล้ว ไม่สามารถแก้ไขได้')
                return _scores_redir(period.id)
            calc_mode = (request.POST.get('score_calc_mode') or period.score_calc_mode or ScorePeriod.CALC_DIRECT).strip()
            if calc_mode not in (ScorePeriod.CALC_DIRECT, ScorePeriod.CALC_PROPORTIONAL):
                calc_mode = ScorePeriod.CALC_DIRECT
            period.name = (request.POST.get('name') or period.name).strip()
            period.order_no = int(request.POST.get('order_no') or period.order_no)
            period.max_score = float(request.POST.get('max_score') or period.max_score)
            period.score_calc_mode = calc_mode
            period.note = (request.POST.get('note') or '').strip()
            period.save()
            recalculate_period_students_scores(period)
            messages.success(request, f'อัปเดตข้อมูล "{period.name}" เรียบร้อยแล้ว')
            return _scores_redir(period.id)

        elif action == 'assign_period_activities':
            period = get_object_or_404(ScorePeriod, pk=request.POST.get('target_period_id'))
            if period.is_finalized:
                messages.error(request, 'ไม่สามารถเปลี่ยนแปลงกิจกรรมของช่วงคะแนนที่สรุปและล็อกแล้วได้')
                return _scores_redir(period.id)
            chosen_ids = {int(x) for x in request.POST.getlist('activity_ids') if str(x).isdigit()}
            # ถอดกิจกรรมเดิมที่เคยอยู่ในช่วงนี้หากไม่ได้ติ๊กเลือก (ย้ายออกได้)
            LearningActivity.objects.filter(score_period=period).exclude(id__in=chosen_ids).update(score_period=None)
            # ดึงเฉพาะกิจกรรมที่ไม่ได้อยู่ในช่วงที่ล็อกแล้วเข้ามาในช่วงนี้
            movable = LearningActivity.objects.filter(id__in=chosen_ids).filter(
                Q(score_period__isnull=True) | Q(score_period__is_finalized=False)
            )
            movable.update(score_period=period)
            recalculate_period_students_scores(period)
            messages.success(request, f'อัปเดตรายการกิจกรรมในช่วง "{period.name}" เรียบร้อยแล้ว')
            return _scores_redir(period.id)

        elif action == 'delete_period':
            period = get_object_or_404(ScorePeriod, pk=request.POST.get('target_period_id'))
            if period.is_finalized:
                messages.error(request, 'ไม่สามารถลบช่วงคะแนนที่สรุปและล็อกแล้วได้')
                return _scores_redir(period.id)
            if period.score_logs.exists():
                messages.error(request, 'ไม่สามารถลบช่วงคะแนนนี้ได้เนื่องจากมีบันทึกคะแนนแล้ว')
                return _scores_redir(period.id)
            p_name = period.name
            LearningActivity.objects.filter(score_period=period).update(score_period=None)
            period.delete()
            next_p = get_active_score_period()
            messages.info(request, f'ลบ "{p_name}" เรียบร้อยแล้ว')
            return _scores_redir(next_p.id)

        elif action == 'finalize_period':
            period = get_object_or_404(ScorePeriod, pk=request.POST.get('target_period_id'))
            closed_p, next_p = finalize_score_period(period, teacher_user=request.user)
            messages.warning(
                request,
                f'สรุปคะแนนและล็อก "{closed_p.name}" เรียบร้อยแล้ว! ระบบจะไม่อนุญาตให้บวก/ลบในช่วงนี้ได้อีก และสลับไป "{next_p.name}" ให้อัตโนมัติ'
            )
            return _scores_redir(next_p.id)

        elif action == 'award_student':
            st = get_object_or_404(User, pk=request.POST.get('student_id'))
            target_pid = request.POST.get('target_period_id') or (selected_period.id if selected_period else '')
            period = get_object_or_404(ScorePeriod, pk=target_pid)
            if period.is_finalized:
                messages.error(
                    request,
                    f'ช่วงคะแนน "{period.name}" สรุปคะแนนและล็อกไว้แล้ว! ไม่สามารถบวกหรือลบคะแนนในส่วนนี้ได้ กรุณาไปดำเนินการในช่วงคะแนนถัดไป'
                )
                return _scores_redir(period.id)

            rule_id = request.POST.get('rule_id')
            custom_points = request.POST.get('custom_points')
            custom_note = (request.POST.get('custom_reason') or '').strip()

            if custom_points not in (None, ''):
                pts = abs(float(custom_points))
                ctype = request.POST.get('custom_type', ScoreRule.TYPE_PLUS)
                signed_pts = pts if ctype == ScoreRule.TYPE_PLUS else -pts
                ScoreLog.objects.create(
                    student=st,
                    score_period=period,
                    academic_year=getattr(period, 'academic_year', '') or get_current_academic_year(),
                    points=signed_pts,
                    reason=custom_note or f'{"บวก" if signed_pts >= 0 else "ลบ"}คะแนนใน {period.name}',
                    source_module='manual',
                    awarded_by=request.user,
                )
                messages.success(request, f'บันทึก {signed_pts:+g} คะแนน ให้ {st.get_full_name()} ใน {period.name} เรียบร้อยแล้ว')
            else:
                rule = get_object_or_404(ScoreRule, pk=rule_id)
                ScoreLog.objects.create(
                    student=st,
                    rule=rule,
                    score_period=period,
                    academic_year=getattr(period, 'academic_year', '') or get_current_academic_year(),
                    points=rule.signed_points,
                    reason=custom_note or rule.name,
                    source_module='manual',
                    awarded_by=request.user,
                )
                messages.success(request, f'บันทึกคะแนน {rule} ให้กับ {st.get_full_name()} ใน {period.name} เรียบร้อยแล้ว')
            return _scores_redir(period.id)

    rules = ScoreRule.objects.all()
    classrooms = Classroom.objects.all()
    students = list(get_active_students_qs(cur_yr, room_code=room_code).order_by('class_number'))
    period_logs_map = compute_student_period_scores(
        selected_period,
        ScoreLog.objects.filter(score_period=selected_period, student__room_code=room_code),
    )
    student_rows = []
    for st in students:
        pstat = period_logs_map.get(
            st.id, {'raw_activity': 0.0, 'activity': 0.0, 'plus': 0.0, 'minus': 0.0, 'net': 0.0}
        )
        student_rows.append({
            'student': st,
            'raw_activity_pts': round(pstat['raw_activity'], 2),
            'activity_pts': round(pstat['activity'], 2),
            'plus_pts': round(pstat['plus'], 2),
            'minus_pts': round(pstat['minus'], 2),
            'period_net': round(pstat['net'], 2),
        })

    all_activities = list(LearningActivity.objects.filter(academic_year=cur_yr).select_related('score_period').order_by('id'))
    period_activity_ids = {a.id for a in all_activities if a.score_period_id == selected_period.id}
    recent_logs = ScoreLog.objects.filter(score_period=selected_period).select_related('student', 'rule', 'score_period').order_by('-created_at')[:30]
    selected_period_structure = compute_period_structure(selected_period) if selected_period else None

    return render(request, 'teacher/scores.html', {
        'rules': rules,
        'classrooms': classrooms,
        'selected_room': room_code,
        'periods': periods,
        'selected_period': selected_period,
        'selected_period_structure': selected_period_structure,
        'calc_mode_choices': ScorePeriod.CALC_MODE_CHOICES,
        'active_period': active_period,
        'all_activities': all_activities,
        'period_activity_ids': period_activity_ids,
        'students': students,
        'student_rows': student_rows,
        'recent_logs': recent_logs,
    })


# =============================================================================
# (ฝั่งครู) จัดการและสรุปคะแนนกิจกรรมเรียงลำดับ (DSBasicSorting)
# =============================================================================
@teacher_required
def teacher_sorting_manage_view(request):
    auto_close_expired_attendance_sessions()
    setting = SystemSetting.get()
    all_classrooms = list(Classroom.objects.all().order_by('grade_level', 'code'))
    all_room_codes_set = {c.code for c in all_classrooms}
    latest_att_room = get_latest_attendance_room_code()

    params = request.POST if request.method == 'POST' else request.GET
    if params.get('room_all') == '1':
        room_filter = ''
    elif 'rooms' in params:
        picked_rooms = [r for r in params.getlist('rooms') if r.strip()]
        norm = normalize_sorting_room_filter(picked_rooms)
        norm_set = {c for c in norm.split(',') if c}
        room_filter = '' if (not norm or (all_room_codes_set and norm_set >= all_room_codes_set)) else norm
    elif 'room' in params:
        room_filter = normalize_sorting_room_filter(params.get('room') or '')
    else:
        room_filter = latest_att_room

    def _sort_redir(aid=None, force_room=None):
        rf_val = room_filter if force_room is None else force_room
        qs_parts = []
        if aid:
            qs_parts.append(f'activity_id={aid}')
        if rf_val:
            qs_parts.append(f'room={rf_val}')
        else:
            qs_parts.append('room=all')
        qs = '?' + '&'.join(qs_parts)
        return redirect(f'/teacher/sorting/{qs}')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle_main_menu':
            setting.sorting_menu_open = not setting.sorting_menu_open
            setting.save(update_fields=['sorting_menu_open'])
            status_txt = 'เปิด' if setting.sorting_menu_open else 'ปิด'
            messages.success(request, f'{status_txt}เมนูกิจกรรมเรียงลำดับสำหรับนักเรียนเรียบร้อยแล้ว')
            return _sort_redir(request.POST.get('activity_id'))

        elif action == 'create_activity':
            act = SortingActivity.objects.create(
                title=(request.POST.get('title') or 'กิจกรรมเรียงลำดับข้อมูล').strip(),
                academic_year=get_current_academic_year(),
                dataset=(request.POST.get('dataset') or '').strip(),
                unit_count=max(4, min(10, int(request.POST.get('unit_count') or 7))),
                mode=request.POST.get('mode', SortingActivity.MODE_BUBBLE_ASC),
                time_limit=int(request.POST.get('time_limit') or 180),
                is_active=True,
                is_exam_mode=(request.POST.get('is_exam_mode') == '1'),
                top_x_reward=int(request.POST.get('top_x_reward') or 5),
                created_by=request.user,
            )
            messages.success(request, 'สร้างรอบกิจกรรมเรียงลำดับใหม่เรียบร้อยแล้ว')
            return _sort_redir(act.id)

        elif action == 'new_session':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            act.session_no = int(act.session_no or 1) + 1
            act.exam_closed_rewarded = False
            act.save(update_fields=['session_no', 'exam_closed_rewarded'])
            messages.success(request, f'เริ่มเซสชันใหม่ (#{act.session_no}) ของ "{act.title}" เรียบร้อยแล้ว')
            return _sort_redir(act.id)

        elif action == 'clear_list':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            del_qs = filter_sorting_qs_by_room(
                SortingScore.objects.filter(activity=act, session_no=act.session_no), room_filter
            )
            del_qs.delete()
            scope_lbl = get_sorting_room_filter_label(room_filter)
            messages.info(request, f'ล้างรายการผู้เล่นในเซสชัน #{act.session_no} ({scope_lbl}) แล้ว')
            return _sort_redir(act.id)

        elif action == 'toggle_activity':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            act.is_active = not act.is_active
            act.save(update_fields=['is_active'])
            return _sort_redir(request.POST.get('selected_activity_id') or act.id)

        elif action == 'finish_exam':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            rf_target = room_filter if room_filter != '' or 'room' in request.POST or 'rooms' in request.POST or request.POST.get('room_all') == '1' else (act.broadcast_room_code or '')
            rec = finalize_sorting_exam_record(
                act, room_filter=rf_target, teacher_user=request.user, status=SortingExamRecord.STATUS_FINISHED
            )
            messages.success(
                request,
                f'จบการสอบ "{rec.title}" ({rec.room_label}) และบันทึกสถิติแล้ว — '
                f'ผู้เข้าสอบ {rec.participant_count} คน · สูงสุด {rec.max_score:.2f} · เฉลี่ย {rec.avg_score:.2f} คะแนน'
            )
            return _sort_redir(act.id)

        elif action == 'pause_exam':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            rf_target = room_filter or act.broadcast_room_code or ''
            rec = finalize_sorting_exam_record(
                act, room_filter=rf_target, teacher_user=request.user, status=SortingExamRecord.STATUS_PAUSED
            )
            messages.info(request, f'ปิดระบบสอบชั่วคราวแล้ว (บันทึกสถิติรอบ #{rec.session_no}) — นักเรียนยังฝึกเรียงลำดับได้ตามปกติ')
            return _sort_redir(act.id)

        elif action == 'reopen_exam':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            rec_id = (request.POST.get('record_id') or '').strip()
            if rec_id.isdigit():
                rec = get_object_or_404(SortingExamRecord, pk=int(rec_id), activity=act)
                act.session_no = rec.session_no
                act.broadcast_room_code = rec.room_filter
                act.broadcast_sort = rec.sort_type
                act.broadcast_order = rec.sort_order
                act.broadcast_unit = rec.unit_count
                act.is_exam_mode = True
                act.broadcast_seq = int(act.broadcast_seq or 0) + 1
                act.save()
                rec.status = SortingExamRecord.STATUS_ACTIVE
                rec.ended_at = None
                rec.save(update_fields=['status', 'ended_at'])
                messages.success(request, f'เปิดระบบสอบรอบ #{rec.session_no} ({rec.room_label}) อีกครั้งเรียบร้อยแล้ว')
            else:
                act.is_exam_mode = True
                act.broadcast_room_code = room_filter
                act.broadcast_seq = int(act.broadcast_seq or 0) + 1
                act.save()
                cur_rec = SortingExamRecord.objects.filter(activity=act, session_no=act.session_no).first()
                if cur_rec:
                    cur_rec.status = SortingExamRecord.STATUS_ACTIVE
                    cur_rec.ended_at = None
                    cur_rec.save(update_fields=['status', 'ended_at'])
                messages.success(request, f'เปิดระบบสอบ "{act.title}" ({get_sorting_room_filter_label(room_filter)}) เรียบร้อยแล้ว')
            return _sort_redir(act.id)

        elif action == 'delete_exam_record':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            rec = get_object_or_404(SortingExamRecord, pk=request.POST.get('record_id'), activity=act)
            t_lbl = rec.title
            rec.delete()
            messages.info(request, f'ลบสถิติ "{t_lbl}" เรียบร้อยแล้ว')
            return _sort_redir(act.id)

        elif action == 'toggle_exam_mode':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            if act.is_exam_mode:
                winners, _ = reward_sorting_exam_top_x(
                    act, teacher_user=request.user, room_filter=room_filter, close_exam=True
                )
                finalize_sorting_exam_record(act, room_filter=room_filter, teacher_user=request.user, status=SortingExamRecord.STATUS_FINISHED)
                scope_lbl = get_sorting_room_filter_label(room_filter)
                messages.success(
                    request,
                    f'ปิดโหมดสอบ "{act.title}" และมอบคะแนนบวกอัตโนมัติ ({scope_lbl}) ให้ {len(winners)} อันดับแรกเรียบร้อยแล้ว!'
                )
            else:
                act.is_exam_mode = True
                act.exam_closed_rewarded = False
                act.save(update_fields=['is_exam_mode', 'exam_closed_rewarded'])
                messages.info(request, f'เปิดโหมดสอบสำหรับ "{act.title}" แล้ว')
            return _sort_redir(act.id)

        elif action == 'delete_activity':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            act_title = act.title
            try:
                del_logs, recalc_cnt = delete_sorting_activity_and_recalculate(act)
                messages.success(
                    request,
                    f'ลบกิจกรรม "{act_title}" เรียบร้อยแล้ว (ลบประวัติคะแนน {del_logs} รายการ และคำนวณคะแนนนักเรียนใหม่ {recalc_cnt} คน)'
                )
            except ValueError as ex:
                messages.error(request, str(ex))
            return _sort_redir(None)

        elif action == 'reward_top_x':
            act = get_object_or_404(SortingActivity, pk=request.POST.get('activity_id'))
            raw_topx = (request.POST.get('top_x_reward') or '').strip()
            top_x = max(1, min(200, int(raw_topx))) if raw_topx.isdigit() else max(1, int(act.top_x_reward or 5))
            act.top_x_reward = top_x
            act.save(update_fields=['top_x_reward'])
            winners, rewarded = reward_sorting_exam_top_x(
                act, teacher_user=request.user, room_filter=room_filter
            )
            scope_lbl = get_sorting_room_filter_label(room_filter)
            messages.success(
                request,
                f'มอบคะแนนบวก ({scope_lbl}) ตามกฎ "ทำกิจกรรมเรียงลำดับสูงสุดในรอบสอบ" ให้ {len(winners)} คน สูงสุด (เพิ่มคะแนนใหม่ {len(rewarded)} คน) เรียบร้อยแล้ว!'
            )
            return _sort_redir(act.id)

    score_count_filter = Q()
    if room_filter.startswith('grade:'):
        score_count_filter = Q(scores__student__classroom__grade_level=room_filter.split(':', 1)[1])
    elif room_filter:
        codes = [c for c in room_filter.split(',') if c]
        score_count_filter = Q(scores__student__room_code__in=codes)

    cur_yr = get_current_academic_year()
    activities = SortingActivity.objects.filter(academic_year=cur_yr).annotate(
        play_count=Count('scores', filter=score_count_filter)
    ).all()
    sel_id = request.GET.get('activity_id')
    selected_activity = activities.filter(pk=sel_id).first() if sel_id else activities.first()
    live_data = _build_sorting_live_data(selected_activity, room_filter=room_filter) if selected_activity else {}
    exam_records = list(
        SortingExamRecord.objects.filter(activity=selected_activity).order_by('-started_at', '-id')[:30]
    ) if selected_activity else []

    grade_levels = [
        g for g in Classroom.objects.values_list('grade_level', flat=True).distinct().order_by('grade_level') if g
    ]
    grade_groups_dict = {}
    selected_room_codes_set = set()
    if not room_filter:
        selected_room_codes_set = set(all_room_codes_set)
    elif room_filter.startswith('grade:'):
        lvl = room_filter.split(':', 1)[1]
        selected_room_codes_set = {c.code for c in all_classrooms if c.grade_level == lvl}
    else:
        selected_room_codes_set = {c for c in room_filter.split(',') if c}
    for c in all_classrooms:
        g_key = str(c.grade_level or 'อื่นๆ')
        grade_groups_dict.setdefault(g_key, []).append(c)
    classrooms_by_grade = sorted(grade_groups_dict.items(), key=lambda x: (int(x[0]) if x[0].isdigit() else 99, x[0]))

    return render(request, 'teacher/sorting_manage.html', {
        'activities': activities,
        'selected_activity': selected_activity,
        'live_data': live_data,
        'exam_records': exam_records,
        'classrooms': all_classrooms,
        'classrooms_by_grade': classrooms_by_grade,
        'grade_levels': grade_levels,
        'selected_room': room_filter,
        'selected_room_codes_set': selected_room_codes_set,
        'is_all_rooms_selected': (not room_filter),
        'default_attendance_room_code': latest_att_room,
        'selected_room_label': get_sorting_room_filter_label(room_filter),
        'mode_choices': SortingActivity.MODE_CHOICES,
    })


@teacher_required
def teacher_sorting_summary_view(request, activity_id):
    """F.3 หน้าสรุปคะแนนกิจกรรมเรียงลำดับ: ดูความคืบหน้าสด ดูอันดับ และเลือกผู้เล่น X คนสูงสุดเพื่อมอบคะแนนบวก (กรองเฉพาะชั้น/ห้องที่เลือก)"""
    activity = get_object_or_404(SortingActivity, pk=activity_id)
    room_filter = normalize_sorting_room_filter(request.GET.get('room') or request.POST.get('room') or '')
    redir_qs = f'?room={room_filter}' if room_filter else ''

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reward_top_x':
            raw_topx = (request.POST.get('top_x_reward') or '').strip()
            top_x = max(1, min(200, int(raw_topx))) if raw_topx.isdigit() else max(1, int(activity.top_x_reward or 5))
            activity.top_x_reward = top_x
            activity.save(update_fields=['top_x_reward'])
            winners, rewarded = reward_sorting_exam_top_x(
                activity, teacher_user=request.user, room_filter=room_filter
            )
            scope_lbl = get_sorting_room_filter_label(room_filter)
            messages.success(
                request,
                f'มอบคะแนนบวก ({scope_lbl}) ตามกฎ "ทำกิจกรรมเรียงลำดับสูงสุดในรอบสอบ" ให้ {len(winners)} คน สูงสุด (เพิ่มคะแนนใหม่ {len(rewarded)} คน) เรียบร้อยแล้ว!'
            )
            return redirect(f'/teacher/sorting/{activity.id}/summary/{redir_qs}')
        elif action == 'new_session':
            activity.session_no = int(activity.session_no or 1) + 1
            activity.exam_closed_rewarded = False
            activity.save(update_fields=['session_no', 'exam_closed_rewarded'])
            messages.success(request, f'เริ่มเซสชันใหม่ (#{activity.session_no}) เรียบร้อยแล้ว')
            return redirect(f'/teacher/sorting/{activity.id}/summary/{redir_qs}')
        elif action == 'clear_list':
            filter_sorting_qs_by_room(
                SortingScore.objects.filter(activity=activity, session_no=activity.session_no),
                room_filter,
            ).delete()
            scope_lbl = get_sorting_room_filter_label(room_filter)
            messages.info(request, f'ล้างรายการผู้เล่นในเซสชัน #{activity.session_no} ({scope_lbl}) เรียบร้อยแล้ว')
            return redirect(f'/teacher/sorting/{activity.id}/summary/{redir_qs}')

    live_data = _build_sorting_live_data(activity, room_filter=room_filter)
    scores_qs = filter_sorting_qs_by_room(
        SortingScore.objects.filter(activity=activity, session_no=activity.session_no, status='completed').select_related('student'),
        room_filter,
    ).order_by('-cumulative_score', '-score', 'time_used', 'attempts')
    playing_qs = filter_sorting_qs_by_room(
        SortingScore.objects.filter(activity=activity, session_no=activity.session_no, status='playing').select_related('student'),
        room_filter,
    ).order_by('-updated_at')
    grade_levels = [
        g for g in Classroom.objects.values_list('grade_level', flat=True).distinct().order_by('grade_level') if g
    ]
    return render(request, 'teacher/sorting_summary.html', {
        'activity': activity,
        'live_data': live_data,
        'classrooms': Classroom.objects.all(),
        'grade_levels': grade_levels,
        'selected_room': room_filter,
        'selected_room_label': get_sorting_room_filter_label(room_filter),
        'scores': scores_qs,
        'playing': playing_qs,
    })


# =============================================================================
# (ฝั่งครู) จัดการห้อง Bingo, หมุนวงล้อออกเลข, และดูสถิติ Bingo แยกห้อง/กลุ่ม
# =============================================================================
def _build_bingo_stats_context(request, default_room=None):
    """รวบรวมข้อมูลสถิติ Bingo แยกตามห้อง/กลุ่ม สำหรับแสดงรวมในหน้าจัดการและควบคุมวงล้อ"""
    cur_yr = get_current_academic_year()
    room_filter = request.GET.get('classroom', '')
    group_filter = request.GET.get('group', '')
    bingo_room_id = request.GET.get('bingo_room_id', '')

    drawn_qs = BingoDrawnNumber.objects.filter(room__academic_year=cur_yr).select_related('room__classroom', 'drawn_by_student')
    winner_qs = BingoWinner.objects.filter(room__academic_year=cur_yr).select_related('room__classroom', 'student')

    if room_filter:
        rf_norm = normalize_bingo_room_filter(room_filter)
        drawn_qs = drawn_qs.filter(
            Q(target_room_code='all')
            | Q(target_room_code=rf_norm)
            | Q(room__classroom__code=rf_norm)
            | Q(drawn_by_student__room_code=rf_norm)
        )
        winner_qs = winner_qs.filter(student__room_code=rf_norm)
    if group_filter:
        drawn_qs = drawn_qs.filter(
            Q(room__group_filter__iexact=group_filter) | Q(drawn_by_student__group_name__iexact=group_filter)
        )
        winner_qs = winner_qs.filter(student__group_name__iexact=group_filter)

    all_freq = Counter(drawn_qs.values_list('number', flat=True)).most_common(15)
    student_pick_qs = drawn_qs.filter(drawn_by_mode=BingoDrawnNumber.MODE_STUDENT)
    student_pick_freq = Counter(student_pick_qs.values_list('number', flat=True)).most_common(15)

    w_by_room = list(
        winner_qs.values('student__room_code')
        .annotate(total=Count('id'))
        .order_by('student__room_code')
    )
    w_by_group = list(
        winner_qs.values('student__group_name')
        .annotate(total=Count('id'))
        .order_by('-total')
    )

    selected_room_obj = (
        BingoRoom.objects.filter(pk=bingo_room_id, academic_year=cur_yr).first()
        if bingo_room_id
        else (default_room or BingoRoom.objects.filter(academic_year=cur_yr).first())
    )
    unopened_numbers = []
    opened_in_selected = []
    drawn_by_room_summary = []
    recent_draws_with_hits = []
    if selected_room_obj:
        mn, mx = selected_room_obj.get_min_max_range()
        scope_rf = normalize_bingo_room_filter(room_filter) if room_filter else 'all'
        opened_in_selected, opened_set = get_effective_drawn_list_and_set(selected_room_obj, scope_rf)
        opened_in_selected = sorted(opened_in_selected)
        unopened_numbers = [n for n in range(mn, mx + 1) if n not in opened_set]
        drawn_by_room_summary = _build_bingo_drawn_by_room(selected_room_obj)
        recent_draws_with_hits = list(
            selected_room_obj.drawn_numbers.select_related('drawn_by_student').order_by('-created_at', '-id')[:40]
        )

    return {
        'stats_bingo_rooms': BingoRoom.objects.filter(academic_year=cur_yr),
        'stats_selected_classroom': room_filter,
        'stats_selected_group': group_filter,
        'stats_selected_room_obj': selected_room_obj,
        'stats_all_freq': all_freq,
        'stats_student_pick_freq': student_pick_freq,
        'stats_winners_by_room': w_by_room,
        'stats_winners_by_group': w_by_group,
        'stats_unopened_numbers': unopened_numbers,
        'stats_opened_in_selected': opened_in_selected,
        'stats_drawn_by_room_summary': drawn_by_room_summary,
        'stats_recent_draws_with_hits': recent_draws_with_hits,
        'stats_recent_student_picks': list(student_pick_qs.order_by('-created_at')[:20]),
        'bingo_rooms': BingoRoom.objects.filter(academic_year=cur_yr),
        'selected_classroom': room_filter,
        'selected_group': group_filter,
        'selected_room_obj': selected_room_obj,
        'all_freq': all_freq,
        'student_pick_freq': student_pick_freq,
        'winners_by_group': w_by_group,
        'unopened_numbers': unopened_numbers,
        'opened_in_selected': opened_in_selected,
        'drawn_by_room_summary': drawn_by_room_summary,
        'recent_draws_with_hits': recent_draws_with_hits,
        'recent_student_picks': list(student_pick_qs.order_by('-created_at')[:20]),
    }


@teacher_required
def teacher_bingo_manage_view(request):
    setting = SystemSetting.get()
    classrooms = Classroom.objects.all()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle_main_menu':
            setting.bingo_menu_open = not setting.bingo_menu_open
            setting.save(update_fields=['bingo_menu_open'])
            messages.success(request, 'อัปเดตสถานะการเปิด/ปิดเมนู Bingo เรียบร้อยแล้ว')
            return redirect('core:teacher_bingo_manage')

        elif action == 'create_room':
            c_id = request.POST.get('classroom_id')
            room_obj = Classroom.objects.filter(pk=c_id).first() if c_id else None
            is_grade_wide = (request.POST.get('is_grade_wide') == '1') or (room_obj is None)
            if is_grade_wide:
                room_obj = None
            continuous_play = (request.POST.get('continuous_play', '1') == '1')
            patterns = request.POST.getlist('win_patterns') or ['all']
            custom_size = max(2, min(12, int(request.POST.get('size') or 5)))
            num_range_str = (request.POST.get('number_range') or f'1-{max(36, custom_size * custom_size + 10)}').strip()
            room = BingoRoom.objects.create(
                title=(request.POST.get('title') or 'Bingo พร้อมกันทั้งระดับสายชั้น ม.4').strip(),
                academic_year=get_current_academic_year(),
                classroom=room_obj,
                is_grade_wide=is_grade_wide,
                continuous_play=continuous_play,
                group_filter=(request.POST.get('group_filter') or '').strip(),
                size=custom_size,
                number_range=num_range_str,
                win_patterns=','.join(patterns),
                special_corner_center_rule=request.POST.get('special_corner_center_rule', 'student_fill_protected'),
                draw_condition=request.POST.get('draw_condition', 'normal'),
                allow_student_pick=(request.POST.get('allow_student_pick') == '1'),
                status=BingoRoom.STATUS_OPEN,
                created_by=request.user,
            )
            messages.success(request, f'สร้างห้อง Bingo "{room.title}" (ตาราง {room.size}×{room.size} เล่นต่อเนื่องทั้งระดับ) เรียบร้อยแล้ว')
            return redirect('core:teacher_bingo_control', room_id=room.id)

        elif action == 'reset_room':
            room = get_object_or_404(BingoRoom, pk=request.POST.get('room_id'))
            room.drawn_numbers.all().delete()
            room.winners.all().delete()
            room.active_picker_student = None
            room.active_picker_room_code = ''
            room.active_picker_granted_at = None
            room.active_picker_accepted = False
            room.last_picker_event = ''
            room.status = BingoRoom.STATUS_OPEN
            room.save(update_fields=['status', 'active_picker_student', 'active_picker_room_code', 'active_picker_granted_at', 'active_picker_accepted', 'last_picker_event'])
            messages.info(request, f'รีเซ็ตเลขที่ออกในห้อง "{room.title}" เรียบร้อยแล้ว')
            return redirect('core:teacher_bingo_manage')

    cur_yr = get_current_academic_year()
    rooms = BingoRoom.objects.filter(academic_year=cur_yr).annotate(card_count=Count('cards', distinct=True), winner_count=Count('winners', distinct=True)).all()
    stats_ctx = _build_bingo_stats_context(request)
    active_tab = (request.GET.get('tab') or ('stats' if (request.GET.get('classroom') or request.GET.get('group') or request.GET.get('bingo_room_id')) else 'rooms')).strip()
    return render(request, 'teacher/bingo_manage.html', {
        **stats_ctx,
        'winners_by_room': stats_ctx['stats_winners_by_room'],
        'rooms': rooms,
        'classrooms': classrooms,
        'active_tab': active_tab,
    })


@teacher_required
def teacher_bingo_control_view(request, room_id):
    """หน้าจอวงล้อออกเลข Bingo สำหรับผู้ดูแล รองรับ filter ออกเลขรายห้อง + ดูสถิติ + ให้สิทธิ์นักเรียนเลขที่ X ออกเลข"""
    room = get_object_or_404(BingoRoom, pk=room_id)
    default_rf = room.classroom.code if room.classroom else (get_latest_attendance_room_code() or 'all')
    selected_room_filter = normalize_bingo_room_filter(
        request.GET.get('room_filter') or request.POST.get('room_filter') or default_rf
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_settings':
            room.draw_condition = request.POST.get('draw_condition', room.draw_condition)
            room.allow_student_pick = (request.POST.get('allow_student_pick') == '1')
            room.continuous_play = (request.POST.get('continuous_play', '1') == '1')
            room.status = request.POST.get('status', room.status)
            room.save(update_fields=['draw_condition', 'allow_student_pick', 'continuous_play', 'status'])
            messages.success(request, 'อัปเดตการตั้งค่าห้อง Bingo เรียบร้อยแล้ว')
            return redirect(f'/teacher/bingo/{room.id}/control/?room_filter={selected_room_filter}')
        elif action == 'reset_drawn':
            reset_scope = normalize_bingo_room_filter(request.POST.get('reset_scope') or 'all')
            if reset_scope == 'all':
                room.drawn_numbers.all().delete()
                room.winners.all().delete()
                room.active_picker_student = None
                room.active_picker_room_code = ''
                room.active_picker_accepted = False
                room.active_picker_granted_at = None
                room.last_picker_event = ''
                room.status = BingoRoom.STATUS_OPEN
                room.save(update_fields=['status', 'active_picker_student', 'active_picker_room_code', 'active_picker_accepted', 'active_picker_granted_at', 'last_picker_event'])
                messages.info(request, 'เริ่มรอบใหม่ทุกห้องเรียบร้อยแล้ว (ล้างเลขที่ออกและผู้ชนะทั้งหมด คงตารางนักเรียนเดิมไว้)')
            else:
                room.drawn_numbers.filter(target_room_code=reset_scope).delete()
                room.winners.filter(room_code=reset_scope).delete()
                messages.info(request, f'ล้างเลขที่ออกและผู้ชนะเฉพาะห้อง {get_bingo_room_label(reset_scope)} เรียบร้อยแล้ว')
            return redirect(f'/teacher/bingo/{room.id}/control/?room_filter={selected_room_filter}')
        elif action == 'reset_all_new_game':
            room.drawn_numbers.all().delete()
            room.winners.all().delete()
            room.cards.all().delete()
            room.active_picker_student = None
            room.active_picker_room_code = ''
            room.active_picker_granted_at = None
            room.active_picker_accepted = False
            room.last_picker_event = ''
            room.status = BingoRoom.STATUS_OPEN
            room.save(update_fields=['status', 'active_picker_student', 'active_picker_room_code', 'active_picker_granted_at', 'active_picker_accepted', 'last_picker_event'])
            messages.info(request, 'เริ่มเกมใหม่ทั้งหมดเรียบร้อยแล้ว (ล้างเลขที่ออก ผู้ชนะ และตาราง Bingo ของนักเรียนทั้งหมด)')
            return redirect(f'/teacher/bingo/{room.id}/control/?room_filter={selected_room_filter}')

    mn, mx = room.get_min_max_range()
    drawn_qs = get_effective_drawn_qs(room, selected_room_filter).select_related('drawn_by_student')
    drawn_nums, drawn_set = get_effective_drawn_list_and_set(room, selected_room_filter)
    unopened_nums = [x for x in range(mn, mx + 1) if x not in drawn_set]
    cards = room.cards.select_related('student', 'student__classroom').order_by('student__room_code', 'student__class_number')
    cards_count_in_scope = cards.count() if selected_room_filter == 'all' else cards.filter(student__room_code=selected_room_filter).count()

    room_card_students = [
        {
            'id': c.student.id,
            'class_number': c.student.class_number,
            'name': c.student.get_full_name(),
            'room_code': c.student.room_code,
            'room_display': c.student.room_display,
        }
        for c in cards
    ]

    winners_all = room.winners.select_related('student', 'student__classroom').order_by('rank_order')
    winners = winners_all if selected_room_filter == 'all' else winners_all.filter(room_code=selected_room_filter)

    winners_grouped = {}
    for w in winners:
        rc = w.room_code or w.student.room_code or '99'
        r_label = w.student.room_display
        winners_grouped.setdefault((int(rc) if rc.isdigit() else 99, r_label), []).append(w)
    winners_by_room = [(k[1], v) for k, v in sorted(winners_grouped.items(), key=lambda x: x[0])]

    cards_grouped = {}
    for c in cards:
        rc = c.student.room_code or '99'
        r_label = c.student.room_display
        key = (int(rc) if rc.isdigit() else 99, r_label)
        cards_grouped[key] = cards_grouped.get(key, 0) + 1
    room_cards_summary = [(k[1], v) for k, v in sorted(cards_grouped.items(), key=lambda x: x[0])]

    latest_drawn = drawn_qs.last()
    latest_hit_stats = compute_bingo_number_hits(room, latest_drawn.number, selected_room_filter) if latest_drawn else None
    stats_ctx = _build_bingo_stats_context(request, default_room=room)
    all_rooms_qs = Classroom.objects.all()
    picker_room_default = selected_room_filter if selected_room_filter != 'all' else (get_latest_attendance_room_code() or (all_rooms_qs.first().code if all_rooms_qs.exists() else '01'))

    return render(request, 'teacher/bingo_control.html', {
        **stats_ctx,
        'room': room,
        'classrooms': all_rooms_qs,
        'selected_room_filter': selected_room_filter,
        'selected_room_label': get_bingo_room_label(selected_room_filter),
        'picker_room_default': picker_room_default,
        'room_card_students': room_card_students,
        'active_picker': room.active_picker_student,
        'drawn_qs': drawn_qs,
        'drawn_json': json.dumps(drawn_nums),
        'drawn_scope_json': json.dumps(_build_bingo_drawn_scope_map(room, selected_room_filter)),
        'drawn_by_room': _build_bingo_drawn_by_room(room),
        'latest_hit_stats': latest_hit_stats,
        'unopened_nums': unopened_nums,
        'min_num': mn,
        'max_num': mx,
        'all_range': list(range(mn, mx + 1)),
        'cards': cards,
        'cards_count_in_scope': cards_count_in_scope,
        'winners': winners,
        'winners_by_room': winners_by_room,
        'room_cards_summary': room_cards_summary,
    })


@teacher_required
def teacher_bingo_stats_view(request):
    """หน้าสถิติ Bingo สำหรับผู้ดูแล"""
    stats_ctx = _build_bingo_stats_context(request)
    return render(request, 'teacher/bingo_stats.html', {
        **stats_ctx,
        'classrooms': Classroom.objects.all(),
        'winners_by_room': stats_ctx['stats_winners_by_room'],
    })


# =============================================================================
# (ฝั่งครู) จัดการระบบสุ่ม Debsirin Lotto (ใช้เลขประจำตัวนักเรียน 5 หลัก)
# =============================================================================
def _get_lotto_pending_points(session):
    rule = ScoreRule.objects.filter(code='lotto_picked', is_active=True).first()
    if rule:
        return float(rule.signed_points)
    return float(session.bonus_points or 1.0)


def _parse_lotto_filter_params(data, session=None):
    """แยกค่าตัวกรองการสุ่ม ID LOTTO ทั้ง 4 ข้อจาก request params (POST หรือ GET)"""
    act_raw = (data.get('filter_activity_id') or '').strip()
    act_id = int(act_raw) if act_raw.isdigit() else (session.filter_activity_id if session and 'filter_activity_id' not in data else None)
    x_raw = (data.get('filter_last_x_count') or '').strip()
    x_cnt = max(1, min(50, int(x_raw))) if x_raw.isdigit() else (session.filter_last_x_count if session else 5)
    cdate = _parse_check_date(data.get('filter_check_date')) if data.get('filter_check_date') else (session.filter_check_date if session and session.filter_check_date else timezone.localdate())
    return {
        'filter_submitted_activity': (data.get('filter_submitted_activity') == '1'),
        'filter_activity_id': act_id,
        'filter_exclude_last_x': (data.get('filter_exclude_last_x') == '1'),
        'filter_last_x_count': x_cnt,
        'filter_material_checked': (data.get('filter_material_checked') == '1'),
        'filter_presented_activity': (data.get('filter_presented_activity') == '1'),
        'filter_check_date': cdate,
    }


@teacher_required
def teacher_lotto_manage_view(request):
    setting = SystemSetting.get()
    classrooms = Classroom.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'toggle_main_menu':
            setting.lotto_menu_open = not setting.lotto_menu_open
            setting.save(update_fields=['lotto_menu_open'])
            messages.success(request, 'อัปเดตการเปิด/ปิดเมนู ID LOTTO เรียบร้อยแล้ว')
            return redirect('core:teacher_lotto_manage')

        elif action == 'create_session':
            cid = request.POST.get('classroom_id')
            room_obj = Classroom.objects.filter(pk=cid).first() if cid else None
            fp = _parse_lotto_filter_params(request.POST)
            sess = LottoSession.objects.create(
                title=(request.POST.get('title') or 'ID LOTTO').strip(),
                academic_year=get_current_academic_year(),
                classroom=room_obj,
                group_filter=(request.POST.get('group_filter') or '').strip(),
                winner_count=int(request.POST.get('winner_count') or 5),
                only_never_picked_in_session=(request.POST.get('only_never_picked') == '1'),
                bonus_points=float(request.POST.get('bonus_points') or 1.0),
                filter_submitted_activity=fp['filter_submitted_activity'],
                filter_activity_id=fp['filter_activity_id'],
                filter_exclude_last_x=fp['filter_exclude_last_x'],
                filter_last_x_count=fp['filter_last_x_count'],
                filter_material_checked=fp['filter_material_checked'],
                filter_presented_activity=fp['filter_presented_activity'],
                filter_check_date=fp['filter_check_date'],
                is_open=True,
                created_by=request.user,
            )
            messages.success(request, f'สร้างรอบการสุ่ม "{sess.title}" เรียบร้อยแล้ว')
            return redirect(f'/teacher/lotto/?session_id={sess.id}')

        elif action == 'update_filters':
            sess = get_object_or_404(LottoSession, pk=request.POST.get('session_id'))
            fp = _parse_lotto_filter_params(request.POST, sess)
            for k, v in fp.items():
                setattr(sess, k, v)
            sess.save(update_fields=list(fp.keys()))
            messages.success(request, 'อัปเดตเงื่อนไขตัวกรองการสุ่ม ID LOTTO เรียบร้อยแล้ว')
            return redirect(f'/teacher/lotto/?session_id={sess.id}')

        elif action == 'finalize_scores':
            sess = get_object_or_404(LottoSession, pk=request.POST.get('session_id'))
            try:
                awarded, pts_each = finalize_lotto_session_scores(sess, teacher_user=request.user)
                if not awarded:
                    messages.info(request, 'ไม่มีรายชื่อรอสรุปคะแนนเพิ่มเติมในรอบนี้ (ผู้ที่ถูกลบออกจะไม่ได้รับคะแนน)')
                else:
                    messages.success(
                        request,
                        f'สรุปคะแนน ID LOTTO "{sess.title}" สำเร็จ! มอบ +{pts_each:g} คะแนน ให้นักเรียนที่คงเหลือ {len(awarded)} คนเรียบร้อยแล้ว'
                    )
            except ValueError as e:
                messages.error(request, str(e))
            return redirect(f'/teacher/lotto/?session_id={sess.id}')

    cur_yr = get_current_academic_year()
    sessions = LottoSession.objects.filter(academic_year=cur_yr).select_related('classroom', 'filter_activity').all()
    sel_id = request.GET.get('session_id')
    current_session = sessions.filter(pk=sel_id).first() if sel_id else sessions.first()

    results = []
    removed_results = []
    candidate_count = 0
    pending_count = 0
    awarded_count = 0
    effective_bonus_points = 1.0
    if current_session:
        results = current_session.results.filter(removed=False).select_related('student').order_by('-order_index')
        removed_results = current_session.results.filter(removed=True).select_related('student').order_by('-created_at')
        pending_count = current_session.results.filter(removed=False, is_awarded=False).count()
        awarded_count = current_session.results.filter(removed=False, is_awarded=True).count()
        effective_bonus_points = _get_lotto_pending_points(current_session)
        candidate_count = get_lotto_eligible_students_qs(current_session).count()

    return render(request, 'teacher/lotto_manage.html', {
        'classrooms': classrooms,
        'activities': LearningActivity.objects.filter(academic_year=cur_yr).order_by('id'),
        'today_str': timezone.localdate().strftime('%Y-%m-%d'),
        'sessions': sessions,
        'current_session': current_session,
        'results': results,
        'removed_results': removed_results,
        'candidate_count': candidate_count,
        'pending_count': pending_count,
        'awarded_count': awarded_count,
        'effective_bonus_points': effective_bonus_points,
    })


@teacher_required
@require_POST
def api_lotto_draw(request, session_id):
    """
    สุ่มนักเรียน ID LOTTO จากรายชื่อตามเงื่อนไข (ห้อง/กลุ่ม/ยังไม่เคยถูกสุ่ม + ตัวกรองส่งงาน/ไม่ใช่ X คนสุดท้าย/ตรวจสอบแล้ว/นำเสนอแล้ว)
    - สร้างผลสุ่มในสถานะรอสรุป (is_awarded=False, ยังไม่บวกคะแนน จนกว่าครูจะกดปุ่มสรุปเพื่อให้คะแนน)
    """
    session = get_object_or_404(LottoSession, pk=session_id)
    if 'filters_present' in request.POST:
        fp = _parse_lotto_filter_params(request.POST, session)
        for k, v in fp.items():
            setattr(session, k, v)
        session.save(update_fields=list(fp.keys()))

    cand_qs = get_lotto_eligible_students_qs(session)

    pool = list(cand_qs)
    if not pool:
        return JsonResponse({'ok': False, 'error': 'ไม่มีรายชื่อนักเรียนที่ตรงตามเงื่อนไขคงเหลือให้สุ่มแล้ว'}, status=400)

    lucky_student = random.choice(pool)
    next_order = session.results.count() + 1
    pending_points = _get_lotto_pending_points(session)

    res = LottoResult.objects.create(
        session=session,
        student=lucky_student,
        order_index=next_order,
        points_awarded=0.0,
        is_awarded=False,
    )

    pending_count = session.results.filter(removed=False, is_awarded=False).count()
    awarded_count = session.results.filter(removed=False, is_awarded=True).count()
    sid_5 = (lucky_student.student_id or '00000').zfill(5)
    return JsonResponse({
        'ok': True,
        'result_id': res.id,
        'order_index': res.order_index,
        'student_id': sid_5,
        'digits_6': sid_5.zfill(6),
        'full_name': lucky_student.get_full_name(),
        'room_display': lucky_student.room_display,
        'class_number': lucky_student.class_number,
        'group_name': lucky_student.group_name,
        'avatar_url': lucky_student.avatar_url,
        'avatar_initial': lucky_student.avatar_initial,
        'points_awarded': 0.0,
        'pending_points': pending_points,
        'is_awarded': False,
        'pending_count': pending_count,
        'awarded_count': awarded_count,
        'total_score': lucky_student.total_score,
        'remaining_candidates': len(pool) - 1,
        'created_at': res.created_at.strftime('%H:%M:%S'),
    })


@teacher_required
@require_POST
def api_lotto_remove_result(request, session_id):
    """
    ลบชื่อนักเรียนออกจากรายการสุ่ม (เช่น นักเรียนไม่อยู่ในห้อง / ลาป่วย)
    - ถ้ายังไม่ได้สรุปคะแนน -> ลบออกโดยไม่เคยบวกคะแนน
    - ถ้าสรุปคะแนนไปแล้ว -> ดึงคะแนนคืน
    """
    session = get_object_or_404(LottoSession, pk=session_id)
    result_id = request.POST.get('result_id')
    reason = (request.POST.get('reason') or 'ไม่อยู่ในห้องเรียน / สละสิทธิ์').strip()

    res = get_object_or_404(LottoResult, pk=result_id, session=session)
    if not res.removed:
        res.removed = True
        res.removed_reason = reason
        if res.is_awarded and res.points_awarded:
            rev_pts = -abs(res.points_awarded)
            ScoreLog.objects.create(
                student=res.student,
                academic_year=session.academic_year or get_current_academic_year(),
                points=rev_pts,
                reason=f'ยกเลิกผลสุ่ม ID LOTTO รอบ "{session.title}" เหตุผล: {reason}',
                source_module='lotto',
                awarded_by=request.user,
            )
            res.is_awarded = False
            res.points_awarded = 0.0
        res.save(update_fields=['removed', 'removed_reason', 'is_awarded', 'points_awarded'])
    return JsonResponse({
        'ok': True,
        'result_id': res.id,
        'student_name': res.student.get_full_name(),
        'reason': res.removed_reason,
        'pending_count': session.results.filter(removed=False, is_awarded=False).count(),
        'awarded_count': session.results.filter(removed=False, is_awarded=True).count(),
    })


@teacher_required
@require_POST
def api_lotto_finalize(request, session_id):
    """ปุ่มสรุปเพื่อให้คะแนนนักเรียนที่สุ่มได้ทั้งหมดในรอบนี้ (ไม่นับรายชื่อที่ถูกลบออก)"""
    session = get_object_or_404(LottoSession, pk=session_id)
    try:
        awarded, pts_each = finalize_lotto_session_scores(session, teacher_user=request.user)
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    total_awarded = session.results.filter(removed=False, is_awarded=True).count()
    return JsonResponse({
        'ok': True,
        'awarded_count': len(awarded),
        'total_awarded_in_session': total_awarded,
        'pending_count': 0,
        'points_each': pts_each,
        'awarded_result_ids': [r.id for r in awarded],
        'message': (
            f'สรุปคะแนน ID LOTTO สำเร็จ! มอบ +{pts_each:g} คะแนน ให้ผู้โชคดี {len(awarded)} คน'
            if awarded else 'ไม่มีรายชื่อรอสรุปคะแนนเพิ่มเติม'
        ),
    })


@login_required
def api_lotto_state(request, session_id):
    session = get_object_or_404(LottoSession, pk=session_id)
    pending_points = _get_lotto_pending_points(session)
    override_filters = None
    if request.GET.get('filters_present') == '1':
        override_filters = _parse_lotto_filter_params(request.GET, session)
    cand_count = get_lotto_eligible_students_qs(session, override_filters=override_filters).count()
    active_results = [
        {
            'id': r.id,
            'order_index': r.order_index,
            'student_id': (r.student.student_id or '').zfill(5),
            'full_name': r.student.get_full_name(),
            'room_display': r.student.room_display,
            'class_number': r.student.class_number,
            'avatar_url': r.student.avatar_url,
            'avatar_initial': r.student.avatar_initial,
            'points_awarded': r.points_awarded,
            'pending_points': pending_points,
            'is_awarded': r.is_awarded,
            'time': r.created_at.strftime('%H:%M:%S'),
        }
        for r in session.results.filter(removed=False).select_related('student').order_by('-order_index')
    ]
    return JsonResponse({
        'ok': True,
        'session_title': session.title,
        'candidate_count': cand_count,
        'results': active_results,
    })


# =============================================================================
# หน้าสำหรับ Admin (ผู้ดูแลระบบสูงสุด - ข้อ 4 สำหรับ Admin)
# =============================================================================
@admin_required
def admin_manage_staff_view(request):
    """
    A.2 จัดการ Admin/ผู้ดูแล:
    - เพิ่มบัญชีด้วย username/password หรือระบุเป็นอีเมลโรงเรียน @debsirin.ac.th
    - กรณีเพิ่มด้วยอีเมลโรงเรียน ผู้ใช้สามารถล็อกอินผ่าน Google OAuth ได้ในภายหลัง
    """
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_staff':
            role = request.POST.get('role', User.ROLE_TEACHER)
            username = (request.POST.get('username') or '').strip()
            email = (request.POST.get('email') or '').strip().lower()
            password = (request.POST.get('password') or '1234').strip()
            fname = (request.POST.get('first_name') or '').strip()
            lname = (request.POST.get('last_name') or '').strip()
            allow_oauth = (request.POST.get('allow_google_oauth') == '1')

            if not username and email:
                username = email.split('@')[0]

            if not username:
                messages.error(request, 'กรุณาระบุ Username หรืออีเมลโรงเรียน @debsirin.ac.th')
            elif User.objects.filter(username__iexact=username).exists():
                messages.error(request, f'ชื่อผู้ใช้ {username} มีอยู่ในระบบแล้ว')
            else:
                u = User.objects.create_user(
                    username=username,
                    password=password,
                    email=email,
                    first_name=fname or username,
                    last_name=lname,
                    role=role if role in (User.ROLE_ADMIN, User.ROLE_TEACHER) else User.ROLE_TEACHER,
                    allow_google_oauth=allow_oauth,
                    is_superuser=(role == User.ROLE_ADMIN),
                )
                messages.success(
                    request,
                    f'เพิ่มบัญชี {u.get_role_display()} "{u.username}" (อีเมล: {u.email or "-"}) เรียบร้อยแล้ว'
                )
                return redirect('core:admin_manage_staff')

        elif action == 'delete_staff':
            target = get_object_or_404(User, pk=request.POST.get('user_id'))
            if target.pk == request.user.pk:
                messages.error(request, 'ไม่สามารถลบบัญชีที่กำลังล็อกอินอยู่ได้')
            else:
                target.delete()
                messages.info(request, 'ลบบัญชีผู้ดูแลเรียบร้อยแล้ว')
            return redirect('core:admin_manage_staff')

    staff_users = User.objects.filter(role__in=[User.ROLE_ADMIN, User.ROLE_TEACHER]).order_by('role', 'username')
    return render(request, 'admin_panel/manage_staff.html', {
        'staff_users': staff_users,
    })


@admin_required
def admin_oauth_settings_view(request):
    """ตั้งค่าทั่วไป สีธีมผู้ดูแล/นักเรียน เมนูเกม ปีการศึกษา Google OAuth (@debsirin.ac.th) และ AI API"""
    ensure_academic_year_exists()
    setting = SystemSetting.get()
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save_all').strip()
        if action == 'reset_theme_colors':
            setting.theme_colors_json = '{}'
            setting.save(update_fields=['theme_colors_json', 'updated_at'])
            messages.success(request, 'รีเซ็ตชุดสีธีมของระบบกลับสู่สีมาตรฐานเรียบร้อยแล้ว')
            return redirect('core:admin_oauth_settings')

        if action == 'verify_default_gdrive':
            sa_raw = (request.POST.get('gdrive_service_account_json') or setting.gdrive_service_account_json or '').strip()
            df_raw = (request.POST.get('gdrive_default_folder_url') or setting.gdrive_default_folder_url or '').strip()
            setting.gdrive_service_account_json = sa_raw
            setting.gdrive_default_folder_url = df_raw
            res = verify_gdrive_folder_access(df_raw, setting=setting)
            setting.gdrive_default_folder_verified = bool(res.get('ok'))
            if res.get('ok') and res.get('folder_url'):
                setting.gdrive_default_folder_url = res['folder_url']
            setting.save()
            if res.get('ok'):
                messages.success(request, res['message'])
            else:
                messages.error(request, res['message'])
            return redirect('core:admin_oauth_settings')

        if 'site_title' in request.POST:
            st_title = (request.POST.get('site_title') or '').strip()[:150]
            if st_title:
                setting.site_title = st_title
        if 'site_subtitle' in request.POST:
            setting.site_subtitle = (request.POST.get('site_subtitle') or '').strip()[:200]
        if 'last_activity_max_score' in request.POST:
            raw_ms = (request.POST.get('last_activity_max_score') or '').strip()
            try:
                setting.last_activity_max_score = max(1.0, float(raw_ms))
            except ValueError:
                pass
        if 'sidebar_heading_size_px' in request.POST:
            raw_fs = (request.POST.get('sidebar_heading_size_px') or '').strip()
            try:
                setting.sidebar_heading_size_px = max(11, min(22, int(raw_fs)))
            except ValueError:
                pass
        if request.POST.get('game_toggles_present') == '1':
            setting.sorting_menu_open = (request.POST.get('sorting_menu_open') == '1')
            setting.bingo_menu_open = (request.POST.get('bingo_menu_open') == '1')
            setting.lotto_menu_open = (request.POST.get('lotto_menu_open') == '1')

        setting.allowed_oauth_domain = (request.POST.get('allowed_oauth_domain') or 'debsirin.ac.th').strip().lstrip('@')
        setting.google_client_id = (request.POST.get('google_client_id') or '').strip()
        setting.google_client_secret = (request.POST.get('google_client_secret') or '').strip()
        setting.ai_provider = request.POST.get('ai_provider', 'gemini')
        setting.gemini_api_key = (request.POST.get('gemini_api_key') or '').strip()
        setting.claude_api_key = (request.POST.get('claude_api_key') or '').strip()
        setting.attendance_check_ip_prefix = (request.POST.get('attendance_check_ip_prefix') or '').strip()

        raw_ay = (request.POST.get('current_academic_year') or '').strip()[:20]
        ay_error_msg = ''
        if raw_ay and raw_ay != setting.current_academic_year:
            if AcademicYear.objects.filter(year=raw_ay).exists():
                switch_current_academic_year(raw_ay)
                setting = SystemSetting.get()
            else:
                ay_error_msg = f'ยังไม่ได้สร้างปีการศึกษา "{raw_ay}" กรุณากดปุ่ม "ขึ้นปีการศึกษาใหม่" เพื่อสร้างก่อนใช้งาน'

        if 'gdrive_service_account_json' in request.POST:
            setting.gdrive_service_account_json = (request.POST.get('gdrive_service_account_json') or '').strip()
        if 'gdrive_default_folder_url' in request.POST:
            new_gd_url = (request.POST.get('gdrive_default_folder_url') or '').strip()[:500]
            if new_gd_url != setting.gdrive_default_folder_url:
                setting.gdrive_default_folder_verified = False
            setting.gdrive_default_folder_url = new_gd_url

        theme_data = setting.get_theme_colors()
        for key in SystemSetting.DEFAULT_THEME_COLORS:
            raw_hex = (request.POST.get(f'theme_{key}') or '').strip()
            if re.match(r'^#[0-9a-fA-F]{6}$', raw_hex):
                theme_data[key] = raw_hex
        setting.theme_colors_json = json.dumps(theme_data, ensure_ascii=False)
        setting.save()
        if ay_error_msg:
            messages.error(request, ay_error_msg)
        else:
            messages.success(request, 'บันทึกการตั้งค่าระบบ สีธีม Google OAuth AI API และ Google Drive เรียบร้อยแล้ว')
        return redirect('core:admin_oauth_settings')

    cur_yr = (setting.current_academic_year or '2569').strip()
    academic_years = list(AcademicYear.objects.order_by('-year', '-created_at'))
    suggested_next_year = str(int(cur_yr) + 1) if cur_yr.isdigit() else f'{cur_yr}_new'
    return render(request, 'admin_panel/oauth_settings.html', {
        'setting': setting,
        'theme_colors': setting.get_theme_colors(),
        'academic_years': academic_years,
        'suggested_next_year': suggested_next_year,
    })


@admin_required
def admin_session_settings_view(request):
    """ตั้งค่า Session (บังคับ 1 session ต่อบัญชี) และดู/เตะ Session ที่กำลังออนไลน์"""
    setting = SystemSetting.get()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save_policy':
            setting.enforce_single_session_student = (request.POST.get('enforce_single_session_student') == '1')
            setting.enforce_single_session_staff = (request.POST.get('enforce_single_session_staff') == '1')
            setting.save(update_fields=['enforce_single_session_student', 'enforce_single_session_staff'])
            messages.success(request, 'บันทึกนโยบายจำกัด 1 Session ต่อบัญชีเรียบร้อยแล้ว')
        elif action == 'revoke_session':
            sess = get_object_or_404(UserSession, pk=request.POST.get('session_id'))
            sess.is_active = False
            sess.save(update_fields=['is_active'])
            User.objects.filter(pk=sess.user_id, current_session_key=sess.session_token).update(
                current_session_key='', current_device_info=''
            )
            messages.info(request, f'ตัดการเชื่อมต่อ Session ของ {sess.user.get_full_name()} เรียบร้อยแล้ว')
        return redirect('core:admin_session_settings')

    active_sessions = UserSession.objects.filter(is_active=True).select_related('user').order_by('-last_active')[:60]
    return render(request, 'admin_panel/session_settings.html', {
        'setting': setting,
        'active_sessions': active_sessions,
    })


@admin_required
def admin_rank_settings_view(request):
    """ตั้งค่าระดับถ้วยรางวัลตามช่วงคะแนนรวมทั้ง 2 ช่วง (คิดเป็น % เต็ม 100%) พร้อมอัปโหลดรูปถ้วยรางวัลแทนได้"""
    tiers = ensure_default_score_rank_tiers()
    if request.method == 'POST':
        action = (request.POST.get('action') or 'save_ranks').strip()
        if action == 'reset_defaults':
            ensure_default_score_rank_tiers(reset=True)
            messages.success(request, 'คืนค่าระดับตามช่วงคะแนน (% และรูปถ้วยรางวัลมาตรฐาน) เรียบร้อยแล้ว')
            return redirect('core:admin_rank_settings')

        for tier in tiers:
            raw_name = (request.POST.get(f'name_{tier.id}') or '').strip()
            if raw_name:
                tier.name = raw_name[:60]
            op = (request.POST.get(f'operator_{tier.id}') or tier.operator).strip()
            if op in (ScoreRankTier.OP_GTE, ScoreRankTier.OP_GT):
                tier.operator = op
            raw_pct = (request.POST.get(f'min_percent_{tier.id}') or '').strip()
            if raw_pct != '':
                try:
                    tier.min_percent = max(0.0, min(100.0, round(float(raw_pct), 2)))
                except ValueError:
                    pass
            raw_col = (request.POST.get(f'color_{tier.id}') or '').strip()
            if re.match(r'^#[0-9a-fA-F]{6}$', raw_col):
                tier.color_hex = raw_col
            if request.POST.get(f'reset_icon_{tier.id}') == '1' and tier.custom_icon:
                tier.custom_icon.delete(save=False)
                tier.custom_icon = None
            up_icon = request.FILES.get(f'custom_icon_{tier.id}')
            if up_icon:
                tier.custom_icon = up_icon
            tier.save()

        messages.success(request, 'บันทึกการตั้งค่าระดับตามช่วงคะแนน (%) และรูปถ้วยรางวัลเรียบร้อยแล้ว')
        return redirect('core:admin_rank_settings')

    periods = list(ScorePeriod.objects.filter(academic_year=get_current_academic_year()).order_by('order_no', 'id'))
    overall_max = round(sum(float(p.max_score or 0.0) for p in periods), 2)
    return render(request, 'admin_panel/rank_settings.html', {
        'tiers': tiers,
        'periods': periods,
        'overall_max': overall_max,
    })


@admin_required
def admin_data_files_view(request):
    """เมนูไฟล์ข้อมูล & ขึ้นปีการศึกษาใหม่: สร้างปีการศึกษา คัดลอกบทเรียน/กิจกรรม รีเซ็ตเกม และลบข้อมูล/ไฟล์สะสมพร้อมตรวจรหัสผ่าน Admin ล่าสุด"""
    ensure_academic_year_exists()
    setting = SystemSetting.get()
    selected_year = (request.GET.get('year') or request.POST.get('filter_year') or '').strip()

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'create_academic_year':
            new_year = (request.POST.get('new_year') or '').strip()[:20]
            source_year = (request.POST.get('source_year') or setting.current_academic_year or '2569').strip()[:20]
            ay_title = (request.POST.get('title') or '').strip()[:120]
            try:
                ay = create_new_academic_year(
                    new_year, source_year=source_year, title=ay_title, created_by=request.user
                )
                messages.success(
                    request,
                    f'สร้างและเปิดใช้งานปีการศึกษาใหม่ "{ay.year}" เรียบร้อยแล้ว! '
                    f'ระบบรีเซ็ตเกมทั้งหมด (Sorting, Bingo, ID LOTTO) และแยกฐานข้อมูลนักเรียนเพื่อรองรับการอัปโหลดรายชื่อใหม่แล้ว '
                    f'กรุณาเลือกคัดลอกบทเรียน/กิจกรรมจากปี {source_year} ด้านล่าง'
                )
                return redirect(f'/admin-panel/data-files/?copy_from={source_year}&setup_new_year={ay.year}#copyFromPrevYearCard')
            except ValueError as ex:
                messages.error(request, str(ex))
            return redirect('core:admin_data_files')

        if action == 'copy_from_year':
            src_y = (request.POST.get('source_year') or '').strip()
            tgt_y = (request.POST.get('target_year') or setting.current_academic_year or '').strip()
            lesson_ids = request.POST.getlist('lesson_ids')
            activity_ids = request.POST.getlist('activity_ids')
            try:
                res = copy_lessons_and_activities_to_academic_year(
                    src_y,
                    tgt_y,
                    lesson_ids=lesson_ids,
                    activity_ids=activity_ids,
                    created_by=request.user,
                )
                skip_txt = (
                    f' (ข้ามรายการที่เคยคัดลอกแล้ว: บทเรียน {res["lessons_skipped"]}, กิจกรรม {res["activities_skipped"]})'
                    if (res['lessons_skipped'] or res['activities_skipped']) else ''
                )
                messages.success(
                    request,
                    f'คัดลอกจากปีการศึกษา {src_y} เข้าสู่ปีการศึกษา {tgt_y} สำเร็จ: '
                    f'บทเรียน {res["lessons_copied"]} บทเรียน, กิจกรรม {res["activities_copied"]} กิจกรรม{skip_txt}'
                )
            except ValueError as ex:
                messages.error(request, str(ex))
            return redirect(f'/admin-panel/data-files/?copy_from={src_y}&setup_new_year={tgt_y}#copyFromPrevYearCard')

        if action == 'switch_academic_year':
            tgt_y = (request.POST.get('target_year') or '').strip()
            try:
                ay = switch_current_academic_year(tgt_y)
                messages.success(request, f'สลับปีการศึกษาปัจจุบันเป็น "{ay.year}" เรียบร้อยแล้ว')
            except ValueError as ex:
                messages.error(request, str(ex))
            return redirect('core:admin_data_files')

        target_year = (request.POST.get('target_year') or '').strip()
        admin_pwd = request.POST.get('admin_password') or ''
        if not verify_admin_latest_password(request.user, admin_pwd):
            messages.error(request, 'รหัสผ่าน Admin ไม่ถูกต้อง! กรุณากรอกรหัสผ่านล่าสุดของบัญชี Admin เพื่อยืนยันการดำเนินการ')
            qs_y = f'?year={selected_year}' if selected_year else ''
            return redirect(f'/admin-panel/data-files/{qs_y}')

        if action == 'reset_year_games':
            rst_yr = target_year or (setting.current_academic_year or '2569')
            reset_all_games_for_academic_year(
                rst_yr,
                deactivate_previous=(rst_yr == get_current_academic_year()),
                created_by=request.user,
            )
            messages.success(request, f'รีเซ็ตเกมทั้งหมด (Sorting, Bingo, ID LOTTO) ของปีการศึกษา {rst_yr} เรียบร้อยแล้ว')
        elif action == 'delete_single_file':
            file_target = (request.POST.get('file_target') or '').strip()
            cnt, freed = delete_activity_files_for_year(target_year, file_target=file_target)
            messages.success(request, f'ลบไฟล์ที่เลือก ({cnt} รายการ) เรียบร้อยแล้ว')
        elif action == 'delete_year_files':
            if not target_year:
                messages.error(request, 'กรุณาระบุปีการศึกษาที่ต้องการลบไฟล์')
            else:
                cnt, _ = delete_activity_files_for_year(target_year)
                messages.success(request, f'ลบไฟล์สะสมการทำกิจกรรมทั้งหมดของปีการศึกษา {target_year} จำนวน {cnt} ไฟล์เรียบร้อยแล้ว')
        elif action == 'delete_year_data':
            if request.POST.get('confirm_delete_year') != '1':
                messages.error(request, 'กรุณาติ๊กยืนยันก่อนลบข้อมูลทั้งปีการศึกษา')
            elif not target_year:
                messages.error(request, 'กรุณาระบุปีการศึกษาที่ต้องการลบข้อมูล')
            else:
                res = delete_all_data_for_academic_year(target_year)
                messages.success(
                    request,
                    f'ลบข้อมูลและไฟล์กิจกรรมทั้งหมดของปีการศึกษา {target_year} เรียบร้อยแล้ว '
                    f'(ลบ {res["deleted_records"]} รายการ, ไฟล์ {res["deleted_files"]} ไฟล์, คืนพื้นที่ {res["freed_display"]})'
                )

        qs_y = f'?year={selected_year}' if selected_year else ''
        return redirect(f'/admin-panel/data-files/{qs_y}')

    summary = build_academic_year_files_summary(selected_year=selected_year)
    cur_yr = summary['current_academic_year']
    academic_years = list(AcademicYear.objects.order_by('-year', '-created_at'))
    ay_cur = next((ay for ay in academic_years if ay.year == cur_yr), None)
    suggested_next_year = str(int(cur_yr) + 1) if cur_yr.isdigit() else f'{cur_yr}_new'
    available_source_years = [y for y in summary['years'] if y != cur_yr]
    setup_new_year = (request.GET.get('setup_new_year') or '').strip()
    copy_source_year = (request.GET.get('copy_from') or '').strip()
    if not copy_source_year or copy_source_year == cur_yr:
        if ay_cur and ay_cur.copied_from_year and ay_cur.copied_from_year != cur_yr and ay_cur.copied_from_year in available_source_years:
            copy_source_year = ay_cur.copied_from_year
        elif available_source_years:
            copy_source_year = available_source_years[0]
        else:
            copy_source_year = ''

    source_lessons = []
    source_activities = []
    if copy_source_year:
        copied_lesson_ids = set(
            LessonContent.objects.filter(academic_year=cur_yr, copied_from__isnull=False).values_list('copied_from_id', flat=True)
        )
        copied_activity_ids = set(
            LearningActivity.objects.filter(academic_year=cur_yr, copied_from__isnull=False).values_list('copied_from_id', flat=True)
        )
        source_lessons = list(LessonContent.objects.filter(academic_year=copy_source_year).order_by('unit_number', 'id'))
        for l_obj in source_lessons:
            l_obj.already_copied = (l_obj.id in copied_lesson_ids)
        source_activities = list(
            LearningActivity.objects.filter(academic_year=copy_source_year).select_related('score_period').order_by('id')
        )
        for a_obj in source_activities:
            a_obj.already_copied = (a_obj.id in copied_activity_ids)

    return render(request, 'admin_panel/data_files.html', {
        'setting': setting,
        'selected_year': selected_year,
        'academic_years': academic_years,
        'suggested_next_year': suggested_next_year,
        'setup_new_year': setup_new_year,
        'copy_source_year': copy_source_year,
        'available_source_years': available_source_years,
        'source_lessons': source_lessons,
        'source_activities': source_activities,
        **summary,
    })
