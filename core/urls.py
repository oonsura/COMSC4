from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # หน้าแรก & ระบบล็อกอิน
    path('', views.home_redirect_view, name='home'),
    path('login/', views.login_view, name='login'),
    path('login/google-oauth/', views.google_oauth_login_view, name='google_oauth_login'),
    path('login/session-conflict/', views.session_conflict_view, name='session_conflict'),
    path('logout/', views.logout_view, name='logout'),

    # หน้าสำหรับนักเรียน
    path('student/dashboard/', views.student_dashboard_view, name='student_dashboard'),
    path('student/profile/', views.student_profile_view, name='student_profile'),
    path('student/lessons/', views.student_lessons_view, name='student_lessons'),
    path('student/quiz/<int:quiz_id>/', views.student_take_quiz_view, name='student_take_quiz'),
    path('student/activities/', views.student_activities_view, name='student_activities'),
    path('student/attendance/', views.student_attendance_view, name='student_attendance'),
    path('student/scores/', views.student_scores_view, name='student_scores'),

    # หน้าเกมและกิจกรรมเสริมสำหรับนักเรียน
    path('student/sorting/', views.student_sorting_view, name='student_sorting'),
    path('student/bingo/', views.student_bingo_list_view, name='student_bingo_list'),
    path('student/bingo/<int:room_id>/', views.student_bingo_play_view, name='student_bingo_play'),
    path('student/lotto/', views.student_lotto_view, name='student_lotto'),

    # หน้าสำหรับผู้ดูแล/ครู (Teacher / Staff)
    path('teacher/dashboard/', views.teacher_dashboard_view, name='teacher_dashboard'),
    path('teacher/students/', views.teacher_students_view, name='teacher_students'),
    path('teacher/students/import/', views.teacher_import_students_view, name='teacher_import_students'),
    path('teacher/students/template/', views.teacher_students_template_view, name='teacher_students_template'),
    path('teacher/students/<int:user_id>/edit/', views.teacher_student_edit_view, name='teacher_student_edit'),
    path('teacher/lessons-quizzes/', views.teacher_lessons_quizzes_view, name='teacher_lessons_quizzes'),
    path('teacher/activities/', views.teacher_activities_view, name='teacher_activities'),
    path('teacher/activities/new/', views.teacher_activity_editor_view, name='teacher_activity_create'),
    path('teacher/activities/<int:activity_id>/edit/', views.teacher_activity_editor_view, name='teacher_activity_edit'),
    path('teacher/presentations/', views.teacher_presentations_view, name='teacher_presentations'),
    path('teacher/submissions/', views.teacher_submissions_view, name='teacher_submissions'),
    path('teacher/submissions/export/', views.teacher_submissions_export_view, name='teacher_submissions_export'),
    path('teacher/attendance/', views.teacher_attendance_view, name='teacher_attendance'),
    path('teacher/scores/', views.teacher_scores_view, name='teacher_scores'),

    # หน้าจัดการระบบเกมสำหรับผู้ดูแล/ครู
    path('teacher/sorting/', views.teacher_sorting_manage_view, name='teacher_sorting_manage'),
    path('teacher/sorting/<int:activity_id>/summary/', views.teacher_sorting_summary_view, name='teacher_sorting_summary'),
    path('teacher/sorting/<int:activity_id>/export-csv/', views.teacher_sorting_export_csv_view, name='teacher_sorting_export_csv'),
    path('teacher/bingo/', views.teacher_bingo_manage_view, name='teacher_bingo_manage'),
    path('teacher/bingo/<int:room_id>/control/', views.teacher_bingo_control_view, name='teacher_bingo_control'),
    path('teacher/bingo/stats/', views.teacher_bingo_stats_view, name='teacher_bingo_stats'),
    path('teacher/lotto/', views.teacher_lotto_manage_view, name='teacher_lotto_manage'),

    # หน้าสำหรับ Admin (ผู้ดูแลระบบสูงสุด)
    path('admin-panel/staff/', views.admin_manage_staff_view, name='admin_manage_staff'),
    path('admin-panel/oauth-settings/', views.admin_oauth_settings_view, name='admin_oauth_settings'),
    path('admin-panel/session-settings/', views.admin_session_settings_view, name='admin_session_settings'),
    path('admin-panel/score-ranks/', views.admin_rank_settings_view, name='admin_rank_settings'),
    path('admin-panel/data-files/', views.admin_data_files_view, name='admin_data_files'),

    # Real-time JSON APIs (สำหรับ แผนผังที่นั่ง/Login/เช็คหนังสือ-สมุด, Sorting, Bingo, Lotto)
    path('api/classroom/<int:classroom_id>/seating-live/', views.api_classroom_seating_live, name='api_classroom_seating_live'),
    path('api/classroom/<int:classroom_id>/seat-check/', views.api_classroom_seat_check, name='api_classroom_seat_check'),
    path('api/classroom/<int:classroom_id>/attendance-batch-score/', views.api_classroom_attendance_batch_score, name='api_classroom_attendance_batch_score'),
    path('api/sorting/<int:activity_id>/heartbeat/', views.api_sorting_heartbeat, name='api_sorting_heartbeat'),
    path('api/sorting/<int:activity_id>/progress/', views.api_sorting_progress, name='api_sorting_progress'),
    path('api/sorting/<int:activity_id>/submit/', views.api_sorting_submit, name='api_sorting_submit'),
    path('api/sorting/<int:activity_id>/broadcast-play/', views.api_sorting_broadcast_play, name='api_sorting_broadcast_play'),
    path('api/sorting/<int:activity_id>/live-board/', views.api_sorting_live_board, name='api_sorting_live_board'),
    path('api/sorting/<int:activity_id>/student-history/<int:student_id>/', views.api_sorting_student_history, name='api_sorting_student_history'),
    path('api/bingo/<int:room_id>/state/', views.api_bingo_state, name='api_bingo_state'),
    path('api/bingo/<int:room_id>/draw/', views.api_bingo_draw, name='api_bingo_draw'),
    path('api/bingo/<int:room_id>/grant-picker/', views.api_bingo_grant_picker, name='api_bingo_grant_picker'),
    path('api/bingo/<int:room_id>/student-accept/', views.api_bingo_student_accept, name='api_bingo_student_accept'),
    path('api/bingo/<int:room_id>/student-pick/', views.api_bingo_student_pick, name='api_bingo_student_pick'),
    path('api/bingo/<int:room_id>/student-forfeit/', views.api_bingo_student_forfeit, name='api_bingo_student_forfeit'),
    path('api/lotto/<int:session_id>/draw/', views.api_lotto_draw, name='api_lotto_draw'),
    path('api/lotto/<int:session_id>/remove-result/', views.api_lotto_remove_result, name='api_lotto_remove_result'),
    path('api/lotto/<int:session_id>/finalize/', views.api_lotto_finalize, name='api_lotto_finalize'),
    path('api/lotto/<int:session_id>/state/', views.api_lotto_state, name='api_lotto_state'),
    path('api/activities/upload-media/', views.api_activity_upload_media, name='api_activity_upload_media'),
    path('api/activities/verify-gdrive/', views.api_activity_verify_gdrive, name='api_activity_verify_gdrive'),
    path('api/activities/<int:activity_id>/ai-check/', views.api_activity_ai_check, name='api_activity_ai_check'),
    path('api/activities/<int:activity_id>/toggle-answer-item/', views.api_activity_toggle_answer_item, name='api_activity_toggle_answer_item'),
    path('api/activities/<int:activity_id>/group-move/', views.api_activity_group_move, name='api_activity_group_move'),
    path('api/student/ui-pref/', views.api_student_ui_pref, name='api_student_ui_pref'),
]
