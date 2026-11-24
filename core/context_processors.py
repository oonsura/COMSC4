from .models import (
    ActivityExtensionRequest,
    ActivitySubmission,
    BingoRoom,
    LearningActivity,
    LessonContent,
    LottoSession,
    SortingActivity,
    SystemSetting,
)
from .services import compute_student_rank_overview, get_user_ui_preferences


def site_context(request):
    setting = SystemSetting.get()
    cur_yr = (setting.current_academic_year or '2569').strip() or '2569'
    active_sorting_count = SortingActivity.objects.filter(academic_year=cur_yr, is_active=True).count()
    active_bingo_count = BingoRoom.objects.filter(academic_year=cur_yr, status__in=['open', 'playing']).count()
    active_lotto_count = LottoSession.objects.filter(academic_year=cur_yr, is_open=True).count()
    pending_extension_requests_count = ActivityExtensionRequest.objects.filter(
        status=ActivityExtensionRequest.STATUS_PENDING,
        activity__academic_year=cur_yr,
    ).count()
    theme_colors = setting.get_theme_colors()

    nav_lessons = []
    nav_activities = []
    student_pending_activities_count = 0
    student_rank_overview = None
    student_ui_prefs = {}
    student_sidebar_open = {}
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and getattr(user, 'role', '') == 'student':
        user_rc = getattr(user, 'room_code', '')
        nav_lessons = list(
            LessonContent.objects.filter(academic_year=cur_yr, is_published=True).order_by('unit_number', 'id')
        )
        nav_activities = [
            act for act in LearningActivity.objects.filter(academic_year=cur_yr).order_by('id')
            if act.applies_to_room(user_rc)
        ]
        submitted_ids = set(
            ActivitySubmission.objects.filter(student=user).values_list('activity_id', flat=True)
        )
        for act in nav_activities:
            act.is_submitted_by_me = (act.id in submitted_ids)
        student_pending_activities_count = sum(1 for a in nav_activities if not a.is_submitted_by_me)
        student_rank_overview = compute_student_rank_overview(user)
        student_ui_prefs = get_user_ui_preferences(user)
        student_sidebar_open = student_ui_prefs.get('sidebar_open') or {}

    return {
        'site_setting': setting,
        'theme_colors': theme_colors,
        'active_sorting_count': active_sorting_count,
        'active_bingo_count': active_bingo_count,
        'active_lotto_count': active_lotto_count,
        'pending_extension_requests_count': pending_extension_requests_count,
        'nav_lessons': nav_lessons,
        'nav_activities': nav_activities,
        'student_pending_activities_count': student_pending_activities_count,
        'student_rank_overview': student_rank_overview,
        'student_ui_prefs': student_ui_prefs,
        'student_sidebar_open': student_sidebar_open,
    }
