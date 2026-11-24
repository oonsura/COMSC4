"""
ชุดทดสอบอัตโนมัติ (Automated Test Suite) สำหรับระบบ COMSC4
ครอบคลุมการตรวจสอบทุกเงื่อนไขตามสเปก:
1. นักเรียนล็อกอินด้วย Google OAuth รูปแบบ s[เลขประจำตัว 5 หลัก]@debsirin.ac.th, ปฏิเสธโดเมนอื่น, 1-Session Guard (เลือกใช้งานต่อที่อุปกรณ์ใหม่หรือคงอุปกรณ์เดิม), และการหมุน/Crop รูปโปรไฟล์
2. , C, D, E: บทเรียน, แบบทดสอบ AI, กิจกรรมการเรียนรู้, เช็คชื่อด้วยโค้ด + แผนผังที่นั่ง, และระบบกฎคะแนนอัตโนมัติ
3. (DSBasicSorting) เรียงลำดับข้อมูล, คำนวณคะแนน, และโหมดสอบแจกคะแนนบวกให้ผู้เล่นสูงสุด Top X คนแรก
4. (Bingo) สร้างตาราง Bingo, Guard ตรวจสอบการเลือกเลขของนักเรียน (ห้ามเลือกเลขมุม/ตรงกลาง และ Simulation Guard ห้ามเลือกเลขที่ทำให้ตัวเอง Bingo ทันที), และการแจกคะแนนผู้ชนะ
5. (Debsirin Lotto) สุ่มนักเรียนด้วยเลขประจำตัว 5 หลัก + บวกคะแนนอัตโนมัติ + ลบออกจากรายการสุ่มพร้อมบันทึกเหตุผล
"""
import base64
import io
import json
from datetime import timedelta
from pathlib import Path
import openpyxl
from PIL import Image
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .excel_import import import_students_from_preview, preview_students
from .models import (
    AcademicYear,
    AIQuiz, ActivityAnswerKeyItem, ActivityBulkGradeBatch, ActivityExtensionRequest,
    ActivityGradingTier, ActivityGroup, ActivityGroupMember,
    ActivityPresentationDraw, ActivitySubmission,
    AttendanceRecord, AttendanceSession,
    BingoCard, BingoDrawnNumber, BingoRoom, BingoWinner,
    Classroom, LabRoomLayout, LearningActivity, LessonContent, LottoResult, LottoSession,
    ScoreLog, ScorePeriod, ScoreRankTier, ScoreRule, SortingActivity, SortingExamRecord, SortingScore,
    StudentMaterialCheck, SystemSetting, User, UserSession,
)
from .services import (
    auto_close_expired_attendance_sessions,
    award_points_by_rule,
    build_auto_seat_numbers,
    compute_group_max_size,
    compute_student_period_scores,
    compute_student_rank_overview,
    draw_next_bingo_number,
    ensure_default_score_rank_tiers,
    get_effective_drawn_list_and_set,
    get_next_activity_bulk_grade_info,
    get_student_activity_access,
    resolve_score_rank,
    seed_initial_comsc4_data,
    seed_sample_activities_room01,
    validate_student_bingo_pick,
)


class Comsc4FullSystemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_initial_comsc4_data()

    def test_module_a_google_oauth_and_single_session_choice(self):
        c1 = Client()
        # 1. ทดสอบปฏิเสธโดเมนที่ไม่ใช่ @debsirin.ac.th
        resp_bad = c1.post(reverse('core:google_oauth_login'), {'google_email': 's51443@gmail.com'}, follow=True)
        self.assertContains(resp_bad, 'ต้องใช้อีเมลโรงเรียนโดเมน @debsirin.ac.th เท่านั้น')

        # 2. ทดสอบล็อกอินสำเร็จด้วย s51443@debsirin.ac.th (นักเรียนจริงใน Room 01)
        resp_ok = c1.post(reverse('core:google_oauth_login'), {'google_email': 's51443@debsirin.ac.th'}, follow=True)
        self.assertEqual(resp_ok.status_code, 200)
        st = User.objects.get(student_id='51443')
        self.assertTrue(st.current_session_key)

        # 3. ทดสอบล็อกอินซ้ำจากอุปกรณ์ที่ 2 (c2) -> ระบบต้องพาไปหน้าเลือกอุปกรณ์ (session_conflict)
        c2 = Client()
        resp_conflict = c2.post(reverse('core:google_oauth_login'), {'google_email': 's51443@debsirin.ac.th'})
        self.assertRedirects(resp_conflict, reverse('core:session_conflict'))

        # 4. อุปกรณ์ที่ 2 เลือก "ใช้งานต่อที่อุปกรณ์ใหม่ (new_device)"
        resp_takeover = c2.post(reverse('core:session_conflict'), {'choice': 'new_device'}, follow=True)
        self.assertEqual(resp_takeover.status_code, 200)

        # 5. อุปกรณ์เดิม (c1) เมื่อเปิดหน้าแดชบอร์ดจะถูกเตะออกทันที
        resp_kicked = c1.get(reverse('core:student_dashboard'))
        self.assertRedirects(resp_kicked, reverse('core:login'))

    def test_module_a4_avatar_rotate_and_crop(self):
        c = Client()
        st = User.objects.get(student_id='51443')
        c.force_login(st)

        img = Image.new('RGB', (300, 200), color=(22, 163, 74))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        b64_str = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')

        resp = c.post(reverse('core:student_profile'), {
            'action': 'upload_cropped_avatar',
            'cropped_image_data': b64_str,
            'rotate_deg': '90',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        st.refresh_from_db()
        self.assertTrue(bool(st.profile_photo))

    def test_module_f_sorting_and_exam_top_x_reward(self):
        act = SortingActivity.objects.first()
        act.is_exam_mode = True
        act.top_x_reward = 2
        act.save()

        st1 = User.objects.get(student_id='51443')
        st2 = User.objects.exclude(student_id='51443').filter(role=User.ROLE_STUDENT).first()
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()

        c1 = Client()
        c1.force_login(st1)
        res = c1.post(reverse('core:api_sorting_submit', args=[act.id]), {
            'right_rounds': 7, 'attempts': 0, 'time_used': 14.0, 'unit_count': 7
        }).json()
        self.assertTrue(res['ok'])
        self.assertEqual(res['score'], 68.0)

        SortingScore.objects.create(
            activity=act, student=st2, score=55.0, time_used=25.0, attempts=1, status='completed', is_exam_mode=True)

        # ครูกดปิดโหมดสอบและแจกคะแนน Top X
        ct = Client()
        ct.force_login(teacher)
        ct.post(reverse('core:teacher_sorting_summary', args=[act.id]), {
            'action': 'reward_top_x', 'top_x_reward': 2
        })
        st1.refresh_from_db()
        self.assertGreater(st1.total_score, 0)

    def test_module_g_bingo_student_pick_guards_and_winner(self):
        room = BingoRoom.objects.first()
        room.size = 3
        room.number_range = '1-20'
        room.win_patterns = 'horizontal'
        room.allow_student_pick = True
        room.save()

        st = User.objects.get(student_id='51443')
        # ตาราง 3x3:
        # [ 1(มุม),  2,      3(มุม) ]
        # [ 4,       5(กลาง), 6      ]
        # [ 7(มุม),  8,      9(มุม) ]
        grid = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        BingoCard.objects.update_or_create(
            room=room, student=st,
            defaults={'card_data': json.dumps(grid), 'is_locked': True})

        # 1) ทดสอบเลือกเลขที่ไม่อยู่ในตารางของตนเอง (15) -> ต้องถูกปฏิเสธ
        ok, msg = validate_student_bingo_pick(room, st, 15)
        self.assertFalse(ok)
        self.assertIn('ไม่ได้อยู่ในตาราง', msg)

        # 2) ทดสอบเลือกเลขตำแหน่งมุมตาราง (1) -> ต้องถูกปฏิเสธ
        ok, msg = validate_student_bingo_pick(room, st, 1)
        self.assertFalse(ok)
        self.assertIn('มุมตาราง', msg)

        # 3) ทดสอบเลือกเลขตำแหน่งกลางตาราง (5) -> ต้องถูกปฏิเสธ
        ok, msg = validate_student_bingo_pick(room, st, 5)
        self.assertFalse(ok)
        self.assertIn('กลางตาราง', msg)

        # 4) ทดสอบเลือกเลขปกติที่ไม่ใช่มุม/กลาง และยังไม่ทำให้ Bingo ทันที (เลข 4) -> ต้องผ่าน!
        ok, msg = validate_student_bingo_pick(room, st, 4)
        self.assertTrue(ok)

        # 5) ทดสอบ Simulation Guard: สมมติว่าเลข 4 และ 5 ถูกเปิดไปแล้วในแถวกลาง [4, 5, 6]
        # หากนักเรียนพยายามเลือกเลข 6 ซึ่งจะทำให้แถว [4, 5, 6] ชนะ Bingo ทันที -> ระบบต้องปฏิเสธ!
        BingoDrawnNumber.objects.create(room=room, number=4, drawn_by_mode='normal')
        BingoDrawnNumber.objects.create(room=room, number=5, drawn_by_mode='normal')
        ok_win, msg_win = validate_student_bingo_pick(room, st, 6)
        self.assertFalse(ok_win)
        self.assertIn('Simulation Guard', msg_win)

    def test_module_h_debsirin_lotto_draw_and_remove_reason(self):
        sess = LottoSession.objects.first()
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        draw_res = ct.post(reverse('core:api_lotto_draw', args=[sess.id])).json()
        self.assertTrue(draw_res['ok'])
        self.assertEqual(len(draw_res['student_id']), 5)

        res_id = draw_res['result_id']
        rm_res = ct.post(reverse('core:api_lotto_remove_result', args=[sess.id]), {
            'result_id': res_id,
            'reason': 'นักเรียนลาป่วยไม่อยู่ในห้อง',
        }).json()
        self.assertTrue(rm_res['ok'])
        lotto_obj = LottoResult.objects.get(pk=res_id)
        self.assertTrue(lotto_obj.removed)
        self.assertEqual(lotto_obj.removed_reason, 'นักเรียนลาป่วยไม่อยู่ในห้อง')

    def test_csv_import_dscomposn_workflow(self):
        csv_path = Path(settings.BASE_DIR) / 'students_data.csv'
        self.assertTrue(csv_path.exists())

        with open(csv_path, 'rb') as fp:
            pdata = preview_students(fp)

        self.assertEqual(pdata['total'], 506)
        self.assertEqual(pdata['valid_count'], 506)
        self.assertEqual(pdata['invalid_count'], 0)
        res = import_students_from_preview(pdata)
        self.assertEqual(res['total'], 506)
        self.assertEqual(len(res['errors']), 0)

        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)
        with open(csv_path, 'rb') as fp:
            resp_prev = ct.post(reverse('core:teacher_import_students'), {'action': 'preview', 'file': fp})
            self.assertEqual(resp_prev.status_code, 200)
            self.assertContains(resp_prev, 'ตรวจสอบผลการอ่านข้อมูล (506 แถว)')
        resp_conf = ct.post(reverse('core:teacher_import_students'), {'action': 'confirm_import'}, follow=True)
        self.assertEqual(resp_conf.status_code, 200)
        self.assertContains(resp_conf, 'Import สำเร็จ')

    def test_bingo_custom_size_grade_wide_continuous_per_room(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # สร้างห้อง Bingo ขนาดกำหนดเอง 6x6 เล่นพร้อมกันทั้งระดับ ต่อเนื่องไม่หยุด
        resp = ct.post(reverse('core:teacher_bingo_manage'), {
            'action': 'create_room',
            'title': 'Bingo 6x6 ทั้งระดับ ม.4',
            'is_grade_wide': '1',
            'continuous_play': '1',
            'size': '6',
            'number_range': '1-50',
            'win_patterns': ['horizontal'],
            'allow_student_pick': '1',
        })
        self.assertEqual(resp.status_code, 302)
        room = BingoRoom.objects.get(title='Bingo 6x6 ทั้งระดับ ม.4')
        self.assertEqual(room.size, 6)
        self.assertTrue(room.is_grade_wide)
        self.assertTrue(room.continuous_play)

        st_r1 = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').first()
        st_r2 = User.objects.filter(role=User.ROLE_STUDENT, room_code='02').first()
        self.assertIsNotNone(st_r1)
        self.assertIsNotNone(st_r2)

        # ตาราง 6x6: แถวแรกของห้อง 01 คือ 1..6, แถวแรกของห้อง 02 คือ 7..12
        grid_r1 = list(range(1, 37))
        grid_r2 = list(range(7, 43))
        BingoCard.objects.create(room=room, student=st_r1, card_data=json.dumps(grid_r1), is_locked=True)
        BingoCard.objects.create(room=room, student=st_r2, card_data=json.dumps(grid_r2), is_locked=True)

        # ออกเลข 1..6 ให้ห้อง 01 ชนะก่อน -> เกมต้องยังเล่นต่อได้ (status == playing)
        for num in range(1, 7):
            draw_next_bingo_number(room, mode=BingoDrawnNumber.MODE_MANUAL, chosen_number=num, teacher=teacher)

        room.refresh_from_db()
        self.assertEqual(room.status, BingoRoom.STATUS_PLAYING)
        w1 = BingoWinner.objects.get(room=room, student=st_r1)
        self.assertEqual(w1.room_code, '01')
        self.assertEqual(w1.room_rank_order, 1)

        # ออกเลข 7..12 ต่อเนื่องเพื่อหาผู้ชนะของห้อง 02
        for num in range(7, 13):
            draw_next_bingo_number(room, mode=BingoDrawnNumber.MODE_MANUAL, chosen_number=num, teacher=teacher)

        room.refresh_from_db()
        self.assertEqual(room.status, BingoRoom.STATUS_PLAYING)
        w2 = BingoWinner.objects.get(room=room, student=st_r2)
        self.assertEqual(w2.room_code, '02')
        self.assertEqual(w2.room_rank_order, 1)

        # ครูกดเริ่มรอบใหม่ -> เลขที่ออกและผู้ชนะถูกล้างพร้อมเล่นรอบถัดไป
        ct.post(reverse('core:teacher_bingo_control', args=[room.id]), {'action': 'reset_drawn'})
        room.refresh_from_db()
        self.assertEqual(room.status, BingoRoom.STATUS_OPEN)
        self.assertEqual(room.drawn_numbers.count(), 0)
        self.assertEqual(room.winners.count(), 0)

    def test_classroom_seating_layout_customization(self):
        room = Classroom.objects.get(code='01')
        self.assertEqual(room.board_position, Classroom.BOARD_BOTTOM)
        self.assertEqual(room.get_row_order(), 'bottom_to_top')
        self.assertEqual(room.get_rows_sequence()[0], room.seat_rows)

        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # สลับกระดานดำไปด้านบนแล้วกลับมาด้านล่าง
        ct.post(reverse('core:teacher_attendance'), {'action': 'quick_toggle_board', 'classroom_id': room.id})
        room.refresh_from_db()
        self.assertEqual(room.board_position, Classroom.BOARD_TOP)
        self.assertEqual(room.get_row_order(), 'top_to_bottom')

        ct.post(reverse('core:teacher_attendance'), {'action': 'quick_toggle_board', 'classroom_id': room.id})
        room.refresh_from_db()
        self.assertEqual(room.board_position, Classroom.BOARD_BOTTOM)
        self.assertEqual(room.get_row_order(), 'bottom_to_top')

        # ปรับขนาดผังเป็น 5x8, ตั้งป้ายชื่อ PC-01 ที่ 1_1, ปิดที่นั่ง 1_4 เป็นทางเดิน
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'update_classroom_layout',
            'classroom_id': room.id,
            'seat_rows': '5',
            'seat_cols': '8',
            'board_position': 'bottom',
            'row_order': 'bottom_to_top',
            'disabled_seats_csv': '',
        })
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'set_seat_label',
            'classroom_id': room.id,
            'seat_key': '1_1',
            'seat_label': 'PC-01',
        })
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'toggle_single_seat',
            'classroom_id': room.id,
            'seat_key': '1_4',
        })

        room.refresh_from_db()
        self.assertEqual(room.seat_rows, 5)
        self.assertEqual(room.seat_cols, 8)
        self.assertEqual(room.get_custom_labels_dict().get('1_1'), 'PC-01')
        self.assertIn('1_4', room.get_disabled_seats_set())

        # นักเรียนเช็คชื่อ: ที่นั่งทางเดิน (1_4) ต้องถูกปฏิเสธ, ที่นั่งปกติ (1_1) ต้องสำเร็จ
        sess = AttendanceSession.objects.create(
            classroom=room, title='ทดสอบผังที่นั่ง', check_in_code='TEST99', is_open=True, created_by=teacher)
        st = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').first()
        cs = Client()
        cs.force_login(st)

        resp_disabled = cs.post(reverse('core:student_attendance'), {
            'check_in_code': 'TEST99', 'seat_row': 1, 'seat_col': 4
        }, follow=True)
        self.assertContains(resp_disabled, 'ทางเดินหรือที่นั่งที่ปิดใช้งาน')
        self.assertFalse(AttendanceRecord.objects.filter(session=sess, student=st).exists())

        resp_ok = cs.post(reverse('core:student_attendance'), {
            'check_in_code': 'TEST99', 'seat_row': 1, 'seat_col': 1
        }, follow=True)
        self.assertContains(resp_ok, 'เช็คชื่อสำเร็จ')
        self.assertTrue(AttendanceRecord.objects.filter(session=sess, student=st, seat_row=1, seat_col=1).exists())

    def test_special_student_99999_login_and_last_class_number(self):
        st99 = User.objects.get(student_id='99999')
        self.assertEqual(st99.username, '99999')
        self.assertEqual(st99.title_prefix, 'นาย')
        self.assertEqual(st99.first_name, 'สมชาย')
        self.assertEqual(st99.last_name, 'ใจดี')
        self.assertEqual(st99.room_code, '01')
        self.assertTrue(st99.allow_password_login)

        room01_students = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')
        self.assertEqual(room01_students.count(), 30)
        self.assertEqual(room01_students.last().student_id, '99999')
        self.assertEqual(st99.class_number, 30)

        # ล็อกอินกรณีพิเศษด้วย username=99999, password=1234
        c = Client()
        resp = c.post(reverse('core:login'), {
            'login_mode': 'staff', 'username': '99999', 'password': '1234'
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'สมชาย ใจดี')

        # นักเรียนปกติที่ไม่มีสิทธิ์ allow_password_login ห้ามล็อกอินด้วยรหัสผ่าน
        normal_st = User.objects.get(student_id='51443')
        normal_st.set_password('1234')
        normal_st.save()
        c_norm = Client()
        resp_norm = c_norm.post(reverse('core:login'), {
            'login_mode': 'staff', 'username': 's51443', 'password': '1234'
        }, follow=True)
        self.assertContains(resp_norm, 'เฉพาะผู้ดูแลหรือบัญชีกรณีพิเศษเท่านั้น')

    def test_dsbasicsorting_form1_form2_parity_and_no_numericupdown(self):
        act = SortingActivity.objects.first()
        st99 = User.objects.get(student_id='99999')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()

        cs = Client()
        cs.force_login(st99)
        ct = Client()
        ct.force_login(teacher)

        # 1. ตรวจสอบว่าไม่มี <input type="number"> (numericUpDown) ในหน้าเกม Sorting ทั้งฝั่งนักเรียนและครู
        for html_resp in [
            cs.get(reverse('core:student_sorting')),
            ct.get(reverse('core:teacher_sorting_manage')),
            ct.get(reverse('core:teacher_sorting_summary', args=[act.id])),
        ]:
            self.assertEqual(html_resp.status_code, 200)
            body_text = html_resp.content.decode('utf-8')
            self.assertNotIn('type="number"', body_text)
            self.assertNotIn("type='number'", body_text)

        # 2. นักเรียนส่ง heartbeat ออนไลน์ -> ครูเห็นใน listBox1 (players)
        hb1 = cs.post(reverse('core:api_sorting_heartbeat', args=[act.id])).json()
        self.assertTrue(hb1['ok'])
        board1 = ct.get(reverse('core:api_sorting_live_board', args=[act.id])).json()
        self.assertEqual(board1['online_count'], 1)
        self.assertEqual(board1['players'][0]['student_id'], '99999')
        self.assertEqual(board1['players'][0]['state'], 'online')

        # 3. ครูกด PLAY สั่งเริ่มพร้อมกัน (สุ่ม 5-9 ตัว = 4, Bubble, น้อย>>มาก)
        play_res = ct.post(reverse('core:api_sorting_broadcast_play', args=[act.id]), {
            'sort_type': 'bubble', 'sort_order': 'asc', 'unit_count': 4, 'room_code': ''
        }).json()
        self.assertTrue(play_res['ok'])
        hb2 = cs.post(reverse('core:api_sorting_heartbeat', args=[act.id])).json()
        self.assertEqual(hb2['broadcast_seq'], play_res['broadcast_seq'])
        self.assertEqual(hb2['session_no'], play_res['session_no'])

        # 4. นักเรียนเริ่มทำและส่ง Progress -> ครูเห็นกำลังทำรอบ
        cs.post(reverse('core:api_sorting_progress', args=[act.id]), {
            'current_round': 3, 'total_rounds': 7, 'attempts': 1, 'time_used': 8
        })
        board2 = ct.get(reverse('core:api_sorting_live_board', args=[act.id])).json()
        self.assertEqual(board2['players'][0]['state'], 'playing')
        self.assertIn('กำลังทำรอบ 3/7', board2['players'][0]['status_text'])

        # 5. นักเรียนส่งคะแนน 2 รอบติดกัน -> คะแนนสะสมต้องรวมกัน และ listBox2 มี 1 แถว ส่วน listBox3 มีครบ 2 รอบ
        sub1 = cs.post(reverse('core:api_sorting_submit', args=[act.id]), {
            'right_rounds': 7, 'attempts': 0, 'time_used': 14.0, 'unit_count': 7,
            'sort_type': 'bubble', 'sort_order': 'asc', 'session_no': play_res['session_no']
        }).json()
        self.assertEqual(sub1['score'], 68.0)
        self.assertEqual(sub1['cumulative_score'], 68.0)

        sub2 = cs.post(reverse('core:api_sorting_submit', args=[act.id]), {
            'right_rounds': 5, 'attempts': 1, 'time_used': 10.0, 'unit_count': 5,
            'sort_type': 'selection', 'sort_order': 'desc', 'session_no': play_res['session_no']
        }).json()
        self.assertEqual(sub2['score'], 46.0)
        self.assertEqual(sub2['cumulative_score'], 114.0)
        self.assertEqual(sub2['play_round_no'], 2)

        board3 = ct.get(reverse('core:api_sorting_live_board', args=[act.id])).json()
        self.assertEqual(len(board3['leaderboard']), 1)
        self.assertEqual(board3['leaderboard'][0]['cumulative_score'], 114.0)

        hist_res = ct.get(reverse('core:api_sorting_student_history', args=[act.id, st99.pk])).json()
        self.assertEqual(len(hist_res['rounds']), 2)
        self.assertEqual(hist_res['cumulative_score'], 114.0)

        # 6. Export CSV
        csv_resp = ct.get(reverse('core:teacher_sorting_export_csv', args=[act.id]))
        self.assertEqual(csv_resp.status_code, 200)
        csv_text = csv_resp.content.decode('utf-8-sig')
        self.assertIn('สมชาย ใจดี', csv_text)
        self.assertIn('114.0', csv_text)

    def test_bingo_per_room_draw_filter_and_hit_statistics(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        room = BingoRoom.objects.create(
            title='Bingo แยกออกเลขรายห้อง & สถิติ',
            is_grade_wide=True,
            continuous_play=True,
            size=3,
            number_range='1-20',
            win_patterns='horizontal',
            status=BingoRoom.STATUS_OPEN,
            created_by=teacher,)

        r1_students = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:2])
        r2_student = User.objects.filter(role=User.ROLE_STUDENT, room_code='02').order_by('class_number').first()
        BingoCard.objects.create(room=room, student=r1_students[0], card_data=json.dumps([1, 2, 3, 4, 5, 6, 7, 8, 9]), is_locked=True)
        BingoCard.objects.create(room=room, student=r1_students[1], card_data=json.dumps([10, 11, 12, 13, 14, 15, 16, 17, 18]), is_locked=True)
        BingoCard.objects.create(room=room, student=r2_student, card_data=json.dumps([1, 10, 11, 12, 13, 14, 15, 16, 17]), is_locked=True)

        # 1. ออกเลข 1 เฉพาะห้อง 01 -> ต้องนับคนได้รับเลขเฉพาะห้อง 01 (1 จาก 2 คน = 50%)
        d1 = ct.post(reverse('core:api_bingo_draw', args=[room.id]), {
            'mode': BingoDrawnNumber.MODE_MANUAL, 'number': 1, 'target_room_code': '01'
        }).json()
        self.assertTrue(d1['ok'])
        self.assertEqual(d1['hit_stats']['hit_count'], 1)
        self.assertEqual(d1['hit_stats']['eligible_cards_count'], 2)
        self.assertEqual(d1['hit_stats']['hit_percent'], 50.0)

        # ห้อง 01 เห็นเลข 1 แต่ห้อง 02 ยังไม่เห็นเลข 1
        state_r1 = ct.get(reverse('core:api_bingo_state', args=[room.id]) + '?room_filter=01').json()
        state_r2 = ct.get(reverse('core:api_bingo_state', args=[room.id]) + '?room_filter=02').json()
        self.assertIn(1, state_r1['drawn_numbers'])
        self.assertNotIn(1, state_r2['drawn_numbers'])

        # 2. ห้อง 02 ออกเลข 1 แยกต่างหากได้ และสถิติของห้อง 02 คือ 1 จาก 1 คน = 100%
        d2 = ct.post(reverse('core:api_bingo_draw', args=[room.id]), {
            'mode': BingoDrawnNumber.MODE_MANUAL, 'number': 1, 'target_room_code': '02'
        }).json()
        self.assertTrue(d2['ok'])
        self.assertEqual(d2['hit_stats']['hit_count'], 1)
        self.assertEqual(d2['hit_stats']['eligible_cards_count'], 1)
        self.assertEqual(d2['hit_stats']['hit_percent'], 100.0)

        # 3. ออกเลข 10 แบบทั้งระดับ ('all') -> มีผู้ได้รับ 2 จาก 3 ตาราง และทุกห้องเห็นเลข 10
        d3 = ct.post(reverse('core:api_bingo_draw', args=[room.id]), {
            'mode': BingoDrawnNumber.MODE_MANUAL, 'number': 10, 'target_room_code': 'all'
        }).json()
        self.assertTrue(d3['ok'])
        self.assertEqual(d3['hit_stats']['hit_count'], 2)
        self.assertEqual(d3['hit_stats']['eligible_cards_count'], 3)

        eff_r1, _ = get_effective_drawn_list_and_set(room, '01')
        eff_r2, _ = get_effective_drawn_list_and_set(room, '02')
        self.assertEqual(eff_r1, [1, 10])
        self.assertEqual(eff_r2, [1, 10])

        # 4. ล้างเฉพาะเลขของห้อง 01 -> เลข 1 ของห้อง 01 หายไป แต่เลข 1 ของห้อง 02 และเลข 10 ทั้งระดับยังอยู่
        ct.post(reverse('core:teacher_bingo_control', args=[room.id]), {
            'action': 'reset_drawn', 'reset_scope': '01', 'room_filter': '01'
        })
        eff_r1_after, _ = get_effective_drawn_list_and_set(room, '01')
        eff_r2_after, _ = get_effective_drawn_list_and_set(room, '02')
        self.assertEqual(eff_r1_after, [10])
        self.assertEqual(eff_r2_after, [1, 10])

    def test_no_module_word_and_thai_sorting_names(self):
        # 1. ต้องไม่มีคำว่า "โมดูล" ในเทมเพลตใดๆ เลย
        tpl_root = Path(settings.BASE_DIR) / 'templates'
        for p in tpl_root.rglob('*.html'):
            content = p.read_text(encoding='utf-8')
            self.assertNotIn('โมดูล', content, f'Found โมดูล in template: {p}')

        # 2. ชื่อเรียงลำดับต้องมีภาษาไทยทั้งหน้า Sorting ของนักเรียนและผู้ดูแล
        st99 = User.objects.get(student_id='99999')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        cs = Client()
        cs.force_login(st99)
        ct = Client()
        ct.force_login(teacher)

        for resp in [
            cs.get(reverse('core:student_sorting')),
            ct.get(reverse('core:teacher_sorting_manage')),
        ]:
            self.assertEqual(resp.status_code, 200)
            body = resp.content.decode('utf-8')
            self.assertNotIn('โมดูล', body)
            self.assertIn('ฟองสบู่', body)
            self.assertIn('แบบเลือก', body)
            self.assertIn('แบบแทรก', body)

    def test_seating_designer_seat_numbers_optional_and_live_login(self):
        room01 = Classroom.objects.get(code='01')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # ออกแบบแผนผังใหม่: ระบุ 1_1 ให้เลขที่ 30 (นายสมชาย 99999), ส่วน 1_2 ไม่ระบุเลขที่ (เว้นว่าง), ปิด 1_3
        resp = ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_seat_design_bulk',
            'classroom_id': room01.id,
            'seat_num_1_1': '30',
            'seat_lbl_1_1': 'PC-SPECIAL',
            'seat_num_1_2': '',
            'seat_lbl_1_2': 'เครื่องสำรอง',
            'seat_dis_1_3': '1',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        room01.refresh_from_db()
        s_nums = room01.get_seat_numbers_dict()
        self.assertEqual(s_nums.get('1_1'), 30)
        self.assertNotIn('1_2', s_nums)
        self.assertIn('1_3', room01.get_disabled_seats_set())

        # นักเรียน 99999 ล็อกอินเข้ามา -> ผู้ดูแลต้องเห็นในแผนผังและรายชื่อ Login สดทันที
        cs = Client()
        login_resp = cs.post(reverse('core:login'), {
            'login_mode': 'staff', 'username': '99999', 'password': '1234'
        }, follow=True)
        self.assertEqual(login_resp.status_code, 200)

        snap = ct.get(reverse('core:api_classroom_seating_live', args=[room01.id])).json()
        self.assertTrue(snap['ok'])
        self.assertGreaterEqual(snap['summary']['online_count'], 1)
        seat_1_1 = snap['seats']['1_1']
        self.assertIsNotNone(seat_1_1['student'])
        self.assertEqual(seat_1_1['student']['student_id'], '99999')
        self.assertTrue(seat_1_1['student']['is_online'])
        self.assertIsNone(snap['seats']['1_2']['assigned_number'])

    def test_seat_click_book_notebook_check_awards_and_toggles_points(self):
        room01 = Classroom.objects.get(code='01')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st99 = User.objects.get(student_id='99999')
        start_score = float(st99.total_score or 0.0)

        ct = Client()
        ct.force_login(teacher)

        # กำหนดให้ 1_1 เป็นเลขที่ 30 (99999)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_seat_design_bulk',
            'classroom_id': room01.id,
            'seat_num_1_1': '30',
        })

        # 1. คลิกเช็ค "หนังสือ" ที่ตำแหน่ง 1_1 -> +1 คะแนน
        r1 = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 1, 'seat_col': 1, 'check_type': 'toggle_book'
        }).json()
        self.assertTrue(r1['ok'])
        self.assertTrue(r1['has_book'])
        self.assertFalse(r1['has_notebook'])
        st99.refresh_from_db()
        self.assertEqual(st99.total_score, start_score + 1.0)

        # 2. คลิกเช็ค "สมุด" เพิ่ม -> +1 คะแนน (รวม +2)
        r2 = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 1, 'seat_col': 1, 'check_type': 'toggle_notebook'
        }).json()
        self.assertTrue(r2['ok'])
        self.assertTrue(r2['has_book'])
        self.assertTrue(r2['has_notebook'])
        st99.refresh_from_db()
        self.assertEqual(st99.total_score, start_score + 2.0)

        # 3. คลิกยกเลิก "หนังสือ" -> คะแนนหนังสือลดกลับลงมา +1.0
        r3 = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 1, 'seat_col': 1, 'check_type': 'toggle_book'
        }).json()
        self.assertTrue(r3['ok'])
        self.assertFalse(r3['has_book'])
        self.assertTrue(r3['has_notebook'])
        st99.refresh_from_db()
        self.assertEqual(st99.total_score, start_score + 1.0)
        self.assertTrue(StudentMaterialCheck.objects.filter(classroom=room01, student=st99, has_notebook=True, has_book=False).exists())

    def test_score_rule_full_crud_including_auto_rules(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # 1. สร้างกฎคะแนนใหม่
        resp_c = ct.post(reverse('core:teacher_scores'), {
            'action': 'create_rule',
            'name': 'ช่วยงานพิเศษครู',
            'rule_type': ScoreRule.TYPE_PLUS,
            'points': '3.5',
            'note': 'กฎทดสอบ',
        }, follow=True)
        self.assertEqual(resp_c.status_code, 200)
        new_rule = ScoreRule.objects.get(name='ช่วยงานพิเศษครู')
        self.assertEqual(new_rule.points, 3.5)

        # 2. แก้ไขกฎคะแนนเดิม (รวมกฎอัตโนมัติจากระบบเกมเช่น bingo_winner)
        bingo_rule = ScoreRule.objects.get(code='bingo_winner')
        ct.post(reverse('core:teacher_scores'), {
            'action': 'update_rule',
            'rule_id': bingo_rule.id,
            'name': 'ชนะเกม Bingo (ปรับแล้ว)',
            'rule_type': ScoreRule.TYPE_PLUS,
            'points': '4.0',
            'note': 'แก้จากหน้าเว็บ',
            'is_auto': '1',
            'is_active': '1',
        })
        bingo_rule.refresh_from_db()
        self.assertEqual(bingo_rule.name, 'ชนะเกม Bingo (ปรับแล้ว)')
        self.assertEqual(bingo_rule.points, 4.0)

        # 3. ลบกฎคะแนน
        ct.post(reverse('core:teacher_scores'), {
            'action': 'delete_rule',
            'rule_id': new_rule.id,
        })
        self.assertFalse(ScoreRule.objects.filter(pk=new_rule.id).exists())

    def test_score_periods_plus_minus_finalize_lock_and_multi_activity_move(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st99 = User.objects.get(student_id='99999')
        ct = Client()
        ct.force_login(teacher)

        p1 = ScorePeriod.objects.get(order_no=1)
        p2 = ScorePeriod.objects.get(order_no=2)
        act1 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 1:')
        act2 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 2:')

        # 1. ช่วงที่ 1 มี >1 กิจกรรม และย้าย act2 ไป-มาระหว่างช่วงที่ 1 กับช่วงที่ 2 ได้
        self.assertEqual(act1.score_period_id, p1.id)
        self.assertEqual(act2.score_period_id, p1.id)
        ct.post(reverse('core:teacher_scores'), {
            'action': 'assign_period_activities',
            'target_period_id': p2.id,
            'activity_ids': [act2.id],
        })
        act2.refresh_from_db()
        self.assertEqual(act2.score_period_id, p2.id)

        # ย้าย act2 กลับมาช่วงที่ 1
        ct.post(reverse('core:teacher_activities'), {
            'action': 'update_activity_period',
            'target_activity_id': act2.id,
            'score_period_id': p1.id,
        })
        act2.refresh_from_db()
        self.assertEqual(act2.score_period_id, p1.id)

        # 2. บวก/ลบคะแนนในช่วงที่ 1
        ct.post(reverse('core:teacher_scores'), {
            'action': 'award_student', 'student_id': st99.id, 'target_period_id': p1.id,
            'custom_points': '2', 'custom_type': 'plus', 'custom_reason': 'บวกช่วง 1'
        })
        ct.post(reverse('core:teacher_scores'), {
            'action': 'award_student', 'student_id': st99.id, 'target_period_id': p1.id,
            'custom_points': '1', 'custom_type': 'minus', 'custom_reason': 'ลบช่วง 1'
        })

        # 3. กดสรุปคะแนนช่วงที่ 1 -> ล็อกทันที ห้ามบวก/ลบในส่วนนี้ได้อีก!
        ct.post(reverse('core:teacher_scores'), {
            'action': 'finalize_period', 'target_period_id': p1.id,
        })
        p1.refresh_from_db()
        self.assertTrue(p1.is_finalized)

        logs_before = ScoreLog.objects.filter(student=st99, score_period=p1).count()
        resp_blocked = ct.post(reverse('core:teacher_scores'), {
            'action': 'award_student', 'student_id': st99.id, 'target_period_id': p1.id,
            'custom_points': '5', 'custom_type': 'plus', 'custom_reason': 'ห้ามเข้าช่วง 1'
        }, follow=True)
        self.assertContains(resp_blocked, 'สรุปคะแนนและล็อกไว้แล้ว')
        self.assertEqual(ScoreLog.objects.filter(student=st99, score_period=p1).count(), logs_before)

        with self.assertRaises(ValueError):
            ScoreLog.objects.create(student=st99, score_period=p1, points=1.0, reason='blocked')

        # 4. ต้องไปบวก/ลบในช่วงถัดไป (p2)
        ct.post(reverse('core:teacher_scores'), {
            'action': 'award_student', 'student_id': st99.id, 'target_period_id': p2.id,
            'custom_points': '3', 'custom_type': 'plus', 'custom_reason': 'บวกช่วง 2'
        })
        self.assertTrue(ScoreLog.objects.filter(student=st99, score_period=p2, points=3.0).exists())

        # การให้คะแนนอัตโนมัติ (ไม่ระบุ period) ต้องเข้าช่วงที่ 2 อัตโนมัติด้วย
        log_auto = award_points_by_rule(st99, 'active_answer', 'ตอบคำถาม', 1.0, 'manual')
        self.assertEqual(log_auto.score_period_id, p2.id)

    def test_presentation_randomizer_counts_same_day_no_repeat_inspect_remove_grade(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        act1 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 1:')
        act2 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 2:')

        # 1. ตรวจสอบจำนวนผู้ส่งตัวอย่างของห้อง 4/1: กิจกรรมที่ 1 = 28/30, กิจกรรมที่ 2 = 30/30 (มีรูปผลไม้จริง)
        r01_total = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').count()
        self.assertEqual(r01_total, 30)
        self.assertEqual(ActivitySubmission.objects.filter(activity=act1, student__room_code='01').count(), 28)
        self.assertEqual(ActivitySubmission.objects.filter(activity=act2, student__room_code='01').count(), 30)
        sample_img_sub = ActivitySubmission.objects.filter(activity=act2, student__room_code='01').first()
        self.assertTrue(bool(sample_img_sub.work_file))

        # หน้าเว็บแสดงจำนวนผู้ส่งแล้ว 28 / 30 คน บนเมนูนำเสนอใบงาน
        page1 = ct.get(reverse('core:teacher_presentations') + f'?room=01&activity_id={act1.id}')
        self.assertEqual(page1.status_code, 200)
        self.assertContains(page1, '28 / 30 คน')

        # 2. สุ่ม 3 คนแรกเข้า List ตรวจสอบก่อนให้คะแนน
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act1.id,
            'draw_count': 3,
            'draw_mode': 'continue',
        })
        draws_1 = list(ActivityPresentationDraw.objects.filter(activity=act1, is_removed=False))
        self.assertEqual(len(draws_1), 3)
        self.assertEqual(len({d.student_id for d in draws_1}), 3)
        self.assertTrue(all(not d.is_graded for d in draws_1))  # ยังไม่ให้คะแนนทันที จนกว่าครูจะตรวจสอบและกดให้คะแนน

        # 3. นำรายชื่อ 1 คนออกจาก List ตรวจสอบ
        removed_target = draws_1[0]
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'remove_draw',
            'room': '01',
            'activity_id': act1.id,
            'draw_id': removed_target.id,
            'removed_reason': 'ไม่อยู่ในห้อง',
        })
        removed_target.refresh_from_db()
        self.assertTrue(removed_target.is_removed)

        # 4. สุ่มต่อในกิจกรรมที่ 2 ในรอบเดียวกัน -> ต้องไม่ซ้ำกับทั้ง 3 คนที่สุ่มไปแล้ว!
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act2.id,
            'draw_count': 3,
            'draw_mode': 'continue',
        })
        draws_2 = list(ActivityPresentationDraw.objects.filter(activity=act2, is_removed=False))
        self.assertEqual(len(draws_2), 3)
        ids_1 = {d.student_id for d in draws_1}
        ids_2 = {d.student_id for d in draws_2}
        self.assertTrue(ids_1.isdisjoint(ids_2))

        # 5. กดให้คะแนนคนที่ตรวจสอบแล้วใน List
        graded_target = draws_1[1]
        old_score = float(graded_target.student.total_score or 0.0)
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'grade_draw',
            'room': '01',
            'activity_id': act1.id,
            'draw_id': graded_target.id,
            'score_awarded': '5',
            'feedback': 'ตอบดีมาก',
        })
        graded_target.refresh_from_db()
        graded_target.student.refresh_from_db()
        self.assertTrue(graded_target.is_graded)
        self.assertEqual(graded_target.score_awarded, 5.0)
        self.assertEqual(graded_target.student.total_score, old_score + 5.0)

    def test_build_auto_seat_numbers_vertical_variants_and_even_split(self):
        # 1. Default vertical front->back skipping disabled seat
        res_v = build_auto_seat_numbers(
            4, 3, [1, 2, 3, 4, 5],
            disabled_seats={'2_1'},
            direction='col_front_to_back',
        )
        self.assertEqual(res_v, {'1_1': 1, '3_1': 2, '4_1': 3, '1_2': 4, '2_2': 5})

        # 2. Back->front vertical
        res_b2f = build_auto_seat_numbers(3, 2, [1, 2, 3, 4], direction='col_back_to_front')
        self.assertEqual(res_b2f, {'3_1': 1, '2_1': 2, '1_1': 3, '3_2': 4})

        # 3. RTL front->back vertical
        res_rtl = build_auto_seat_numbers(3, 2, [1, 2, 3, 4], direction='col_front_to_back_rtl')
        self.assertEqual(res_rtl, {'1_2': 1, '2_2': 2, '3_2': 3, '1_1': 4})

        # 4. RTL back->front vertical
        res_rtl_b2f = build_auto_seat_numbers(3, 2, [1, 2, 3, 4], direction='col_back_to_front_rtl')
        self.assertEqual(res_rtl_b2f, {'3_2': 1, '2_2': 2, '1_2': 3, '3_1': 4})

        # 5. Snake vertical with per_col=2 cap (2nd col reverses within first 2 front seats)
        res_snake = build_auto_seat_numbers(4, 2, [1, 2, 3, 4], direction='col_snake', per_col=2)
        self.assertEqual(res_snake, {'1_1': 1, '2_1': 2, '2_2': 3, '1_2': 4})

        # 6. Horizontal fallback
        res_row = build_auto_seat_numbers(2, 3, [1, 2, 3, 4], direction='row_front_to_back')
        self.assertEqual(res_row, {'1_1': 1, '1_2': 2, '1_3': 3, '2_1': 4})

        # 7. Fixed per_col=5 on 10x6 room for 30 students -> 5 per column
        res_p5 = build_auto_seat_numbers(10, 6, list(range(1, 31)), direction='col_front_to_back', per_col=5)
        self.assertEqual(len(res_p5), 30)
        self.assertEqual(res_p5['1_1'], 1)
        self.assertEqual(res_p5['5_1'], 5)
        self.assertNotIn('6_1', res_p5)
        self.assertEqual(res_p5['1_2'], 6)
        self.assertEqual(res_p5['5_6'], 30)

        # 8. Even split with carryover when col 1 has fewer enabled seats
        res_even = build_auto_seat_numbers(
            4, 3, list(range(1, 10)),
            disabled_seats={'3_1', '4_1'},  # col 1 has only 2 enabled seats, cols 2 & 3 have 4 each
            direction='col_front_to_back',
            per_col='even',
        )
        self.assertEqual(len(res_even), 9)
        self.assertEqual(res_even['1_1'], 1)
        self.assertEqual(res_even['2_1'], 2)
        self.assertEqual(res_even['4_2'], 6)
        self.assertEqual(res_even['3_3'], 9)

    def test_teacher_vertical_auto_assign_copy_layout_and_compact_views(self):
        room01 = Classroom.objects.get(code='01')
        room02 = Classroom.objects.get(code='02')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # Make room01 a 10x6 classroom with no disabled seats first
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'update_classroom_layout',
            'classroom_id': room01.id,
            'seat_rows': '10',
            'seat_cols': '6',
            'board_position': 'bottom',
            'row_order': 'bottom_to_top',
            'disabled_seats_csv': '',
        })

        # Default vertical auto_assign_seat_numbers (1_1=1, 2_1=2, ..., 10_1=10, 1_2=11)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'auto_assign_seat_numbers',
            'classroom_id': room01.id,
        })
        room01.refresh_from_db()
        nums = room01.get_seat_numbers_dict()
        self.assertEqual(nums.get('1_1'), 1)
        self.assertEqual(nums.get('2_1'), 2)
        self.assertEqual(nums.get('10_1'), 10)
        self.assertEqual(nums.get('1_2'), 11)

        # Custom direction & per_col=5 + disabled seat + custom 2-digit numbers & machine labels via bulk save
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_seat_design_bulk',
            'classroom_id': room01.id,
            'seat_num_1_1': '01',
            'seat_lbl_1_1': '01',
            'seat_num_2_1': '09',
            'seat_lbl_2_1': '02',
            'seat_num_3_1': '30',
            'seat_dis_1_3': '1',
        })
        room01.refresh_from_db()
        self.assertEqual(room01.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room01.get_seat_numbers_dict().get('2_1'), 9)
        self.assertEqual(room01.get_seat_numbers_dict().get('3_1'), 30)
        self.assertEqual(room01.get_custom_labels_dict().get('1_1'), '01')
        self.assertEqual(room01.get_custom_labels_dict().get('2_1'), '02')

        # api_classroom_seating_live returns custom_label
        snap_live = ct.get(reverse('core:api_classroom_seating_live', args=[room01.id])).json()
        self.assertEqual(snap_live['seats']['1_1']['custom_label'], '01')
        self.assertEqual(snap_live['seats']['2_1']['custom_label'], '02')

        ct.post(reverse('core:teacher_attendance'), {
            'action': 'auto_assign_seat_numbers',
            'classroom_id': room01.id,
            'direction': 'col_front_to_back',
            'per_col': '5',
        })
        room01.refresh_from_db()
        nums_p5 = room01.get_seat_numbers_dict()
        self.assertEqual(nums_p5.get('1_1'), 1)
        self.assertEqual(nums_p5.get('5_1'), 5)
        self.assertIsNone(nums_p5.get('6_1'))
        self.assertNotIn('1_3', nums_p5)
        self.assertEqual(nums_p5.get('2_3'), 11)

        # api_classroom_seat_check returns seats + online_students for 1-round-trip update
        chk_resp = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 1, 'seat_col': 1, 'check_type': 'toggle_book'
        }).json()
        self.assertTrue(chk_resp['ok'])
        self.assertIn('seats', chk_resp)
        self.assertIn('online_students', chk_resp)
        self.assertTrue(chk_resp['seats']['1_1']['student']['has_book'])

        # check_type='toggle_both' toggles book + notebook together
        both_on_resp = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 2, 'seat_col': 1, 'check_type': 'toggle_both'
        }).json()
        self.assertTrue(both_on_resp['ok'])
        self.assertTrue(both_on_resp['seats']['2_1']['student']['has_book'])
        self.assertTrue(both_on_resp['seats']['2_1']['student']['has_notebook'])

        both_off_resp = ct.post(reverse('core:api_classroom_seat_check', args=[room01.id]), {
            'seat_row': 2, 'seat_col': 1, 'check_type': 'toggle_both'
        }).json()
        self.assertTrue(both_off_resp['ok'])
        self.assertFalse(both_off_resp['seats']['2_1']['student']['has_book'])
        self.assertFalse(both_off_resp['seats']['2_1']['student']['has_notebook'])

        # GET teacher_attendance (live, design, lab) on 10x6 room renders compact grid markup & single big ตรวจสอบ button
        live_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}')
        self.assertEqual(live_page.status_code, 200)
        self.assertContains(live_page, 'liveGridMatrix')
        self.assertContains(live_page, 'seat-chk-btn')
        self.assertContains(live_page, 'ตรวจสอบ')
        self.assertContains(live_page, "'toggle_both'")
        self.assertNotContains(live_page, 'is-book-on')
        self.assertNotContains(live_page, 'is-nb-on')
        self.assertNotContains(live_page, '>นส')
        self.assertContains(live_page, '>C1<')
        self.assertContains(live_page, 'id="liveSeatCell_1_1"')
        self.assertContains(live_page, 'id="liveSeatCell_10_6"')
        self.assertNotContains(live_page, 'id="liveSeatCell_"')
        self.assertNotContains(live_page, 'quickCopySrcSelect')

        design_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=design')
        self.assertEqual(design_page.status_code, 200)
        self.assertContains(design_page, 'designGridMatrix')
        self.assertContains(design_page, 'C1')
        self.assertNotContains(design_page, 'แบ่งเท่ากัน')
        self.assertNotContains(design_page, 'quickCopySrcSelect')
        self.assertNotContains(design_page, 'fillEndInput')
        for marker in [
            'fillStartInput', 'fillSkipInput',
            'fillDirSelect', 'runAutoFill()',
            'id="designNum_1_1"', 'name="seat_num_1_1"',
            'id="designNum_10_6"',
        ]:
            self.assertContains(design_page, marker)
        self.assertNotContains(design_page, 'id="designNum_"')
        self.assertNotContains(design_page, 'name="seat_num_"')

        lab_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=lab')
        self.assertEqual(lab_page.status_code, 200)
        self.assertNotContains(lab_page, 'quickCopySrcSelect')
        self.assertNotContains(lab_page, 'labFillEndInput')
        for marker in [
            'id="labLbl_1_1"', 'name="seat_lbl_1_1"',
            'id="labDis_1_1"', 'id="labLbl_10_6"',
        ]:
            self.assertContains(lab_page, marker)
        self.assertNotContains(lab_page, 'id="labLbl_"')

        # GET student_attendance renders compact CSS Grid with C1 header
        AttendanceSession.objects.create(
            classroom=room01, title='คาบ 10x6', check_in_code='GRID10', is_open=True, created_by=teacher
        )
        st01 = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').first()
        cs = Client()
        cs.force_login(st01)
        st_page = cs.get(reverse('core:student_attendance'))
        self.assertEqual(st_page.status_code, 200)
        self.assertContains(st_page, 'student-seat-grid')
        self.assertContains(st_page, 'C1')
        self.assertContains(st_page, 'id="studentSeatCell_1_1"')
        self.assertNotContains(st_page, 'id="studentSeatCell_"')

    def test_lab_room_layouts_134_135_136_default_by_machine_and_per_class_override(self):
        room01 = Classroom.objects.get(code='01')
        room02 = Classroom.objects.get(code='02')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        # Default lab rooms 134, 135, 136 exist
        for name in ('ห้อง 134', 'ห้อง 135', 'ห้อง 136'):
            self.assertTrue(LabRoomLayout.objects.filter(name=name).exists(), f'Missing default lab room {name}')

        lab_tab = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=lab')
        self.assertEqual(lab_tab.status_code, 200)
        self.assertContains(lab_tab, '3. แผนผังห้อง')
        self.assertNotContains(lab_tab, '3. แผนผังห้อง (134/135/136)')
        self.assertContains(lab_tab, 'id="labSelectorSelect"')
        self.assertContains(lab_tab, '+ เพิ่มห้อง')
        self.assertContains(lab_tab, 'data-bs-target="#createLabRoomModal"')
        self.assertContains(lab_tab, '<input type="hidden" name="lab_name"')
        self.assertNotContains(lab_tab, 'oninput="markLabDirty(true)"')
        self.assertContains(lab_tab, 'id="labToggleBtn_1_1"')
        self.assertContains(lab_tab, 'toggleLabSeatDisabled')
        self.assertContains(lab_tab, 'mapClassroomsLabModal')
        self.assertContains(lab_tab, 'bulk_assign_classroom_lab_rooms')
        self.assertNotContains(lab_tab, 'fillEndInput')
        self.assertNotContains(lab_tab, 'labFillEndInput')
        self.assertNotContains(lab_tab, 'toggleLabAisleMode')
        for name in ('ห้อง 134', 'ห้อง 135', 'ห้อง 136'):
            self.assertContains(lab_tab, name)

        # Create ห้อง 137 (10x6)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'create_lab_room',
            'classroom_id': room01.id,
            'lab_name': 'ห้อง 137',
            'seat_rows': '10',
            'seat_cols': '6',
            'board_position': 'bottom',
            'row_order': 'bottom_to_top',
        })
        lab137 = LabRoomLayout.objects.get(name='ห้อง 137')
        self.assertEqual(lab137.seat_rows, 10)
        self.assertEqual(lab137.seat_cols, 6)

        # Edit machine numbers & remove machine 1_3 (preserve label '13' via readonly input, no re-numbering)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_lab_room_design',
            'classroom_id': room01.id,
            'lab_room_id': lab137.id,
            'lab_name': 'ห้อง 137',
            'seat_rows': '10',
            'seat_cols': '6',
            'board_position': 'bottom',
            'row_order': 'bottom_to_top',
            'seat_lbl_1_1': '01',
            'seat_lbl_2_1': '15',
            'seat_lbl_1_3': '13',
            'seat_dis_1_3': '1',
        })
        lab137.refresh_from_db()
        self.assertIn('1_3', lab137.get_disabled_seats_set())
        self.assertEqual(lab137.get_machine_labels_dict().get('1_1'), '01')
        self.assertEqual(lab137.get_machine_labels_dict().get('2_1'), '15')
        self.assertEqual(lab137.get_machine_labels_dict().get('1_3'), '13')

        # Assign 4/1 and 4/2 to ห้อง 137 -> both inherit 10x6, disabled 1_3, and default seating by machine number
        for rm in (room01, room02):
            ct.post(reverse('core:teacher_attendance'), {
                'action': 'assign_classroom_lab_room',
                'classroom_id': rm.id,
                'lab_room_id': lab137.id,
            })
        room01.refresh_from_db()
        room02.refresh_from_db()
        self.assertEqual(room01.lab_room_id, lab137.id)
        self.assertEqual(room02.lab_room_id, lab137.id)
        self.assertEqual(room01.get_effective_rows(), 10)
        self.assertEqual(room01.get_effective_cols(), 6)
        self.assertIn('1_3', room01.get_disabled_seats_set())
        self.assertEqual(room02.get_effective_rows(), 10)
        self.assertIn('1_3', room02.get_disabled_seats_set())
        self.assertEqual(room01.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room01.get_seat_numbers_dict().get('2_1'), 15)
        self.assertNotIn('1_3', room01.get_seat_numbers_dict())
        self.assertEqual(room02.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room02.get_seat_numbers_dict().get('2_1'), 15)
        self.assertNotIn('1_3', room02.get_seat_numbers_dict())

        # Tab 2 renders unassigned students interactive bar & easy direct-overwrite input hooks while 1_3 is disabled
        design_tab = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=design')
        self.assertEqual(design_tab.status_code, 200)
        for marker in (
            'id="classroomSelectorSelect"',
            'classroom-select-status is-seat-incomplete',
            'color:#dc2626',
            'this.value.length>0&&this.selectionStart===0',
            'id="designUnassignedBar"',
            'id="designUnassignedChips"',
            'autoPlaceMissingStudents()',
            'onStudentNumFocus(this,',
            'onDesignInputClick(this,',
        ):
            self.assertContains(design_tab, marker)

        # Save unassigned student #13 into active empty seat 3_1 while 1_3 remains disabled
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_seat_design_bulk',
            'classroom_id': room01.id,
            'seat_num_1_1': '01',
            'seat_num_2_1': '15',
            'seat_num_3_1': '13',
        })
        room01.refresh_from_db()
        self.assertEqual(room01.get_seat_numbers_dict().get('3_1'), 13)
        self.assertNotIn('1_3', room01.get_seat_numbers_dict())

        # Reset 4/1 back to lab default before testing restore of machine 1_3
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'reset_class_seats_to_lab_default',
            'classroom_id': room01.id,
        })
        room01.refresh_from_db()

        # Restore machine 1_3 (seat_dis_1_3='0') -> returns to seat_numbers with original label '13' without re-numbering others
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_lab_room_design',
            'classroom_id': room01.id,
            'lab_room_id': lab137.id,
            'lab_name': 'ห้อง 137',
            'seat_rows': '10',
            'seat_cols': '6',
            'board_position': 'bottom',
            'row_order': 'bottom_to_top',
            'seat_lbl_1_1': '01',
            'seat_lbl_2_1': '15',
            'seat_lbl_1_3': '13',
            'seat_dis_1_3': '0',
        })
        lab137.refresh_from_db()
        room01.refresh_from_db()
        room02.refresh_from_db()
        self.assertNotIn('1_3', lab137.get_disabled_seats_set())
        self.assertEqual(room01.get_seat_numbers_dict().get('1_3'), 13)
        self.assertEqual(room01.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room01.get_seat_numbers_dict().get('2_1'), 15)

        # Override ONLY 4/1 student seating via save_seat_design_bulk (sending only seat_num_*)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'save_seat_design_bulk',
            'classroom_id': room01.id,
            'seat_num_1_1': '05',
            'seat_num_2_1': '08',
        })
        room01.refresh_from_db()
        room02.refresh_from_db()
        lab137.refresh_from_db()
        self.assertTrue(room01.has_custom_seat_override())
        self.assertEqual(room01.get_seat_numbers_dict().get('1_1'), 5)
        self.assertEqual(room01.get_seat_numbers_dict().get('2_1'), 8)
        # Machine labels on ห้อง 137 and seating for 4/2 remain untouched
        self.assertEqual(lab137.get_machine_labels_dict().get('1_1'), '01')
        self.assertEqual(lab137.get_machine_labels_dict().get('2_1'), '15')
        self.assertEqual(room02.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room02.get_seat_numbers_dict().get('2_1'), 15)

        # Reset 4/1 back to sitting by ห้อง 137 machine numbers
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'reset_class_seats_to_lab_default',
            'classroom_id': room01.id,
        })
        room01.refresh_from_db()
        self.assertFalse(room01.has_custom_seat_override())
        self.assertEqual(room01.get_seat_numbers_dict().get('1_1'), 1)
        self.assertEqual(room01.get_seat_numbers_dict().get('2_1'), 15)

        # Bulk map multiple classrooms to different lab rooms in one POST
        lab135 = LabRoomLayout.objects.get(name='ห้อง 135')
        lab136 = LabRoomLayout.objects.get(name='ห้อง 136')
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'bulk_assign_classroom_lab_rooms',
            'classroom_id': room01.id,
            f'class_lab_{room01.id}': str(lab135.id),
            f'class_lab_{room02.id}': str(lab136.id),
        })
        room01.refresh_from_db()
        room02.refresh_from_db()
        self.assertEqual(room01.lab_room_id, lab135.id)
        self.assertEqual(room02.lab_room_id, lab136.id)

        complete_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=design')
        self.assertEqual(complete_page.status_code, 200)
        self.assertContains(complete_page, 'classroom-select-status is-seat-complete')
        self.assertContains(complete_page, 'color:#0f172a')
        self.assertNotContains(complete_page, 'classroom-select-status is-seat-incomplete')

    def test_live_check_absent_list_last_x_summary_and_cancel_score(self):
        room01 = Classroom.objects.get(code='01')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        students = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:4])
        self.assertGreaterEqual(len(students), 4)
        st1, st2, st3, st4 = students[0], students[1], students[2], students[3]

        sess = AttendanceSession.objects.create(
            classroom=room01, title='ทดสอบเช็คชื่อ-สรุปสาย', check_in_code='LATE01', is_open=True, created_by=teacher
        )
        now = timezone.now()
        r1 = AttendanceRecord.objects.create(session=sess, student=st1, seat_row=1, seat_col=1, status=AttendanceRecord.STATUS_PRESENT)
        r2 = AttendanceRecord.objects.create(session=sess, student=st2, seat_row=2, seat_col=1, status=AttendanceRecord.STATUS_PRESENT)
        r3 = AttendanceRecord.objects.create(session=sess, student=st3, seat_row=3, seat_col=1, status=AttendanceRecord.STATUS_PRESENT)
        AttendanceRecord.objects.filter(pk=r1.pk).update(checked_at=now - timedelta(minutes=10))
        AttendanceRecord.objects.filter(pk=r2.pk).update(checked_at=now - timedelta(minutes=5))
        AttendanceRecord.objects.filter(pk=r3.pk).update(checked_at=now - timedelta(minutes=1))

        ct = Client()
        ct.force_login(teacher)

        live_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&session_id={sess.id}')
        self.assertEqual(live_page.status_code, 200)
        for marker in (
            'id="liveSidePanel"',
            'id="liveAbsentList"',
            'id="lastXCountInput"',
            'id="btnSummarizeLastX"',
            'id="btnSaveLastXScore"',
            'id="recentScoreBatchesList"',
        ):
            self.assertContains(live_page, marker)

        live_snap = ct.get(reverse('core:api_classroom_seating_live', args=[room01.id]) + f'?session_id={sess.id}').json()
        self.assertTrue(live_snap['ok'])
        reg_ids = [s['id'] for s in live_snap['registered_students']]
        abs_ids = {s['id'] for s in live_snap['absent_students']}
        self.assertEqual(reg_ids, [st1.id, st2.id, st3.id])
        self.assertNotIn(st1.id, abs_ids)
        self.assertNotIn(st2.id, abs_ids)
        self.assertNotIn(st3.id, abs_ids)
        self.assertIn(st4.id, abs_ids)

        st2_before = float(st2.total_score or 0.0)
        st3_before = float(st3.total_score or 0.0)

        # Award -1 score to last 2 registered students (st2, st3)
        award_resp = ct.post(reverse('core:api_classroom_attendance_batch_score', args=[room01.id]), {
            'action': 'award',
            'session_id': sess.id,
            'student_ids': [str(st2.id), str(st3.id)],
            'points': '-1',
            'reason': 'มาสาย 2 คนสุดท้าย',
            'apply_to_group': '0',
        }).json()
        self.assertTrue(award_resp['ok'])
        self.assertEqual(award_resp['affected_count'], 2)
        batch_id = award_resp['batch_id']
        st2.refresh_from_db()
        st3.refresh_from_db()
        self.assertAlmostEqual(float(st2.total_score), st2_before - 1.0, places=2)
        self.assertAlmostEqual(float(st3.total_score), st3_before - 1.0, places=2)
        batch_ids = [b['batch_id'] for b in award_resp['recent_score_batches']]
        self.assertIn(batch_id, batch_ids)

        # Cancel that batch -> scores revert and batch disappears from recent_score_batches
        cancel_resp = ct.post(reverse('core:api_classroom_attendance_batch_score', args=[room01.id]), {
            'action': 'cancel',
            'session_id': sess.id,
            'batch_id': batch_id,
        }).json()
        self.assertTrue(cancel_resp['ok'])
        self.assertEqual(cancel_resp['cancelled_count'], 2)
        st2.refresh_from_db()
        st3.refresh_from_db()
        self.assertAlmostEqual(float(st2.total_score), st2_before, places=2)
        self.assertAlmostEqual(float(st3.total_score), st3_before, places=2)
        remaining_ids = [b['batch_id'] for b in cancel_resp['recent_score_batches']]
        self.assertNotIn(batch_id, remaining_ids)

    def test_copy_layout_to_multiple_rooms_and_copy_lab_room(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        room02 = Classroom.objects.get(code='02')
        room03 = Classroom.objects.get(code='03')
        ct = Client()
        ct.force_login(teacher)

        # ตั้งห้อง 01 เป็นแบบกำหนดเองขนาด 4x5 พร้อมเลขที่ และทดสอบคัดลอกไปยังหลายห้องพร้อมกัน
        room01.lab_room = None
        room01.seat_rows = 4
        room01.seat_cols = 5
        room01.board_position = Classroom.BOARD_TOP
        room01.layout_config = '{"row_order": "top_to_bottom", "disabled_seats": ["1_3"], "custom_labels": {"1_1": "PC01"}, "seat_numbers": {"1_1": 1, "1_2": 2}, "auto_direction": "col_front_to_back"}'
        room01.save()

        resp_multi = ct.post(reverse('core:teacher_attendance'), {
            'action': 'copy_layout_to_rooms',
            'classroom_id': room01.id,
            'target_classroom_ids': [str(room02.id), str(room03.id)],
            'copy_numbers_mode': 'auto_vertical',
            'tab': 'design',
        })
        self.assertEqual(resp_multi.status_code, 302)
        room02.refresh_from_db()
        room03.refresh_from_db()
        self.assertEqual((room02.seat_rows, room02.seat_cols, room02.board_position), (4, 5, Classroom.BOARD_TOP))
        self.assertEqual((room03.seat_rows, room03.seat_cols, room03.board_position), (4, 5, Classroom.BOARD_TOP))
        self.assertIn('1_3', room02.get_disabled_seats_set())
        self.assertNotIn('1_3', room03.get_seat_numbers_dict())

        # ทดสอบคัดลอกแผนผัง Lab Room จากห้องแรกไปอีกห้อง
        lab1, lab2 = list(LabRoomLayout.objects.order_by('id')[:2])
        resp_lab = ct.post(reverse('core:teacher_attendance'), {
            'action': 'copy_lab_room_layout',
            'classroom_id': room01.id,
            'lab_room_id': lab2.id,
            'source_lab_room_id': lab1.id,
            'tab': 'lab',
        })
        self.assertEqual(resp_lab.status_code, 302)
        lab2.refresh_from_db()
        self.assertEqual((lab2.seat_rows, lab2.seat_cols, lab2.board_position), (lab1.seat_rows, lab1.seat_cols, lab1.board_position))
        self.assertEqual(lab2.get_machine_labels_dict(), lab1.get_machine_labels_dict())

        att_page = ct.get(reverse('core:teacher_attendance') + f'?classroom_id={room01.id}&tab=design')
        self.assertEqual(att_page.status_code, 200)
        for marker in ('copyClassroomLayoutModal', 'copyLabRoomModal', 'labFillPrefixInput', 'labAutoReflowChk'):
            self.assertContains(att_page, marker)

    def test_room01_sample_activities_prompts_and_student_showcase(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st99 = User.objects.get(student_id='99999')
        ct = Client()

        # Teacher hits seed_room01_samples & loads teacher activities page
        ct.force_login(teacher)
        seed_resp = ct.post(reverse('core:teacher_activities'), {
            'action': 'seed_room01_samples',
            'room': '01',
        })
        self.assertEqual(seed_resp.status_code, 302)

        t_act1 = LearningActivity.objects.filter(title__startswith='กิจกรรมที่ 1:').first()
        t_act2 = LearningActivity.objects.filter(title__startswith='กิจกรรมที่ 2:').first()
        self.assertIsNotNone(t_act1)
        self.assertIsNotNone(t_act2)
        self.assertEqual(t_act1.activity_type, LearningActivity.TYPE_QA)
        self.assertEqual(t_act2.activity_type, LearningActivity.TYPE_IMAGE)
        self.assertEqual(ActivitySubmission.objects.filter(activity=t_act1, student__room_code='01').count(), 28)
        self.assertEqual(ActivitySubmission.objects.filter(activity=t_act2, student__room_code='01').count(), 30)

        t_page = ct.get(reverse('core:teacher_presentations') + f'?room=01&activity_id={t_act2.id}')
        self.assertEqual(t_page.status_code, 200)
        self.assertContains(t_page, 'มีผู้ส่งผลงานแล้ว 30 / 30 คน')

        # Special student 99999 views student activities board filtered to fruit activity
        ct.force_login(st99)
        s_page = ct.get(reverse('core:student_activities') + f'?activity_id={t_act2.id}')
        self.assertEqual(s_page.status_code, 200)
        self.assertContains(s_page, '1 + 1')
        self.assertContains(s_page, '28/30')
        self.assertContains(s_page, '30/30')
        self.assertContains(s_page, 'studentImgModal')

        # Fruit PNGs generated properly (300x300)
        fruit_path = settings.MEDIA_ROOT / 'activities' / 'fruits' / 'watermelon.png'
        self.assertTrue(fruit_path.exists())
        with Image.open(fruit_path) as im:
            self.assertEqual(im.size, (300, 300))

    def test_sorting_room_filter_isolates_scores_reward_clear_and_csv(self):
        act = SortingActivity.objects.first()
        act.session_no = (act.session_no or 1) + 1
        act.is_exam_mode = True
        act.top_x_reward = 1
        act.save()

        st_r01 = User.objects.filter(role=User.ROLE_STUDENT, room_code='01').first()
        st_r02 = User.objects.filter(role=User.ROLE_STUDENT, room_code='02').first()
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()

        r01_before = float(st_r01.total_score or 0.0)
        r02_before = float(st_r02.total_score or 0.0)

        # ห้อง 02 ได้คะแนนสูงกว่าห้อง 01
        SortingScore.objects.create(
            activity=act, student=st_r01, session_no=act.session_no,
            score=50.0, cumulative_score=50.0, time_used=12.0, attempts=0, status='completed', is_exam_mode=True
        )
        SortingScore.objects.create(
            activity=act, student=st_r02, session_no=act.session_no,
            score=95.0, cumulative_score=95.0, time_used=5.0, attempts=0, status='completed', is_exam_mode=True
        )

        ct = Client()
        ct.force_login(teacher)

        # กรองเฉพาะห้อง 01 -> ไม่รวมห้อง 02 และไม่มี type="number"
        for url in (
            reverse('core:teacher_sorting_manage') + f'?activity_id={act.id}&room=01',
            reverse('core:teacher_sorting_summary', args=[act.id]) + '?room=01',
        ):
            pg = ct.get(url)
            self.assertEqual(pg.status_code, 200)
            html = pg.content.decode('utf-8')
            self.assertNotIn('type="number"', html)
            self.assertNotIn("type='number'", html)

        board_01 = ct.get(reverse('core:api_sorting_live_board', args=[act.id]) + '?room=01').json()
        self.assertEqual([p['student_id'] for p in board_01['leaderboard']], [st_r01.student_id])

        board_grade4 = ct.get(reverse('core:api_sorting_live_board', args=[act.id]) + '?room=grade:4').json()
        self.assertEqual(len(board_grade4['leaderboard']), 2)

        csv_01 = ct.get(reverse('core:teacher_sorting_export_csv', args=[act.id]) + '?room=01').content.decode('utf-8-sig')
        self.assertIn(st_r01.student_id, csv_01)
        self.assertNotIn(st_r02.student_id, csv_01)

        # กดให้คะแนน Top X โดยเลือก room=01 -> ห้อง 01 ต้องได้คะแนน, ห้อง 02 ต้องไม่ได้
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'reward_top_x',
            'activity_id': act.id,
            'room': '01',
            'top_x_reward': 1,
        })
        st_r01.refresh_from_db()
        st_r02.refresh_from_db()
        self.assertGreater(float(st_r01.total_score), r01_before)
        self.assertEqual(float(st_r02.total_score), r02_before)

        # ล้างรายการเฉพาะห้อง 01 -> ห้อง 02 ยังอยู่ครบ
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'clear_list',
            'activity_id': act.id,
            'room': '01',
        })
        self.assertEqual(SortingScore.objects.filter(activity=act, session_no=act.session_no, student__room_code='01').count(), 0)
        self.assertEqual(SortingScore.objects.filter(activity=act, session_no=act.session_no, student__room_code='02').count(), 1)

    def test_lotto_finalize_awards_only_non_removed_and_idempotent(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        sess = LottoSession.objects.create(
            title='ทดสอบสรุปคะแนน Lotto เฉพาะคนไม่ถูกลบ',
            classroom=room01,
            bonus_points=1.0,
            is_open=True,
            created_by=teacher,
        )
        ct = Client()
        ct.force_login(teacher)

        lotto_page = ct.get(reverse('core:teacher_lotto_manage') + f'?session_id={sess.id}')
        self.assertContains(lotto_page, 'สรุปเพื่อให้คะแนน')

        picks = [ct.post(reverse('core:api_lotto_draw', args=[sess.id])).json() for _ in range(3)]
        self.assertTrue(all(p['ok'] and p['is_awarded'] is False for p in picks))

        students = [User.objects.get(student_id=p['student_id']) for p in picks]
        scores_before = [float(s.total_score or 0.0) for s in students]

        # หลังสุ่มทันที คะแนนยังไม่บวก (รอสรุป)
        for s, old_sc in zip(students, scores_before):
            s.refresh_from_db()
            self.assertEqual(float(s.total_score or 0.0), old_sc)

        # ลบคนที่ 2 ออก
        rm_res = ct.post(reverse('core:api_lotto_remove_result', args=[sess.id]), {
            'result_id': picks[1]['result_id'],
            'reason': 'ไม่อยู่ในห้อง',
        }).json()
        self.assertTrue(rm_res['ok'])
        self.assertEqual(rm_res['pending_count'], 2)

        # กดสรุปเพื่อให้คะแนน -> เฉพาะคนที่ 1 และ 3 ได้คะแนน, คนที่ 2 ไม่ได้
        fin_res = ct.post(reverse('core:api_lotto_finalize', args=[sess.id])).json()
        self.assertTrue(fin_res['ok'])
        self.assertEqual(fin_res['awarded_count'], 2)

        for s in students:
            s.refresh_from_db()
        self.assertEqual(float(students[0].total_score), scores_before[0] + fin_res['points_each'])
        self.assertEqual(float(students[1].total_score or 0.0), scores_before[1])
        self.assertEqual(float(students[2].total_score), scores_before[2] + fin_res['points_each'])

        # กดสรุปซ้ำอีกครั้งต้องไม่บวกซ้ำ
        fin_again = ct.post(reverse('core:api_lotto_finalize', args=[sess.id])).json()
        self.assertEqual(fin_again['awarded_count'], 0)
        students[0].refresh_from_db()
        self.assertEqual(float(students[0].total_score), scores_before[0] + fin_res['points_each'])

    def test_presentations_menu_continue_vs_reset_custom_count_and_descending_bulk_grade(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        act1 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 1:')
        act2 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 2:')
        room01_students = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number'))

        ct = Client()
        ct.force_login(teacher)

        # 1. หน้าเมนู "นำเสนอใบงาน" พร้อม zoom controls
        pres_pg = ct.get(reverse('core:teacher_presentations') + f'?room=01&activity_id={act1.id}')
        self.assertEqual(pres_pg.status_code, 200)
        for marker in ('นำเสนอใบงาน', 'workZoomViewport', 'workZoomCanvas', 'grade_all_submitted'):
            self.assertContains(pres_pg, marker)

        # 2. กรอกเลขเกินจำนวนผู้ส่งจริง (99 > 28) ต้องปฏิเสธและไม่สร้าง draw
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act1.id,
            'draw_mode': 'continue',
            'draw_count': 99,
        })
        self.assertEqual(ActivityPresentationDraw.objects.filter(activity=act1).count(), 0)

        # 3. สุ่ม 4 คนในกิจกรรมที่ 1 -> แล้วเปลี่ยนเป็นกิจกรรมที่ 2 แบบ "สุ่มต่อจากเดิม" 5 คน -> ต้องไม่ซ้ำ
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act1.id,
            'draw_mode': 'continue',
            'draw_count': 4,
        })
        d1_ids = set(ActivityPresentationDraw.objects.filter(activity=act1, round_no=1).values_list('student_id', flat=True))
        self.assertEqual(len(d1_ids), 4)

        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act2.id,
            'draw_mode': 'continue',
            'draw_count': 5,
        })
        d2_ids = set(ActivityPresentationDraw.objects.filter(activity=act2, round_no=1).values_list('student_id', flat=True))
        self.assertEqual(len(d2_ids), 5)
        self.assertTrue(d1_ids.isdisjoint(d2_ids))

        # 4. เลือก "เริ่มสุ่มใหม่" บนกิจกรรมที่ 2 ด้วยจำนวน 6 คน -> ขึ้นรอบที่ 2
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act2.id,
            'draw_mode': 'reset',
            'draw_count': 6,
        })
        self.assertEqual(ActivityPresentationDraw.objects.filter(activity=act2, round_no=2).count(), 6)

        # 5. ปุ่ม "ให้คะแนนทั้งหมด" ครั้งที่ 1 เต็ม (5) -> ส่งทีหลังกดอีกครั้ง (-1 -> 4) -> ส่งอีกคน (-1 -> 3) -> ทดสอบ max_score=2 ต่ำสุด 1
        submitted_1_ids = set(ActivitySubmission.objects.filter(activity=act1, student__room_code='01').values_list('student_id', flat=True))
        unsubmitted_r01 = [s for s in room01_students if s.id not in submitted_1_ids]
        self.assertEqual(len(unsubmitted_r01), 2)

        sample_first_sub = User.objects.get(pk=next(iter(submitted_1_ids)))
        first_before = float(sample_first_sub.total_score or 0.0)

        # ครั้งที่ 1 -> 28 คนได้ +5
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'grade_all_submitted',
            'room': '01',
            'activity_id': act1.id,
        })
        b1 = ActivityBulkGradeBatch.objects.filter(activity=act1, room_code='01', batch_no=1).first()
        self.assertIsNotNone(b1)
        self.assertEqual((b1.score_given, b1.graded_count), (5.0, 28))
        sample_first_sub.refresh_from_db()
        self.assertEqual(float(sample_first_sub.total_score), first_before + 5.0)

        # กดซ้ำตอนยังไม่มีผู้ส่งใหม่ -> ไม่สร้าง batch ที่ 2
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'grade_all_submitted',
            'room': '01',
            'activity_id': act1.id,
        })
        self.assertEqual(ActivityBulkGradeBatch.objects.filter(activity=act1, room_code='01').count(), 1)

        # นักเรียนส่งช้าคนที่ 1 -> ครั้งที่ 2 ได้ 4 คะแนน
        late1 = unsubmitted_r01[0]
        late1_before = float(late1.total_score or 0.0)
        ActivitySubmission.objects.create(activity=act1, student=late1, work_title='ส่งทีหลัง 1', work_text='2')
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'grade_all_submitted',
            'room': '01',
            'activity_id': act1.id,
        })
        b2 = ActivityBulkGradeBatch.objects.filter(activity=act1, room_code='01', batch_no=2).first()
        self.assertIsNotNone(b2)
        self.assertEqual((b2.score_given, b2.graded_count), (4.0, 1))
        late1.refresh_from_db()
        self.assertEqual(float(late1.total_score), late1_before + 4.0)

        # นักเรียนส่งช้าคนที่ 2 -> ครั้งที่ 3 ได้ 3 คะแนน
        late2 = unsubmitted_r01[1]
        late2_before = float(late2.total_score or 0.0)
        ActivitySubmission.objects.create(activity=act1, student=late2, work_title='ส่งทีหลัง 2', work_text='2')
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'grade_all_submitted',
            'room': '01',
            'activity_id': act1.id,
        })
        b3 = ActivityBulkGradeBatch.objects.filter(activity=act1, room_code='01', batch_no=3).first()
        self.assertIsNotNone(b3)
        self.assertEqual((b3.score_given, b3.graded_count), (3.0, 1))
        late2.refresh_from_db()
        self.assertEqual(float(late2.total_score), late2_before + 3.0)

        # ทดสอบเพดานต่ำสุด 1 คะแนนกับกิจกรรม max_score=2
        period = act1.score_period
        mini_act = LearningActivity.objects.create(
            title='กิจกรรมย่อย 2 คะแนน', activity_type=LearningActivity.TYPE_QA, score_period=period, max_score=2.0, created_by=teacher
        )
        for st_idx, expected_pts in enumerate((2.0, 1.0, 1.0), start=1):
            st_m = room01_students[st_idx]
            ActivitySubmission.objects.create(activity=mini_act, student=st_m, work_title='ตอบ', work_text='ok')
            ct.post(reverse('core:teacher_presentations'), {
                'action': 'grade_all_submitted',
                'room': '01',
                'activity_id': mini_act.id,
            })
            b_m = ActivityBulkGradeBatch.objects.filter(activity=mini_act, room_code='01', batch_no=st_idx).first()
            self.assertEqual(b_m.score_given, expected_pts)

    def test_presentations_clean_answer_only_single_line_queue_and_viewed_green(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        act1 = LearningActivity.objects.get(title__startswith='กิจกรรมที่ 1:')
        st99 = User.objects.get(student_id='99999')
        sub99 = ActivitySubmission.objects.get(activity=act1, student=st99)
        sub99.work_title = 'หัวข้อที่ไม่ควรไปโผล่ซ้ำ'
        sub99.work_text = 'คำตอบเฉพาะนักเรียน 42'
        sub99.save()

        draw = ActivityPresentationDraw.objects.create(
            activity=act1,
            submission=sub99,
            student=st99,
            classroom=st99.classroom,
            round_no=1,
            order_index=1,
        )

        ct = Client()
        ct.force_login(teacher)
        resp = ct.get(
            reverse('core:teacher_presentations') + f'?room=01&activity_id={act1.id}&selected_draw_id={draw.id}'
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')

        # 1. Stage shows ONLY student's answer text (not work_title, not question description, not duplicate caption bar)
        self.assertContains(resp, '<div class="pres-qa-answer">คำตอบเฉพาะนักเรียน 42</div>', html=False)
        self.assertNotIn('id="stageWorkCaption"', html)
        self.assertNotIn('หัวข้อที่ไม่ควรไปโผล่ซ้ำ', html)

        # 2. Right queue renders strict 1-line row: ชื่อ นามสกุล (เลขที่) กิจกรรมที่ 1 and green viewed styles
        expected_line = f'{st99.first_name} {st99.last_name} ({st99.class_number}) กิจกรรมที่ 1'
        self.assertIn(f'<div class="pres-queue-line">{expected_line}</div>', html)
        self.assertIn('#dcfce7', html)
        self.assertIn('is-viewed', html)

    def test_activities_page_no_students_and_period_direct_vs_proportional_scoring(self):
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st99 = User.objects.get(student_id='99999')
        ct = Client()
        ct.force_login(teacher)

        # 1. หน้า /teacher/activities/ ต้องไม่มีรายชื่อนักเรียนและไม่มีตัวสุ่ม แต่มีโครงสร้างคะแนนช่วง
        act_page = ct.get(reverse('core:teacher_activities'))
        self.assertEqual(act_page.status_code, 200)
        act_html = act_page.content.decode('utf-8')
        self.assertNotIn('random_present', act_html)
        self.assertNotIn('99999', act_html)
        self.assertNotIn('สมชาย', act_html)
        self.assertIn('รวมตรงไม่หาร', act_html)
        self.assertIn('รวมแบบเฉลี่ยสัดส่วน', act_html)

        # 2. สร้างช่วงคะแนนใหม่แบบ default (direct: รวมตรงไม่หาร) มี 2 กิจกรรม (5 + 10 = 15) แต่กำหนดเต็มช่วง 20
        ct.post(reverse('core:teacher_activities'), {
            'action': 'create_period',
            'name': 'ช่วงทดสอบโครงสร้าง',
            'order_no': 9,
            'max_score': 20,
            'score_calc_mode': ScorePeriod.CALC_DIRECT,
        })
        tp = ScorePeriod.objects.get(order_no=9)
        self.assertEqual(tp.score_calc_mode, ScorePeriod.CALC_DIRECT)

        a1 = LearningActivity.objects.create(
            title='ใบงาน A', activity_type=LearningActivity.TYPE_QA, score_period=tp, max_score=5.0, created_by=teacher
        )
        a2 = LearningActivity.objects.create(
            title='ใบงาน B', activity_type=LearningActivity.TYPE_QA, score_period=tp, max_score=10.0, created_by=teacher
        )

        # นักเรียนได้ 5 จาก a1 และ 7 จาก a2 -> ดิบ = 12.0, คะแนนบวก 1.5 -> รวม 13.5
        ScoreLog.objects.filter(student=st99).delete()
        st99.recalculate_total_score()
        ScoreLog.objects.create(student=st99, score_period=tp, points=5.0, reason=f'คะแนน {a1.title}', source_module='activity')
        ScoreLog.objects.create(student=st99, score_period=tp, points=7.0, reason=f'คะแนน {a2.title}', source_module='activity')
        ScoreLog.objects.create(student=st99, score_period=tp, points=1.5, reason='บวกพิเศษ', source_module='manual')

        st99.refresh_from_db()
        self.assertEqual(float(st99.total_score), 13.5)

        # 3. สลับช่วงนี้เป็น "เฉลี่ยสัดส่วน" (เข้าเต็ม 20 จากดิบ 15 -> 12 * 20 / 15 = 16.0; รวมบวก = 17.5)
        ct.post(reverse('core:teacher_activities'), {
            'action': 'update_period_config',
            'target_period_id': tp.id,
            'name': tp.name,
            'order_no': tp.order_no,
            'max_score': 20,
            'score_calc_mode': ScorePeriod.CALC_PROPORTIONAL,
        })
        tp.refresh_from_db()
        st99.refresh_from_db()
        self.assertEqual(tp.score_calc_mode, ScorePeriod.CALC_PROPORTIONAL)
        self.assertEqual(float(st99.total_score), 17.5)

        stats_prop = compute_student_period_scores(tp, ScoreLog.objects.filter(score_period=tp, student=st99))[st99.id]
        self.assertEqual(stats_prop['raw_activity'], 12.0)
        self.assertEqual(stats_prop['activity'], 16.0)
        self.assertEqual(stats_prop['net'], 17.5)

        # ตรวจสอบการแสดงผลบนหน้าครู/นักเรียน
        ts_page = ct.get(reverse('core:teacher_scores') + f'?room=01&period_id={tp.id}')
        self.assertContains(ts_page, '16.00')
        ct.force_login(st99)
        ss_page = ct.get(reverse('core:student_scores'))
        self.assertContains(ss_page, '16.00')

        # 4. สลับกลับเป็น direct -> คะแนนรวมกลับมา 13.5 โดยไม่หาร
        ct.force_login(teacher)
        ct.post(reverse('core:teacher_activities'), {
            'action': 'update_period_config',
            'target_period_id': tp.id,
            'name': tp.name,
            'order_no': tp.order_no,
            'max_score': 20,
            'score_calc_mode': ScorePeriod.CALC_DIRECT,
        })
        st99.refresh_from_db()
        self.assertEqual(float(st99.total_score), 13.5)

    def test_activity_editor_rich_html_pdf_answer_keys_and_custom_grading_tiers(self):
        """สร้าง/แก้ไขใบงานแบบ Rich Word, แนบ Image/PDF (embed/download), เฉลยรายข้อ และกำหนดคะแนนแต่ละรอบ"""
        teacher = User.objects.get(username='admin')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        ct = Client()
        ct.force_login(teacher)

        # 1. ทดสอบอัปโหลดรูปภาพและ PDF ผ่าน api_activity_upload_media
        buf = io.BytesIO()
        Image.new('RGB', (20, 20), color=(34, 197, 94)).save(buf, format='PNG')
        buf.seek(0)
        up_img = SimpleUploadedFile('diagram.png', buf.getvalue(), content_type='image/png')
        resp_img = ct.post(reverse('core:api_activity_upload_media'), {'file': up_img, 'kind': 'image'})
        self.assertEqual(resp_img.status_code, 200)
        data_img = resp_img.json()
        self.assertTrue(data_img['ok'])
        self.assertIn('activities/editor/', data_img['url'])

        up_pdf = SimpleUploadedFile('handout.pdf', b'%PDF-1.4 fake pdf', content_type='application/pdf')
        resp_pdf = ct.post(reverse('core:api_activity_upload_media'), {'file': up_pdf, 'kind': 'pdf'})
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertIn('activities/pdfs/', resp_pdf.json()['url'])

        bad_file = SimpleUploadedFile('bad.txt', b'not-an-image', content_type='text/plain')
        resp_bad = ct.post(reverse('core:api_activity_upload_media'), {'file': bad_file, 'kind': 'image'})
        self.assertEqual(resp_bad.status_code, 400)

        # 2. เปิดหน้าสร้างใบงาน + บันทึกข้อมูลทั้ง 3 แท็บ
        resp_get_new = ct.get(reverse('core:teacher_activity_create'))
        self.assertEqual(resp_get_new.status_code, 200)

        ws_pdf = SimpleUploadedFile('worksheet_main.pdf', b'%PDF-1.4 worksheet', content_type='application/pdf')
        ans2_pdf = SimpleUploadedFile('ans_item2.pdf', b'%PDF-1.4 ans2', content_type='application/pdf')
        resp_create = ct.post(reverse('core:teacher_activity_create'), {
            'title': 'ใบงานสร้างแบบ Word และกำหนดเกณฑ์คะแนน',
            'description': 'คำอธิบายใบงานแบบย่อ',
            'content_html': f'<h3>โจทย์ทดลอง</h3><p><img src="{data_img["url"]}" alt="img"></p>',
            'activity_type': LearningActivity.TYPE_QA,
            'score_period_id': period.id,
            'max_score': '10',
            'is_active': '1',
            'pdf_display_mode': LearningActivity.PDF_EMBED,
            'worksheet_pdf': ws_pdf,
            'ans_form_present': '1',
            'ans_slot': ['1', '2'],
            'ans_order_1': '1',
            'ans_title_1': 'เฉลยข้อที่ 1',
            'ans_html_1': '<p>ตอบ 2 เพราะ 1+1=2</p>',
            'ans_pdf_mode_1': LearningActivity.PDF_EMBED,
            'ans_order_2': '2',
            'ans_title_2': 'เฉลยข้อที่ 2 แนบ PDF',
            'ans_html_2': '<p>ดูรายละเอียดใน PDF</p>',
            'ans_pdf_mode_2': LearningActivity.PDF_DOWNLOAD,
            'ans_pdf_file_2': ans2_pdf,
            'tiers_form_present': '1',
            'tier_round': ['1', '2', '3'],
            'tier_score': ['10', '8', '5.5'],
            'tier_label': ['ครั้งที่ 1 เต็ม', 'ส่งช้ารอบ 2', 'ส่งช้ารอบ 3'],
        })
        self.assertEqual(resp_create.status_code, 302)

        act = LearningActivity.objects.get(title='ใบงานสร้างแบบ Word และกำหนดเกณฑ์คะแนน')
        self.assertIn('<img src=', act.content_html)
        self.assertTrue(bool(act.worksheet_pdf))
        self.assertEqual(act.pdf_display_mode, LearningActivity.PDF_EMBED)
        self.assertEqual(act.answer_key_items.count(), 2)
        item2 = act.answer_key_items.get(order_no=2)
        self.assertEqual(item2.pdf_display_mode, LearningActivity.PDF_DOWNLOAD)
        self.assertTrue(bool(item2.pdf_file))
        tiers = list(act.grading_tiers.order_by('round_no').values_list('score', flat=True))
        self.assertEqual(tiers, [10.0, 8.0, 5.5])

        # 3. ตรวจสอบหน้าแก้ไข + ทดสอบว่า grade_all_submitted ใช้เกณฑ์รอบตรวจที่กำหนดไว้
        resp_edit_get = ct.get(reverse('core:teacher_activity_edit', args=[act.id]))
        self.assertEqual(resp_edit_get.status_code, 200)
        r01_students = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).order_by('class_number')[:3])
        self.assertEqual(len(r01_students), 3)

        for idx, expected_score in enumerate((10.0, 8.0, 5.5), start=1):
            info = get_next_activity_bulk_grade_info(act, '01')
            self.assertEqual((info['batch_no'], info['next_score'], info['has_custom_tiers']), (idx, expected_score, True))
            st_cur = r01_students[idx - 1]
            ActivitySubmission.objects.create(activity=act, student=st_cur, work_title=f'ส่งรอบ {idx}', work_text='ok')
            ct.post(reverse('core:teacher_presentations'), {
                'action': 'grade_all_submitted',
                'room': '01',
                'activity_id': act.id,
            })
            sub_cur = ActivitySubmission.objects.get(activity=act, student=st_cur)
            self.assertEqual(sub_cur.score_awarded, expected_score)

    def test_presentation_keyboard_shortcuts_and_per_item_answer_reveal_locks_student(self):
        """นำเสนอใบงานรองรับลูกศรซ้าย/ขวา และเปิดเฉลยรายข้อทำให้ฝั่งนักเรียนเห็นปุ่มเฉลย + ถูกล็อกไม่ให้แก้ไขงาน"""
        teacher = User.objects.get(username='admin')
        st99 = User.objects.get(username='99999')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        act = LearningActivity.objects.create(
            title='ใบงานทดสอบเฉลยและล็อกการส่ง',
            activity_type=LearningActivity.TYPE_QA,
            score_period=period,
            max_score=5.0,
            is_active=True,
            created_by=teacher,
        )
        ans1 = ActivityAnswerKeyItem.objects.create(
            activity=act, order_no=1, title='เฉลยข้อ 1 เปิดดูได้', content_html='<p>คำตอบที่ถูกต้องคือ 42</p>', is_revealed=False
        )
        ans2 = ActivityAnswerKeyItem.objects.create(
            activity=act, order_no=2, title='เฉลยข้อ 2 ซ่อนลับเฉพาะครู', content_html='<p>ลับเฉพาะครู</p>', is_revealed=False
        )
        sub = ActivitySubmission.objects.create(activity=act, student=st99, work_title='ส่งก่อนเปิดเฉลย', work_text='คำตอบเดิม')

        ct = Client()
        ct.force_login(teacher)
        resp_pres = ct.get(f"{reverse('core:teacher_presentations')}?room=01&activity_id={act.id}")
        self.assertEqual(resp_pres.status_code, 200)
        html_pres = resp_pres.content.decode('utf-8')
        self.assertIn('ArrowLeft', html_pres)
        self.assertIn('ArrowRight', html_pres)
        self.assertIn('answerKeyControlModal', html_pres)

        # เปิดเฉลยเฉพาะข้อที่ 1 ผ่าน API
        resp_tog = ct.post(reverse('core:api_activity_toggle_answer_item', args=[act.id]), {
            'mode': 'single',
            'item_id': ans1.id,
        })
        self.assertEqual(resp_tog.status_code, 200)
        self.assertEqual(resp_tog.json()['revealed_count'], 1)
        ans1.refresh_from_db()
        ans2.refresh_from_db()
        self.assertTrue(ans1.is_revealed)
        self.assertFalse(ans2.is_revealed)

        # นักเรียนดูหน้ากิจกรรม -> เห็นเฉลยข้อ 1, ไม่เห็นข้อ 2, และแก้คำตอบไม่ได้
        cs = Client()
        cs.force_login(st99)
        resp_st = cs.get(reverse('core:student_activities'))
        self.assertEqual(resp_st.status_code, 200)
        html_st = resp_st.content.decode('utf-8')
        self.assertIn('ดูเฉลยใบงาน', html_st)
        self.assertIn('เฉลยข้อ 1 เปิดดูได้', html_st)
        self.assertNotIn('เฉลยข้อ 2 ซ่อนลับเฉพาะครู', html_st)
        self.assertIn('request_extension', html_st)

        cs.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act.id,
            'work_text': 'แอบแก้ไขหลังเปิดเฉลย',
        })
        sub.refresh_from_db()
        self.assertEqual(sub.work_text, 'คำตอบเดิม')

        # ครูซ่อนเฉลยทั้งหมดจากหน้า นำเสนอใบงาน -> นักเรียนกลับมาแก้ไขงานได้อีกครั้ง
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'hide_all_answers',
            'room': '01',
            'activity_id': act.id,
        })
        act.refresh_from_db()
        self.assertFalse(act.has_revealed_answers)
        cs.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act.id,
            'work_text': 'คำตอบใหม่หลังครูซ่อนเฉลย',
        })
        sub.refresh_from_db()
        self.assertEqual(sub.work_text, 'คำตอบใหม่หลังครูซ่อนเฉลย')

    def test_student_extension_request_and_teacher_two_day_approval_per_student(self):
        """นักเรียนยื่นคำร้องขอส่งงานเลยกำหนด -> ครูอนุมัติ (ค่าเริ่มต้นปิดภายใน 2 วัน) เปิดสิทธิ์เฉพาะนักเรียนคนนั้น"""
        teacher = User.objects.get(username='admin')
        st99 = User.objects.get(username='99999')
        other_st = User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).exclude(pk=st99.id).first()
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        now = timezone.now()
        act_over = LearningActivity.objects.create(
            title='ใบงานเลยกำหนดส่งแล้ว',
            activity_type=LearningActivity.TYPE_QA,
            score_period=period,
            max_score=5.0,
            due_date=now - timedelta(hours=3),
            is_active=True,
            created_by=teacher,
        )

        cs = Client()
        cs.force_login(st99)
        self.assertTrue(get_student_activity_access(act_over, st99)['is_locked'])
        cs.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act_over.id,
            'work_text': 'ลองส่งตอนเลยกำหนด',
        })
        self.assertFalse(ActivitySubmission.objects.filter(activity=act_over, student=st99).exists())

        # ยื่นคำร้องขอส่งงานล่าช้า
        cs.post(reverse('core:student_activities'), {
            'action': 'request_extension',
            'activity_id': act_over.id,
            'reason': 'ติดภารกิจแข่งขันกีฬาโรงเรียน',
        })
        ext_req = ActivityExtensionRequest.objects.get(activity=act_over, student=st99)
        self.assertEqual(ext_req.status, ActivityExtensionRequest.STATUS_PENDING)

        # ครูเห็นคำร้องในหน้า ตรวจสอบการส่งงาน และอนุมัติโดยเว้นวันปิด (ค่าเริ่มต้น 2 วัน)
        ct = Client()
        ct.force_login(teacher)
        resp_sub_page = ct.get(f"{reverse('core:teacher_submissions')}?room=01&activity_id={act_over.id}")
        self.assertEqual(resp_sub_page.status_code, 200)
        self.assertIn('ติดภารกิจแข่งขันกีฬาโรงเรียน', resp_sub_page.content.decode('utf-8'))

        ct.post(reverse('core:teacher_submissions'), {
            'action': 'approve_extension',
            'request_id': ext_req.id,
            'approved_until': '',
            'teacher_note': 'อนุญาตให้ส่งภายใน 2 วัน',
        })
        ext_req.refresh_from_db()
        self.assertEqual(ext_req.status, ActivityExtensionRequest.STATUS_APPROVED)
        self.assertIsNotNone(ext_req.approved_until)
        diff_hours = (ext_req.approved_until - timezone.now()).total_seconds() / 3600.0
        self.assertGreaterEqual(diff_hours, 47.5)
        self.assertLessEqual(diff_hours, 48.5)

        # st99 ส่งได้แล้ว ส่วนนักเรียนคนอื่นในห้องเดียวกันยังถูกล็อก
        cs.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act_over.id,
            'work_text': 'ส่งงานหลังได้รับอนุมัติ 2 วัน',
        })
        self.assertTrue(ActivitySubmission.objects.filter(activity=act_over, student=st99, work_text='ส่งงานหลังได้รับอนุมัติ 2 วัน').exists())

        co = Client()
        co.force_login(other_st)
        self.assertTrue(get_student_activity_access(act_over, other_st)['is_locked'])
        co.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act_over.id,
            'work_text': 'คนอื่นแอบส่งตาม',
        })
        self.assertFalse(ActivitySubmission.objects.filter(activity=act_over, student=other_st).exists())

        # นักเรียนคนอื่นส่งคำร้องและครูปฏิเสธ -> ยังถูกล็อก
        co.post(reverse('core:student_activities'), {
            'action': 'request_extension',
            'activity_id': act_over.id,
            'reason': 'ขอส่งด้วยคน',
        })
        ext_other = ActivityExtensionRequest.objects.get(activity=act_over, student=other_st)
        ct.post(reverse('core:teacher_submissions'), {
            'action': 'reject_extension',
            'request_id': ext_other.id,
            'teacher_note': 'ไม่ผ่านเกณฑ์',
        })
        ext_other.refresh_from_db()
        self.assertEqual(ext_other.status, ActivityExtensionRequest.STATUS_REJECTED)
        self.assertTrue(get_student_activity_access(act_over, other_st)['is_locked'])

    def test_submissions_menu_filters_proportional_ungraded_scoring_and_exports(self):
        """ตรวจสอบการส่งงาน: กรองห้อง/ระดับ/สถานะ, ให้คะแนนที่ยังไม่ตรวจลดสัดส่วน % และ Export Excel/CSV"""
        teacher = User.objects.get(username='admin')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        r01 = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).order_by('class_number')[:3])
        r02_st = User.objects.filter(role=User.ROLE_STUDENT, room_code='02', is_active=True).first()
        self.assertEqual(len(r01), 3)
        self.assertIsNotNone(r02_st)

        act = LearningActivity.objects.create(
            title='ใบงานทดสอบตรวจรวมและ Export',
            activity_type=LearningActivity.TYPE_QA,
            score_period=period,
            max_score=10.0,
            is_active=True,
            created_by=teacher,
        )
        # r01[0] ตรวจแล้ว 10, r01[1] ส่งแล้วยังไม่ตรวจ, r01[2] ยังไม่ส่ง, r02_st ส่งแล้วยังไม่ตรวจ
        sub_graded = ActivitySubmission.objects.create(activity=act, student=r01[0], work_title='ตอบ 1', score_awarded=10.0)
        sub_ungraded = ActivitySubmission.objects.create(activity=act, student=r01[1], work_title='ตอบ 2', score_awarded=None)
        sub_r02 = ActivitySubmission.objects.create(activity=act, student=r02_st, work_title='ตอบห้อง 2', score_awarded=None)

        ct = Client()
        ct.force_login(teacher)

        # กรองเฉพาะยังไม่ตรวจของห้อง 01
        resp_ungraded = ct.get(f"{reverse('core:teacher_submissions')}?room=01&activity_id={act.id}&status=ungraded")
        self.assertEqual(resp_ungraded.status_code, 200)
        html_u = resp_ungraded.content.decode('utf-8')
        self.assertIn(r01[1].first_name, html_u)
        self.assertNotIn(r01[2].student_id, html_u)
        self.assertNotIn(r02_st.student_id, html_u)

        # กรองเฉพาะยังไม่ส่งของห้อง 01
        resp_missing = ct.get(f"{reverse('core:teacher_submissions')}?room=01&activity_id={act.id}&status=missing")
        self.assertEqual(resp_missing.status_code, 200)
        self.assertIn(r01[2].student_id, resp_missing.content.decode('utf-8'))

        # ให้คะแนนทั้งหมดที่ยังไม่ตรวจของห้อง 01 แบบลดสัดส่วน 80% -> เฉพาะ r01[1] ได้ 8.0
        ct.post(reverse('core:teacher_submissions'), {
            'action': 'grade_all_ungraded',
            'room': '01',
            'activity_id': act.id,
            'status': 'all',
            'grade_mode': 'percent',
            'percent_value': '80',
        })
        sub_ungraded.refresh_from_db()
        sub_graded.refresh_from_db()
        sub_r02.refresh_from_db()
        self.assertEqual(sub_ungraded.score_awarded, 8.0)
        self.assertEqual(sub_graded.score_awarded, 10.0)
        self.assertIsNone(sub_r02.score_awarded)

        # Export Excel ห้อง 01 (1 ชีต)
        resp_xlsx_01 = ct.get(f"{reverse('core:teacher_submissions_export')}?room=01&activity_id={act.id}&fmt=xlsx")
        self.assertEqual(resp_xlsx_01.status_code, 200)
        wb01 = openpyxl.load_workbook(io.BytesIO(resp_xlsx_01.content))
        self.assertEqual(len(wb01.sheetnames), 1)
        ws01 = wb01.active
        self.assertEqual(ws01.cell(row=1, column=1).value, 'ห้อง')

        # Export Excel ทั้งหมด (ชีตสรุปรวม + ชีตแยกห้อง)
        resp_xlsx_all = ct.get(f"{reverse('core:teacher_submissions_export')}?room=all&activity_id={act.id}&fmt=xlsx")
        self.assertEqual(resp_xlsx_all.status_code, 200)
        wb_all = openpyxl.load_workbook(io.BytesIO(resp_xlsx_all.content))
        self.assertIn('สรุปรวมทั้งหมด', wb_all.sheetnames)
        self.assertGreaterEqual(len(wb_all.sheetnames), 3)

        # Export CSV
        resp_csv = ct.get(f"{reverse('core:teacher_submissions_export')}?room=01&activity_id={act.id}&fmt=csv")
        self.assertEqual(resp_csv.status_code, 200)
        csv_text = resp_csv.content.decode('utf-8-sig')
        self.assertIn(r01[1].student_id, csv_text)

    def test_default_three_grading_tiers_and_last_tier_floor_plus_submissions_round_display(self):
        """สร้างกิจกรรมแล้วสร้างเกณฑ์ 3 รอบอัตโนมัติ และตรวจรอบที่ x เป็นต้นไปได้คะแนนรอบสุดท้าย + แสดงครั้งที่ตรวจในหน้าตรวจสอบการส่งงาน"""
        teacher = User.objects.get(username='admin')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        r01 = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).order_by('class_number')[:5])
        self.assertEqual(len(r01), 5)

        ct = Client()
        ct.force_login(teacher)

        # หน้าสร้างใบงานต้องแสดงเกณฑ์ 3 รอบอัตโนมัติทันที
        resp_new = ct.get(reverse('core:teacher_activity_create'))
        self.assertEqual(resp_new.status_code, 200)
        html_new = resp_new.content.decode('utf-8')
        self.assertIn('ตรวจครั้งที่ 1 (คะแนนเต็ม)', html_new)
        self.assertIn('ตรวจครั้งที่ 3 เป็นต้นไป', html_new)

        # สร้างใบงานโดยไม่ส่ง tiers_form_present -> ระบบสร้าง 3 รอบให้อัตโนมัติ (10, 9, 8)
        ct.post(reverse('core:teacher_activity_create'), {
            'title': 'ใบงานทดสอบเกณฑ์อัตโนมัติ 3 รอบ',
            'content_html': '<p>ทดสอบเกณฑ์คะแนน</p>',
            'activity_type': LearningActivity.TYPE_QA,
            'score_period_id': period.id,
            'max_score': '10',
            'is_active': '1',
        })
        act = LearningActivity.objects.get(title='ใบงานทดสอบเกณฑ์อัตโนมัติ 3 รอบ')
        tiers = list(act.grading_tiers.order_by('round_no').values_list('round_no', 'score'))
        self.assertEqual(tiers, [(1, 10.0), (2, 9.0), (3, 8.0)])

        # ตรวจรอบที่ 1..5 -> รอบที่ 3, 4, 5 ต้องได้คะแนนรอบสุดท้าย (8.0) เสมอ
        for idx, expected_sc in enumerate((10.0, 9.0, 8.0, 8.0, 8.0), start=1):
            info = get_next_activity_bulk_grade_info(act, '01')
            self.assertEqual((info['batch_no'], info['next_score']), (idx, expected_sc))
            ActivitySubmission.objects.create(activity=act, student=r01[idx - 1], work_title=f'รอบ {idx}', work_text='done')
            ct.post(reverse('core:teacher_presentations'), {
                'action': 'grade_all_submitted',
                'room': '01',
                'activity_id': act.id,
            })
            sub = ActivitySubmission.objects.get(activity=act, student=r01[idx - 1])
            self.assertEqual((sub.score_awarded, sub.graded_round_no), (expected_sc, idx))

        # หน้าตรวจสอบการส่งงานแสดงครั้งที่การตรวจของกิจกรรมและรายบุคคล
        resp_subs = ct.get(f"{reverse('core:teacher_submissions')}?room=01&activity_id={act.id}")
        self.assertEqual(resp_subs.status_code, 200)
        html_subs = resp_subs.content.decode('utf-8')
        self.assertIn('ครั้งที่การตรวจของกิจกรรม', html_subs)
        self.assertIn('ครั้งที่ 1', html_subs)
        self.assertIn('ครั้งที่ 5', html_subs)

        # Export Excel ต้องมีคอลัมน์ ครั้งที่ตรวจ
        resp_xlsx = ct.get(f"{reverse('core:teacher_submissions_export')}?room=01&activity_id={act.id}&fmt=xlsx")
        wb = openpyxl.load_workbook(io.BytesIO(resp_xlsx.content))
        headers = [c.value for c in wb.active[1]]
        self.assertIn('ครั้งที่ตรวจ', headers)

    def test_one_click_student_attendance_and_soft_green_box(self):
        """นักเรียนกดปุ่ม ยืนยันการเข้าเรียน ปุ่มเดียวได้เลย + กล่องเช็คชื่อครู/นักเรียนพื้นหลังสีเขียวอ่อน"""
        teacher = User.objects.get(username='admin')
        room01 = Classroom.objects.get(code='01')
        st99 = User.objects.get(student_id='99999')
        sess = AttendanceSession.objects.create(
            classroom=room01,
            title='คาบทดสอบ 1 คลิก',
            check_in_code='AUTO99',
            attendance_points=1.0,
            is_open=True,
            created_by=teacher,
        )

        cs = Client()
        cs.force_login(st99)
        resp_st = cs.get(reverse('core:student_attendance'))
        self.assertEqual(resp_st.status_code, 200)
        html_st = resp_st.content.decode('utf-8')
        self.assertIn('ยืนยันการเข้าเรียน', html_st)
        self.assertIn('attendance-checkin-box', html_st)
        self.assertNotIn('name="check_in_code"', html_st)

        # POST โดยไม่ต้องส่ง check_in_code และไม่ต้องส่ง seat_row/seat_col
        before_pts = float(st99.total_score or 0.0)
        resp_post = cs.post(reverse('core:student_attendance'), {}, follow=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertTrue(AttendanceRecord.objects.filter(session=sess, student=st99).exists())
        st99.refresh_from_db()
        self.assertEqual(float(st99.total_score), before_pts + 1.0)

        # ฝั่งครูมีพื้นหลังเช็คชื่อสีเขียวอ่อน
        ct = Client()
        ct.force_login(teacher)
        resp_t = ct.get(f"{reverse('core:teacher_attendance')}?classroom_id={room01.id}&session_id={sess.id}")
        self.assertEqual(resp_t.status_code, 200)
        html_t = resp_t.content.decode('utf-8')
        self.assertIn('--checkin-box-bg', html_t)
        self.assertIn('#dcfce7', html_t)

    def test_sidebar_reorganization_lesson_dropdown_and_quizzes_inside_lessons(self):
        """ตรวจสอบเมนูใหม่ทั้งฝั่งนักเรียน (แยกบรรทัดชั้น/เลขที่, ข้อมูลส่วนตัว, บทเรียน dropdown, กิจกรรม dropdown) และฝั่งครู/Admin"""
        admin = User.objects.get(username='admin')
        st99 = User.objects.get(student_id='99999')
        lesson1 = LessonContent.objects.first() or LessonContent.objects.create(
            unit_number=1, title='บทที่ 1 ทดสอบ', content_html='<p>เนื้อหา</p>', is_published=True, created_by=admin
        )
        AIQuiz.objects.create(
            lesson=lesson1, title='แบบสอบถามท้ายบททดสอบ', is_active=True, created_by=admin
        )

        # ฝั่งนักเรียน
        cs = Client()
        cs.force_login(st99)
        r_dash = cs.get(reverse('core:student_dashboard'))
        html_s = r_dash.content.decode('utf-8')
        self.assertIn('ข้อมูลส่วนตัว', html_s)
        self.assertIn('เช็คชื่อเข้าเรียน', html_s)
        self.assertIn('#studentLessonsNav', html_s)
        self.assertIn('#studentActivitiesNav', html_s)
        self.assertNotIn('ส่งผลงาน &amp; สุ่มนำเสนอ', html_s)
        self.assertNotIn('โปรไฟล์ &amp; หมุน/Crop รูป', html_s)
        self.assertIn(f'{st99.room_display} · เลขที่ {st99.class_number}', html_s)

        r_less = cs.get(reverse('core:student_lessons'))
        html_less = r_less.content.decode('utf-8')
        self.assertIn('แบบสอบถามท้ายบททดสอบ', html_less)
        self.assertIn('แบบสอบถาม / แบบทดสอบประจำบทเรียน', html_less)
        self.assertNotIn('ผู้ทำแบบทดสอบล่าสุด', html_less)

        # ฝั่ง Admin / ครู
        ca = Client()
        ca.force_login(admin)
        r_t = ca.get(reverse('core:teacher_dashboard'))
        html_t = r_t.content.decode('utf-8')
        self.assertIn('จัดการนักเรียน', html_t)
        self.assertNotIn('จัดการโปรไฟล์นักเรียน', html_t)
        self.assertNotIn('เนื้อหา &amp; สร้างข้อสอบ AI', html_t)
        self.assertIn('<div class="sidebar-section">ระบบเกม</div>', html_t)
        self.assertIn('<div class="sidebar-section">ตั้งค่าระบบ</div>', html_t)
        self.assertIn('ระบบคะแนน &amp; กฎคะแนน', html_t)

    def test_student_activities_privacy_pending_alert_and_group_drag_drop_sorted(self):
        """นักเรียนไม่เห็นผลงานคนอื่น มีแจ้งเตือนงานค้าง และกิจกรรมกลุ่มลากย้ายกลุ่มได้ เรียงตามเลขที่ จนกว่าครูปิดระบบ"""
        teacher = User.objects.get(username='admin')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        r01 = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).order_by('class_number'))
        st_low = r01[0]
        st_high = r01[-1]  # เช่น 99999 เลขที่ 30
        other_st = r01[1]

        act_solo = LearningActivity.objects.create(
            title='กิจกรรมเดี่ยวค้างส่ง', activity_type=LearningActivity.TYPE_QA, score_period=period, max_score=5.0, is_active=True, created_by=teacher
        )
        ActivitySubmission.objects.create(
            activity=act_solo, student=other_st, work_title='ลับของเพื่อน', work_text='SECRET_PEER_WORK_98765'
        )
        act_grp = LearningActivity.objects.create(
            title='กิจกรรมกลุ่มทดลอง',
            activity_type=LearningActivity.TYPE_GENERAL,
            score_period=period,
            max_score=10.0,
            is_active=True,
            is_group_activity=True,
            max_group_size=2,
            group_selection_open=True,
            created_by=teacher,
        )

        # st_high (เลขที่ 30) เข้าดูหน้ากิจกรรม -> ไม่เห็น SECRET_PEER_WORK_98765 และเห็นแจ้งเตือนงานค้าง
        c_high = Client()
        c_high.force_login(st_high)
        r_view = c_high.get(reverse('core:student_activities'))
        html = r_view.content.decode('utf-8')
        self.assertNotIn('SECRET_PEER_WORK_98765', html)
        self.assertIn('แจ้งเตือนงานค้างของคุณ', html)
        self.assertIn('my-student-drag-box', html)

        # st_high เข้ากลุ่มที่ 1 ก่อน จากนั้น st_low (เลขที่ 1) เข้ากลุ่มที่ 1 ตาม -> สมาชิกต้องเรียงตามเลขที่ (1 ก่อน 30)
        groups = list(ActivityGroup.objects.filter(activity=act_grp, room_code='01').order_by('group_no'))
        self.assertGreaterEqual(len(groups), 4)
        g1, g2 = groups[0], groups[1]

        r_mv1 = c_high.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g1.id})
        self.assertEqual(r_mv1.status_code, 200)
        self.assertTrue(r_mv1.json()['ok'])

        c_low = Client()
        c_low.force_login(st_low)
        r_mv2 = c_low.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g1.id})
        self.assertEqual(r_mv2.status_code, 200)
        members_g1 = r_mv2.json()['groups'][0]['members']
        self.assertEqual([m['class_number'] for m in members_g1], [st_low.class_number, st_high.class_number])

        # กลุ่มที่ 1 เต็ม (max_group_size=2) -> other_st เข้ากลุ่มที่ 1 ไม่ได้ (400)
        c_oth = Client()
        c_oth.force_login(other_st)
        r_full = c_oth.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g1.id})
        self.assertEqual(r_full.status_code, 400)

        # ย้ายกลุ่มได้ตราบเท่าที่ยังไม่ปิดระบบจับกลุ่ม
        r_move_g2 = c_high.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g2.id})
        self.assertEqual(r_move_g2.status_code, 200)
        self.assertEqual(r_move_g2.json()['my_group_id'], g2.id)

        # ย้ายกลับมากลุ่มที่ 1 เพื่อส่งงานกลุ่ม -> ต้องสร้าง submission ให้ทั้ง st_low และ st_high
        c_high.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g1.id})
        c_low.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act_grp.id,
            'work_title': 'โครงงานกลุ่ม 1',
            'work_text': 'ผลลัพธ์กลุ่ม 1',
        })
        self.assertTrue(ActivitySubmission.objects.filter(activity=act_grp, student=st_low, group=g1).exists())
        self.assertTrue(ActivitySubmission.objects.filter(activity=act_grp, student=st_high, group=g1).exists())

        # ครูสั่งปิดระบบจับกลุ่ม -> ย้ายกลุ่มไม่ได้อีกต่อไป (400)
        ct = Client()
        ct.force_login(teacher)
        ct.post(reverse('core:teacher_activities'), {
            'action': 'toggle_group_selection',
            'target_activity_id': act_grp.id,
        })
        act_grp.refresh_from_db()
        self.assertFalse(act_grp.group_selection_open)
        r_locked = c_high.post(reverse('core:api_activity_group_move', args=[act_grp.id]), {'group_id': g2.id})
        self.assertEqual(r_locked.status_code, 400)

    def test_admin_theme_color_customization_and_bingo_drag_purple_red_circle(self):
        """Admin ปรับแต่งสีธีมผู้ดูแล/นักเรียนได้ และ Bingo ใช้ drag ใส่ตาราง + กรอบม่วงมุม/กลาง + วงกลมแดงหนาเมื่อออกเลข"""
        admin = User.objects.get(username='admin')
        st99 = User.objects.get(student_id='99999')

        ca = Client()
        ca.force_login(admin)
        resp_save = ca.post(reverse('core:admin_oauth_settings'), {
            'action': 'save_all',
            'allowed_oauth_domain': 'debsirin.ac.th',
            'ai_provider': 'gemini',
            'theme_teacher_sidebar_bg_1': '#123456',
            'theme_teacher_sidebar_bg_2': '#234567',
            'theme_teacher_primary': '#345678',
            'theme_teacher_accent': '#fedcba',
            'theme_teacher_topbar_bg': '#f8f9fa',
            'theme_teacher_body_bg': '#edf2f7',
            'theme_teacher_checkin_bg': '#dcfce7',
            'theme_student_sidebar_bg_1': '#064e3b',
            'theme_student_sidebar_bg_2': '#065f46',
            'theme_student_primary': '#047857',
            'theme_student_accent': '#fbbf24',
            'theme_student_topbar_bg': '#ffffff',
            'theme_student_body_bg': '#f0fdf4',
            'theme_student_checkin_bg': '#d1fae5',
        }, follow=True)
        self.assertEqual(resp_save.status_code, 200)

        html_admin = ca.get(reverse('core:teacher_dashboard')).content.decode('utf-8')
        self.assertIn('#123456', html_admin)
        self.assertIn('#dcfce7', html_admin)

        cs = Client()
        cs.force_login(st99)
        html_st = cs.get(reverse('core:student_dashboard')).content.decode('utf-8')
        self.assertIn('#064e3b', html_st)
        self.assertIn('#f0fdf4', html_st)

        # รีเซ็ตกลับเป็นมาตรฐาน
        ca.post(reverse('core:admin_oauth_settings'), {'action': 'reset_theme_colors'})
        self.assertEqual(SystemSetting.get().theme_colors_json, '{}')

        # ตรวจสอบ Bingo: Drag-and-Drop คลังเลข, กรอบม่วง (#7c3aed), ไม่มีคำว่า มุม/กลาง เป็น tag, วงกลมแดงหนา (#dc2626)
        room = BingoRoom.objects.create(
            title='ห้อง Bingo Drag Test',
            size=4,
            number_range='1-20',
            is_grade_wide=True,
            status=BingoRoom.STATUS_OPEN,
            created_by=admin,
        )
        r_bingo = cs.get(reverse('core:student_bingo_play', args=[room.id]))
        self.assertEqual(r_bingo.status_code, 200)
        b_html = r_bingo.content.decode('utf-8')
        self.assertIn('bingoPoolChips', b_html)
        self.assertIn('cell.draggable = true', b_html)
        self.assertIn('#7c3aed', b_html)
        self.assertIn('#dc2626', b_html)
        self.assertNotIn('>มุม<', b_html)
        self.assertNotIn('>กลาง<', b_html)

    def test_presentations_only_registered_checkbox_filters_randomizer(self):
        """นำเสนอใบงาน: checkbox เฉพาะที่ลงทะเบียน ต้องสุ่มเฉพาะนักเรียนที่ส่งงานและลงทะเบียน (Login/เช็คชื่อ) แล้วเท่านั้น"""
        teacher = User.objects.get(username='admin')
        room01 = Classroom.objects.get(code='01')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        r01 = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).order_by('class_number')[:4])
        self.assertEqual(len(r01), 4)
        st_sess, st_att, st_unreg1, st_unreg2 = r01

        act = LearningActivity.objects.create(
            title='ใบงานทดสอบสุ่มเฉพาะที่ลงทะเบียน',
            activity_type=LearningActivity.TYPE_QA,
            score_period=period,
            max_score=5.0,
            is_active=True,
            created_by=teacher,
        )
        for idx, st in enumerate(r01, start=1):
            ActivitySubmission.objects.create(activity=act, student=st, work_title=f'คำตอบ {idx}', work_text=f'ans-{idx}')

        # ล้าง Session และ Attendance เดิมของห้อง 01 แล้วลงทะเบียนให้เฉพาะ st_sess (Login) และ st_att (เช็คชื่อ)
        UserSession.objects.filter(user__room_code='01').update(is_active=False)
        AttendanceSession.objects.filter(classroom=room01).update(is_open=False)
        AttendanceRecord.objects.filter(student__room_code='01').delete()

        UserSession.objects.create(
            user=st_sess,
            session_token='reg_sess_token_1',
            device_info='Chrome Test',
            is_active=True,
        )
        att_sess = AttendanceSession.objects.create(
            classroom=room01, title='คาบทดสอบลงทะเบียน', check_in_code='REG01', is_open=True, created_by=teacher
        )
        AttendanceRecord.objects.create(session=att_sess, student=st_att, seat_row=1, seat_col=2)

        ct = Client()
        ct.force_login(teacher)
        resp_get = ct.get(f"{reverse('core:teacher_presentations')}?room=01&activity_id={act.id}")
        self.assertEqual(resp_get.status_code, 200)
        html_get = resp_get.content.decode('utf-8')
        self.assertIn('onlyRegisteredChk', html_get)
        self.assertIn('เฉพาะที่ลงทะเบียน (2)', html_get)

        # ขอสุ่ม 3 คนในโหมด เฉพาะที่ลงทะเบียน -> ต้องถูกปฏิเสธ เพราะส่ง+ลงทะเบียนมีเพียง 2 คน
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act.id,
            'draw_mode': 'continue',
            'only_registered': '1',
            'draw_count': 3,
        })
        self.assertEqual(ActivityPresentationDraw.objects.filter(activity=act).count(), 0)

        # ขอสุ่ม 2 คนในโหมด เฉพาะที่ลงทะเบียน -> ต้องได้เฉพาะ {st_sess, st_att} เท่านั้น
        ct.post(reverse('core:teacher_presentations'), {
            'action': 'random_present',
            'room': '01',
            'activity_id': act.id,
            'draw_mode': 'continue',
            'only_registered': '1',
            'draw_count': 2,
        })
        drawn_ids = set(ActivityPresentationDraw.objects.filter(activity=act).values_list('student_id', flat=True))
        self.assertEqual(drawn_ids, {st_sess.id, st_att.id})
        self.assertNotIn(st_unreg1.id, drawn_ids)
        self.assertNotIn(st_unreg2.id, drawn_ids)

    def test_activity_group_count_yellow_bg_and_room_grade_checkbox_targeting(self):
        """สร้างใบงานกลุ่มสีเหลืองอ่อน แบ่ง X กลุ่ม พร้อมเลือก checkbox ชั้น/ห้องเรียนที่ต้องทำ"""
        teacher = User.objects.get(username='admin')
        period = ScorePeriod.objects.order_by('order_no', 'id').first()
        st_r01 = User.objects.filter(role=User.ROLE_STUDENT, room_code='01', is_active=True).first()
        st_r02 = User.objects.filter(role=User.ROLE_STUDENT, room_code='02', is_active=True).first()
        self.assertIsNotNone(st_r01)
        self.assertIsNotNone(st_r02)

        ct = Client()
        ct.force_login(teacher)
        r_new = ct.get(reverse('core:teacher_activity_create')).content.decode('utf-8')
        self.assertIn('targetAllRoomsChk', r_new)
        self.assertIn('grade-select-all-chk', r_new)
        self.assertIn('แบ่งเป็นกี่กลุ่ม', r_new)

        # สร้างกิจกรรมกลุ่มแบ่ง 10 กลุ่ม เฉพาะห้อง 01 เท่านั้น
        ct.post(reverse('core:teacher_activity_create'), {
            'title': 'ใบงานกลุ่มเฉพาะห้อง ม.4/1',
            'content_html': '<p>ทำงานกลุ่มห้อง 4/1</p>',
            'activity_type': LearningActivity.TYPE_GENERAL,
            'score_period_id': period.id,
            'max_score': '10',
            'is_active': '1',
            'is_group_activity': '1',
            'group_count': '10',
            'group_selection_open': '1',
            'target_rooms_present': '1',
            'target_room_codes': ['01'],
        })
        act = LearningActivity.objects.get(title='ใบงานกลุ่มเฉพาะห้อง ม.4/1')
        self.assertTrue(act.is_group_activity)
        self.assertEqual(act.group_count, 10)
        self.assertEqual(act.get_target_rooms_list(), ['01'])
        self.assertTrue(act.applies_to_room('01'))
        self.assertFalse(act.applies_to_room('02'))

        # ห้อง 01 มี 30 คน แบ่ง 10 กลุ่ม -> สูงสุดกลุ่มละ 3 คน
        self.assertEqual(compute_group_max_size(act, '01', 1), 3)
        self.assertEqual(compute_group_max_size(act, '01', 10), 3)

        # หน้าแก้ไขและหน้าจัดการกิจกรรมต้องแสดงพื้นหลังสีเหลืองอ่อน #fefce8
        r_edit = ct.get(reverse('core:teacher_activity_edit', args=[act.id])).content.decode('utf-8')
        self.assertIn('is-group-mode-yellow', r_edit)
        r_list = ct.get(reverse('core:teacher_activities')).content.decode('utf-8')
        self.assertIn('#fefce8', r_list)
        self.assertIn('แบ่ง 10 กลุ่ม', r_list)

        # ฝั่งนักเรียน: ห้อง 01 เห็นพร้อมพื้นหลัง #fefce8 และเห็นจำนวนสูงสุดต่อกลุ่ม; ห้อง 02 ไม่เห็นกิจกรรมนี้เลย
        c1 = Client()
        c1.force_login(st_r01)
        h1 = c1.get(reverse('core:student_activities')).content.decode('utf-8')
        self.assertIn('ใบงานกลุ่มเฉพาะห้อง ม.4/1', h1)
        self.assertIn('#fefce8', h1)
        self.assertIn('ไม่เกิน 3 คน', h1)

        c2 = Client()
        c2.force_login(st_r02)
        h2 = c2.get(reverse('core:student_activities')).content.decode('utf-8')
        self.assertNotIn('ใบงานกลุ่มเฉพาะห้อง ม.4/1', h2)

        # หน้าตรวจสอบการส่งงานกรองห้อง 02 ก็ไม่แสดงกิจกรรมนี้ของห้อง 01
        r_sub_02 = ct.get(f"{reverse('core:teacher_submissions')}?room=02&activity_id={act.id}").content.decode('utf-8')
        self.assertIn('ไม่พบข้อมูลที่ตรงกับตัวกรอง', r_sub_02)

    def test_sample_group_activity_room01_10_groups_20_members_excluding_99999(self):
        """ตัวอย่างกิจกรรมที่ 3 ห้อง 4/1: แบ่ง 10 กลุ่ม เลือกแล้ว 20 คน ยกเว้น 99999 พร้อมให้ 99999 เข้าทดลองเลือกกลุ่มได้"""
        teacher = User.objects.get(username='admin')
        st99 = User.objects.get(student_id='99999')
        res = seed_sample_activities_room01(teacher_user=teacher, force_refresh_samples=True)
        act3 = res['act3']

        self.assertTrue(act3.is_group_activity)
        self.assertEqual(act3.group_count, 10)
        self.assertEqual(act3.get_target_rooms_list(), ['01'])

        groups_r01 = list(ActivityGroup.objects.filter(activity=act3, room_code='01').order_by('group_no'))
        self.assertEqual(len(groups_r01), 10)
        total_members = ActivityGroupMember.objects.filter(activity=act3, group__room_code='01').count()
        self.assertEqual(total_members, 20)
        self.assertFalse(ActivityGroupMember.objects.filter(activity=act3, student=st99).exists())

        # กลุ่มที่ 1 เต็ม 3/3 แล้ว, กลุ่มที่ 10 มี 1/3 (ว่าง 2 ที่) -> 99999 เข้ากลุ่ม 1 ไม่ได้ แต่เข้ากลุ่ม 10 ได้!
        cs = Client()
        cs.force_login(st99)
        r_page = cs.get(f"{reverse('core:student_activities')}?activity_id={act3.id}")
        self.assertEqual(r_page.status_code, 200)
        h_page = r_page.content.decode('utf-8')
        self.assertIn('กิจกรรมที่ 3: กิจกรรมกลุ่ม (แบ่ง 10 กลุ่ม)', h_page)
        self.assertIn('#fefce8', h_page)

        r_full = cs.post(reverse('core:api_activity_group_move', args=[act3.id]), {'group_id': groups_r01[0].id})
        self.assertEqual(r_full.status_code, 400)

        r_ok = cs.post(reverse('core:api_activity_group_move', args=[act3.id]), {'group_id': groups_r01[9].id})
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(r_ok.json()['my_group_id'], groups_r01[9].id)

    def test_activity_default_score_5_and_remembers_last_score(self):
        """ค่าเริ่มต้นคะแนนกิจกรรมคือ 5 และจำคะแนนกิจกรรมล่าสุดไว้เป็นค่าเริ่มต้นของกิจกรรมใหม่ทุกครั้ง"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ct = Client()
        ct.force_login(teacher)

        setting = SystemSetting.objects.first()
        self.assertEqual(float(setting.last_activity_max_score), 5.0)

        r_new1 = ct.get(reverse('core:teacher_activity_create'))
        self.assertEqual(r_new1.status_code, 200)
        html_new1 = r_new1.content.decode('utf-8')
        self.assertRegex(html_new1, r'id="editorMaxScoreInput"[^>]*value="5(\.0)?"')

        # สร้างใบงานคะแนนเต็ม 8.5 -> ระบบต้องจำ 8.5 ไว้เป็นค่าเริ่มต้นถัดไป
        r_save = ct.post(reverse('core:teacher_activity_create'), {
            'title': 'ใบงานจำคะแนน 8.5',
            'activity_type': LearningActivity.TYPE_QA,
            'max_score': '8.5',
            'is_active': '1',
        })
        self.assertEqual(r_save.status_code, 302)
        setting.refresh_from_db()
        self.assertEqual(float(setting.last_activity_max_score), 8.5)

        r_new2 = ct.get(reverse('core:teacher_activity_create'))
        self.assertEqual(r_new2.status_code, 200)
        self.assertIn('value="8.5"', r_new2.content.decode('utf-8'))

        r_list = ct.get(reverse('core:teacher_activities'))
        self.assertEqual(r_list.status_code, 200)
        self.assertIn('value="8.5"', r_list.content.decode('utf-8'))

        # สร้างผ่านหน้า /teacher/activities/ ด้วยคะแนน 12 -> จำค่า 12.0
        period = ScorePeriod.objects.first()
        r_modal_create = ct.post(reverse('core:teacher_activities'), {
            'action': 'create_activity',
            'title': 'ใบงานจำคะแนน 12',
            'description': 'ทดสอบ',
            'activity_type': LearningActivity.TYPE_GENERAL,
            'max_score': '12',
            'score_period_id': period.id if period else '',
        })
        self.assertEqual(r_modal_create.status_code, 302)
        setting.refresh_from_db()
        self.assertEqual(float(setting.last_activity_max_score), 12.0)

    def test_student_login_redirects_to_attendance_hides_button_when_closed_and_25min_auto_close(self):
        """นักเรียนเข้าสู่ระบบแล้วเข้าหน้าเช็คชื่อทันที, ซ่อนปุ่มเมื่อปิดเช็คชื่อ และปิดอัตโนมัติใน 25 นาที"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        st99 = User.objects.get(student_id='99999')
        AttendanceSession.objects.update(is_open=False)

        # 1. นักเรียนล็อกอิน OAuth หรือเข้า core:home -> เด้งเข้าหน้าเช็คชื่อทันที
        cs = Client()
        r_oauth = cs.post(reverse('core:google_oauth_login'), {'google_email': 's99999@debsirin.ac.th'}, follow=True)
        self.assertEqual(r_oauth.status_code, 200)
        self.assertEqual(r_oauth.redirect_chain[-1][0], reverse('core:student_attendance'))

        r_home = cs.get(reverse('core:home'))
        self.assertRedirects(r_home, reverse('core:student_attendance'))

        # 2. กรณีไม่ได้เปิดเช็คชื่อห้อง 01 -> ไม่มีปุ่ม ยืนยันการเข้าเรียน และ POST เช็คชื่อไม่ได้
        r_closed = cs.get(reverse('core:student_attendance'))
        self.assertEqual(r_closed.status_code, 200)
        html_closed = r_closed.content.decode('utf-8')
        self.assertNotIn('id="studentCheckinForm"', html_closed)
        self.assertNotIn('<button type="submit"', html_closed)
        cs.post(reverse('core:student_attendance'), {'seat_row': 1, 'seat_col': 1})
        self.assertEqual(AttendanceRecord.objects.filter(student=st99).count(), 0)

        # 3. ครูเปิดเช็คชื่อ -> ตั้ง auto_close_minutes = 25, นักเรียนเห็นปุ่ม ยืนยันการเข้าเรียน + ตัวจับเวลา
        ct = Client()
        ct.force_login(teacher)
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'create_session',
            'classroom_id': room01.id,
            'title': 'รอบทดสอบ 25 นาที',
            'check_in_code': 'T25MIN',
            'attendance_points': '1.0',
        })
        sess = AttendanceSession.objects.filter(classroom=room01, check_in_code='T25MIN').first()
        self.assertIsNotNone(sess)
        self.assertTrue(sess.is_open)
        self.assertEqual(sess.auto_close_minutes, 25)
        self.assertIsNotNone(sess.opened_at)
        self.assertGreater(sess.remaining_seconds, 0)

        r_open = cs.get(reverse('core:student_attendance'))
        html_open = r_open.content.decode('utf-8')
        self.assertIn('id="studentCheckinForm"', html_open)
        self.assertIn('ยืนยันการเข้าเรียน', html_open)
        self.assertIn('studentCheckinTimer', html_open)

        # 4. ครบ 25 นาที (ย้อนเวลา opened_at ไป 26 นาที) -> ปิดอัตโนมัติทันที และปุ่มหายไป
        sess.opened_at = timezone.now() - timedelta(minutes=26)
        sess.save(update_fields=['opened_at'])
        r_expired = cs.get(reverse('core:student_attendance'))
        html_expired = r_expired.content.decode('utf-8')
        sess.refresh_from_db()
        self.assertFalse(sess.is_open)
        self.assertNotIn('id="studentCheckinForm"', html_expired)
        self.assertNotIn('<button type="submit"', html_expired)

        # 5. ครูสามารถกดเปิดเช็คชื่อใหม่หรือปิดก่อนครบ 25 นาทีได้เอง
        ct.post(reverse('core:teacher_attendance'), {
            'action': 'toggle_session',
            'session_id': sess.id,
        })
        sess.refresh_from_db()
        self.assertTrue(sess.is_open)
        self.assertGreater(sess.remaining_seconds, 1400)

        ct.post(reverse('core:teacher_attendance'), {
            'action': 'toggle_session',
            'session_id': sess.id,
        })
        sess.refresh_from_db()
        self.assertFalse(sess.is_open)

    def test_sidebar_dropdowns_for_teacher_and_student(self):
        """เมนูด้านข้างของผู้ดูแลระบบ/ครู และนักเรียนเป็นแบบ dropdown ครบทุกหมวด"""
        teacher = User.objects.filter(role=User.ROLE_ADMIN).first() or User.objects.filter(role=User.ROLE_TEACHER).first()
        st99 = User.objects.get(student_id='99999')

        ct = Client()
        ct.force_login(teacher)
        r_t = ct.get(reverse('core:teacher_dashboard'))
        self.assertEqual(r_t.status_code, 200)
        html_t = r_t.content.decode('utf-8')
        for token in (
            'href="#teacherMainNav"', 'id="teacherMainNav"',
            'href="#teacherGamesNav"', 'id="teacherGamesNav"',
            'href="#teacherSettingsNav"', 'id="teacherSettingsNav"',
            '<div class="sidebar-section">ระบบเกม</div>',
            '<div class="sidebar-section">ตั้งค่าระบบ</div>',
        ):
            self.assertIn(token, html_t)

        cs = Client()
        cs.force_login(st99)
        r_s = cs.get(reverse('core:student_attendance'))
        self.assertEqual(r_s.status_code, 200)
        html_s = r_s.content.decode('utf-8')
        for token in (
            'href="#studentMainNav"', 'id="studentMainNav"',
            'href="#studentGamesNav"', 'id="studentGamesNav"',
            'เช็คชื่อเข้าเรียน',
        ):
            self.assertIn(token, html_s)

    def test_sorting_multi_room_default_attendance_play_finish_exam_stats_and_delete(self):
        """Sorting เลือกหลายห้อง, ค่าเริ่มต้นจากห้องที่เช็คชื่อล่าสุดแม้ปิดแล้ว, PLAY, จบการสอบบันทึกสถิติ/เปิดสอบใหม่/ลบ"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        room02 = Classroom.objects.get(code='02')
        room03, _ = Classroom.objects.get_or_create(code='03', defaults={'name': 'ม.4/3', 'grade_level': '4'})
        st_r01 = User.objects.get(student_id='99999')
        st_r02 = User.objects.filter(role=User.ROLE_STUDENT, room_code='02').first()
        if not st_r02:
            st_r02 = User.objects.create(
                username='test_s02_sort', student_id='99902', email='s99902@debsirin.ac.th',
                role=User.ROLE_STUDENT, classroom=room02, room_code='02', class_number=1,
                first_name='ห้องสอง', last_name='ทดสอบ',
            )
        st_r03, _ = User.objects.get_or_create(
            username='test_s03_sort',
            defaults={
                'student_id': '99903', 'email': 's99903@debsirin.ac.th',
                'role': User.ROLE_STUDENT, 'classroom': room03, 'room_code': '03',
                'class_number': 1, 'first_name': 'ห้องสาม', 'last_name': 'ทดสอบ',
            },
        )

        # ปิดเช็คชื่อทั้งหมด แล้วสร้างประวัติเช็คชื่อห้อง 02 ล่าสุด (แม้ปิดแล้ว ค่าเริ่มต้น Sorting ต้องเป็น 02)
        AttendanceSession.objects.update(is_open=False)
        AttendanceSession.objects.create(
            classroom=room02, title='เช็คชื่อห้อง 2 ล่าสุด', check_in_code='R02DEF',
            is_open=False, created_by=teacher, opened_at=timezone.now(),
        )

        act = SortingActivity.objects.first()
        ct = Client()
        ct.force_login(teacher)

        r_default = ct.get(reverse('core:teacher_sorting_manage'))
        self.assertEqual(r_default.status_code, 200)
        self.assertEqual(r_default.context['selected_room'], '02')
        html_def = r_default.content.decode('utf-8')
        self.assertIn('ฝึกเรียงลำดับข้อมูล', html_def)
        self.assertIn('id="sortingRoomCheckboxMenu"', html_def)
        self.assertNotIn('สร้างรอบกิจกรรมเรียงลำดับใหม่', html_def)
        self.assertNotIn('type="number"', html_def)

        # เลือกหลายห้องพร้อมกัน (01, 02)
        r_multi = ct.get(reverse('core:teacher_sorting_manage') + f'?activity_id={act.id}&rooms=01&rooms=02')
        self.assertEqual(r_multi.status_code, 200)
        self.assertEqual(r_multi.context['selected_room'], '01,02')

        # กด PLAY เฉพาะห้อง 01,02 -> สร้าง SortingExamRecord(status='active') และนักเรียนห้อง 01/02 เข้าโหมดสอบ ส่วน 03 ฝึกอิสระ
        play_res = ct.post(reverse('core:api_sorting_broadcast_play', args=[act.id]), {
            'sort_type': 'bubble', 'sort_order': 'asc', 'unit_count': 7, 'room_code': '01,02',
        }).json()
        self.assertTrue(play_res['ok'])
        rec = SortingExamRecord.objects.filter(activity=act, session_no=play_res['session_no']).first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, SortingExamRecord.STATUS_ACTIVE)
        self.assertEqual(rec.room_filter, '01,02')

        c1, c2, c3 = Client(), Client(), Client()
        c1.force_login(st_r01)
        c2.force_login(st_r02)
        c3.force_login(st_r03)
        self.assertTrue(c1.post(reverse('core:api_sorting_heartbeat', args=[act.id])).json()['student_in_exam'])
        self.assertTrue(c2.post(reverse('core:api_sorting_heartbeat', args=[act.id])).json()['student_in_exam'])
        self.assertFalse(c3.post(reverse('core:api_sorting_heartbeat', args=[act.id])).json()['student_in_exam'])

        # นักเรียนห้อง 01 ส่งคะแนนในรอบสอบนี้
        sub = c1.post(reverse('core:api_sorting_submit', args=[act.id]), {
            'session_no': play_res['session_no'], 'right_rounds': 7, 'attempts': 0,
            'time_used': 15.0, 'unit_count': 7, 'sort_type': 'bubble', 'sort_order': 'asc',
        }).json()
        self.assertTrue(sub['ok'])

        # จบการสอบ & บันทึกสถิติ -> ต้องเก็บสถิติและแสดงในหน้าจัดการ
        r_fin = ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'finish_exam', 'activity_id': act.id, 'room': '01,02',
        }, follow=True)
        self.assertEqual(r_fin.status_code, 200)
        rec.refresh_from_db()
        self.assertEqual(rec.status, SortingExamRecord.STATUS_FINISHED)
        self.assertGreaterEqual(rec.participant_count, 1)
        self.assertGreater(rec.max_score, 0.0)
        stats = rec.get_stats_list()
        self.assertGreaterEqual(len(stats), 1)
        self.assertEqual(stats[0]['student_id'], st_r01.student_id)
        self.assertIn(f'examStatsModal_{rec.id}', r_fin.content.decode('utf-8'))

        # ปิดระบบสอบชั่วคราว -> นักเรียนฝึกฝนอิสระได้ตลอด
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'pause_exam', 'activity_id': act.id, 'room': '01,02',
        })
        act.refresh_from_db()
        rec.refresh_from_db()
        self.assertFalse(act.is_exam_mode)
        self.assertEqual(rec.status, SortingExamRecord.STATUS_PAUSED)

        # เปิดสอบใหม่จากสถิติรอบนี้ แล้วลบสถิติทิ้ง
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'reopen_exam', 'activity_id': act.id, 'record_id': rec.id, 'room': '01,02',
        })
        act.refresh_from_db()
        rec.refresh_from_db()
        self.assertTrue(act.is_exam_mode)
        self.assertEqual(rec.status, SortingExamRecord.STATUS_ACTIVE)

        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'delete_exam_record', 'activity_id': act.id, 'record_id': rec.id, 'room': '01,02',
        })
        self.assertFalse(SortingExamRecord.objects.filter(pk=rec.id).exists())

    def test_group_activity_late_last_x_penalizes_entire_group_and_cancels_cleanly(self):
        """นักเรียนเข้าสาย X คนสุดท้าย -> สมาชิกทุกคนในกลุ่มกิจกรรมเดียวกันถูกหักคะแนนทั้งกลุ่ม และยกเลิกคืนคะแนนครบ"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        students = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:3])
        st_late, st_mate1, st_other = students[0], students[1], students[2]

        grp_act = LearningActivity.objects.create(
            title='ใบงานกลุ่มลงโทษสายทั้งกลุ่ม', is_group_activity=True, group_count=3,
            max_group_size=4, target_rooms_json=json.dumps(['01']), created_by=teacher,
        )
        g1 = ActivityGroup.objects.create(activity=grp_act, room_code='01', group_no=1, name='กลุ่มที่ 1')
        g2 = ActivityGroup.objects.create(activity=grp_act, room_code='01', group_no=2, name='กลุ่มที่ 2')
        ActivityGroupMember.objects.create(activity=grp_act, group=g1, student=st_late)
        ActivityGroupMember.objects.create(activity=grp_act, group=g1, student=st_mate1)
        ActivityGroupMember.objects.create(activity=grp_act, group=g2, student=st_other)

        sess = AttendanceSession.objects.create(
            classroom=room01, title='คาบทดสอบเข้าสายกลุ่ม', check_in_code='LATEGRP', is_open=True, created_by=teacher,
        )
        AttendanceRecord.objects.create(session=sess, student=st_late, seat_row=1, seat_col=1)
        AttendanceRecord.objects.create(session=sess, student=st_mate1, seat_row=1, seat_col=2)
        AttendanceRecord.objects.create(session=sess, student=st_other, seat_row=1, seat_col=3)

        b_late = float(st_late.total_score or 0.0)
        b_mate = float(st_mate1.total_score or 0.0)
        b_other = float(st_other.total_score or 0.0)

        ct = Client()
        ct.force_login(teacher)
        resp = ct.post(reverse('core:api_classroom_attendance_batch_score', args=[room01.id]), {
            'action': 'award',
            'session_id': sess.id,
            'student_ids': [str(st_late.id)],
            'points': '-1',
            'reason': 'เข้าสาย 1 คนสุดท้าย',
            'apply_to_group': '1',
            'group_activity_id': grp_act.id,
        }).json()
        self.assertTrue(resp['ok'])
        self.assertEqual(resp['direct_count'], 1)
        self.assertEqual(resp['group_penalty_count'], 1)
        self.assertEqual(resp['affected_count'], 2)

        st_late.refresh_from_db()
        st_mate1.refresh_from_db()
        st_other.refresh_from_db()
        self.assertAlmostEqual(float(st_late.total_score), b_late - 1.0, places=2)
        self.assertAlmostEqual(float(st_mate1.total_score), b_mate - 1.0, places=2)
        self.assertAlmostEqual(float(st_other.total_score), b_other, places=2)

        # ยกเลิก batch -> คืนคะแนนทั้งคนมาสายและสมาชิกกลุ่มครบถ้วน
        c_resp = ct.post(reverse('core:api_classroom_attendance_batch_score', args=[room01.id]), {
            'action': 'cancel', 'session_id': sess.id, 'batch_id': resp['batch_id'],
        }).json()
        self.assertTrue(c_resp['ok'])
        self.assertEqual(c_resp['cancelled_count'], 2)
        st_late.refresh_from_db()
        st_mate1.refresh_from_db()
        self.assertAlmostEqual(float(st_late.total_score), b_late, places=2)
        self.assertAlmostEqual(float(st_mate1.total_score), b_mate, places=2)

    def test_individual_activity_group_and_material_check_grading_and_new_submissions_ui(self):
        """กิจกรรมเดี่ยว: ตัวเลือก พิจารณาแบบกลุ่ม + พิจารณาตรวจสอบ + หน้าตรวจสอบการส่งงานเอาลดสัดส่วนออก"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        period = ScorePeriod.objects.filter(is_finalized=False).first()
        st = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:4])
        # Group A: st[0], st[1] (ทั้งคู่ส่งงาน, เฉพาะ st[0] ถูกกดตรวจสอบ, st[1] ไม่ถูกตรวจสอบ)
        # Group B: st[2], st[3] (st[2] ส่งงาน + ตรวจสอบ, st[3] ไม่ส่งงาน + ตรวจสอบ)
        grp_act = LearningActivity.objects.create(
            title='ใบงานกลุ่มอ้างอิง', is_group_activity=True, group_count=2, score_period=period,
            target_rooms_json=json.dumps(['01']), created_by=teacher,
        )
        g_a = ActivityGroup.objects.create(activity=grp_act, room_code='01', group_no=1, name='กลุ่ม A')
        g_b = ActivityGroup.objects.create(activity=grp_act, room_code='01', group_no=2, name='กลุ่ม B')
        ActivityGroupMember.objects.create(activity=grp_act, group=g_a, student=st[0])
        ActivityGroupMember.objects.create(activity=grp_act, group=g_a, student=st[1])
        ActivityGroupMember.objects.create(activity=grp_act, group=g_b, student=st[2])
        ActivityGroupMember.objects.create(activity=grp_act, group=g_b, student=st[3])

        ind_act = LearningActivity.objects.create(
            title='ใบงานเดี่ยวเช็คกลุ่ม+สมุด', is_group_activity=False, score_period=period,
            max_score=5.0, target_rooms_json=json.dumps(['01']),
            require_group_complete=True, require_material_check=True, group_source_activity=grp_act,
            created_by=teacher,
        )
        today = timezone.localdate()
        StudentMaterialCheck.objects.create(classroom=room01, student=st[0], check_date=today, has_book=True, has_notebook=True)
        StudentMaterialCheck.objects.create(classroom=room01, student=st[2], check_date=today, has_book=True, has_notebook=True)
        StudentMaterialCheck.objects.create(classroom=room01, student=st[3], check_date=today, has_book=True, has_notebook=True)

        sub0 = ActivitySubmission.objects.create(activity=ind_act, student=st[0], work_title='A0')
        sub1 = ActivitySubmission.objects.create(activity=ind_act, student=st[1], work_title='A1')
        sub2 = ActivitySubmission.objects.create(activity=ind_act, student=st[2], work_title='B2')

        ct = Client()
        ct.force_login(teacher)

        # หน้าสร้าง/กำหนดคะแนนกิจกรรม ต้องไม่มีตัวเลือก พิจารณาแบบกลุ่ม และ พิจารณาตรวจสอบ (ใช้เฉพาะตอนตรวจสอบการส่งงานเท่านั้น)
        ed_new_html = ct.get(reverse('core:teacher_activity_create')).content.decode('utf-8')
        ed_edit_html = ct.get(reverse('core:teacher_activity_edit', args=[ind_act.id])).content.decode('utf-8')
        for ed_html in (ed_new_html, ed_edit_html):
            self.assertNotIn('ตัวเลือกการให้คะแนนเพิ่มเติมสำหรับกิจกรรมเดี่ยว', ed_html)
            self.assertNotIn('พิจารณาแบบกลุ่ม', ed_html)
            self.assertNotIn('พิจารณาตรวจสอบ', ed_html)

        # หน้า UI ตรวจสอบการส่งงานต้องไม่มีข้อความเก่า + มี checkbox เงื่อนไขเพิ่มเติม (ค่าเริ่มต้นไม่ถูกติ๊กจากกิจกรรม) + ช่องคะแนนเต็ม/คะแนนที่ได้รับแบบ readonly
        page = ct.get(f"{reverse('core:teacher_submissions')}?room=01&activity_id={ind_act.id}")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode('utf-8')
        self.assertNotIn('ตามเกณฑ์รอบตรวจแต่ละกิจกรรม / ลดทีละ 1', html)
        self.assertNotIn('ลดตามสัดส่วนคงเหลือ', html)
        self.assertIn('พิจารณาแบบกลุ่ม', html)
        self.assertIn('พิจารณาตรวจสอบ', html)
        self.assertIn('id="chkConsiderGroup"', html)
        self.assertIn('id="chkConsiderMaterial"', html)
        self.assertNotRegex(html, r'id="chkConsiderGroup"[^>]*checked')
        self.assertNotRegex(html, r'id="chkConsiderMaterial"[^>]*checked')
        self.assertIn('id="bulkAwardScoreInput"', html)
        self.assertIn('id="btnEnableBulkScoreEdit"', html)

        # ตรวจโดยเปิดทั้ง consider_group + consider_material_check -> ทั้ง Group A (st[1] ไม่ตรวจสมุด) และ Group B (st[3] ไม่ส่งงาน) ไม่ผ่าน
        ct.post(reverse('core:teacher_submissions'), {
            'action': 'grade_all_ungraded',
            'room': '01',
            'activity_id': ind_act.id,
            'consider_group': '1',
            'consider_material_check': '1',
            'group_source_activity_id': grp_act.id,
            'check_date': today.strftime('%Y-%m-%d'),
        })
        sub0.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        self.assertIsNone(sub0.score_awarded)
        self.assertIsNone(sub1.score_awarded)
        self.assertIsNone(sub2.score_awarded)

        # ตรวจเฉพาะ consider_group=1 (ไม่เปิด material) -> Group A ส่งครบทั้งกลุ่ม จึงได้คะแนนทั้ง sub0, sub1! ส่วน Group B ยังขาด sub3
        ct.post(reverse('core:teacher_submissions'), {
            'action': 'grade_all_ungraded',
            'room': '01',
            'activity_id': ind_act.id,
            'consider_group': '1',
            'group_source_activity_id': grp_act.id,
        })
        sub0.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        self.assertEqual(sub0.score_awarded, 5.0)
        self.assertEqual(sub1.score_awarded, 5.0)
        self.assertIsNone(sub2.score_awarded)

        # รีเซ็ตและทดสอบ consider_material_check อย่างเดียว -> sub0 และ sub2 ถูกตรวจสอบ (ได้คะแนนตามรอบ 1 หรือ override=4), sub1 ไม่ถูกตรวจ
        sub0.score_awarded = None; sub0.save(update_fields=['score_awarded'])
        sub1.score_awarded = None; sub1.save(update_fields=['score_awarded'])
        ct.post(reverse('core:teacher_submissions'), {
            'action': 'grade_all_ungraded',
            'room': '01',
            'activity_id': ind_act.id,
            'consider_material_check': '1',
            'use_override_score': '1',
            'override_score': '4',
            'check_date': today.strftime('%Y-%m-%d'),
        })
        sub0.refresh_from_db()
        sub1.refresh_from_db()
        sub2.refresh_from_db()
        self.assertEqual(sub0.score_awarded, 4.0)
        self.assertIsNone(sub1.score_awarded)
        self.assertEqual(sub2.score_awarded, 4.0)

    def test_student_activity_ai_review_and_peer_similarity_warning(self):
        """AI ตรวจคำตอบเบื้องต้นระหว่างส่งว่าเพียงพอ/ตรงคำถามหรือไม่ และเตือนเมื่อคำตอบคล้ายเพื่อน"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:2])
        act = LearningActivity.objects.create(
            title='อธิบายอัลกอริทึม Bubble Sort และหลักการทำงาน',
            description='จงอธิบายขั้นตอนการสลับตำแหน่งใน Bubble Sort โดยละเอียด',
            activity_type=LearningActivity.TYPE_QA,
            max_score=5.0,
            target_rooms_json=json.dumps(['01']),
            created_by=teacher,
        )
        peer_ans = 'Bubble Sort คือการจัดเรียงข้อมูลโดยเปรียบเทียบสมาชิกคู่ที่ติดกันแล้วสลับตำแหน่งไปเรื่อยๆ'
        ActivitySubmission.objects.create(activity=act, student=st[0], work_title='Bubble Sort', work_text=peer_ans)

        cs = Client()
        cs.force_login(st[1])

        # 1. คำตอบสั้นไม่เพียงพอ ("ไม่รู้") -> AI แจ้งเตือน
        r_short = cs.post(reverse('core:api_activity_ai_check', args=[act.id]), {
            'work_title': 'ไม่รู้', 'work_text': 'ไม่รู้',
        }).json()
        self.assertTrue(r_short['ok'])
        self.assertFalse(r_short['is_sufficient'])
        self.assertEqual(r_short['status'], 'warning')

        # 2. คำตอบนอกประเด็น ("กินข้าวเหนียวหมูปิ้งอร่อยมากครับวันนี้") -> AI เตือนว่าไม่ตรงคำถาม
        r_off = cs.post(reverse('core:api_activity_ai_check', args=[act.id]), {
            'work_title': 'บันทึกประจำวัน', 'work_text': 'กินข้าวเหนียวหมูปิ้งอร่อยมากครับวันนี้',
        }).json()
        self.assertTrue(r_off['ok'])
        self.assertFalse(r_off['is_relevant'])

        # 3. คำตอบคล้ายกับของเพื่อน (st[0]) -> แจ้ง similarity_warning ทั้งใน API และเมื่อกดส่งจริง
        r_sim = cs.post(reverse('core:api_activity_ai_check', args=[act.id]), {
            'work_title': 'Bubble Sort', 'work_text': peer_ans,
        }).json()
        self.assertTrue(r_sim['is_similar'])
        self.assertGreaterEqual(r_sim['similarity_percent'], 90.0)
        self.assertIn('คล้ายกับเพื่อน', r_sim['similarity_warning'])

        resp_sub = cs.post(reverse('core:student_activities'), {
            'action': 'submit_work',
            'activity_id': act.id,
            'work_title': 'Bubble Sort',
            'work_text': peer_ans,
        }, follow=True)
        self.assertEqual(resp_sub.status_code, 200)
        sub_saved = ActivitySubmission.objects.get(activity=act, student=st[1])
        self.assertIn('คล้ายกับเพื่อน', sub_saved.similarity_warning)

    def test_game_menu_order_no_parens_and_bingo_stats_integrated(self):
        """เรียงเมนูเกมใหม่ (วงล้อ Bingo -> สุ่ม ID Lotto -> เรียงลำดับ), สถิติ Bingo รวมในหน้าวงล้อ และไม่มีข้อความวงเล็บอธิบาย"""
        admin = User.objects.get(username='admin')
        ct = Client()
        ct.force_login(admin)
        html_t = ct.get(reverse('core:teacher_dashboard')).content.decode('utf-8')
        idx_bingo = html_t.find('วงล้อ Bingo')
        idx_lotto = html_t.find('สุ่ม ID Lotto')
        idx_sort = html_t.find('เรียงลำดับ')
        self.assertGreater(idx_bingo, 0)
        self.assertLess(idx_bingo, idx_lotto)
        self.assertLess(idx_lotto, idx_sort)
        self.assertNotIn('(Sorting)', html_t)
        self.assertNotIn('Debsirin Lotto', html_t)

        # สถิติ Bingo แสดงรวมอยู่ในหน้าจัดการวงล้อ Bingo
        r_stats_tab = ct.get(reverse('core:teacher_bingo_manage') + '?tab=stats')
        self.assertEqual(r_stats_tab.status_code, 200)
        h_stats = r_stats_tab.content.decode('utf-8')
        self.assertIn('ตัวเลขที่ถูกเปิดบ่อย', h_stats)
        self.assertIn('สถิติผู้ชนะ Bingo แยกตามห้องและกลุ่ม', h_stats)

        room = BingoRoom.objects.first()
        r_ctrl = ct.get(reverse('core:teacher_bingo_control', args=[room.id]))
        self.assertEqual(r_ctrl.status_code, 200)
        self.assertIn('ให้นักเรียนเลขที่ X เป็นผู้ออกเลข', r_ctrl.content.decode('utf-8'))
        self.assertIn('ตัวเลขที่ถูกเปิดบ่อย', r_ctrl.content.decode('utf-8'))

    def test_bingo_teacher_grants_picker_class_number_guard_and_forfeit(self):
        """Bingo: นักเรียนต้องรอครูกดให้สิทธิ์เลขที่ X แล้วกด 'กดรับสิทธิ์ออกเลข' ก่อน จึงจะเลือกเลขได้ และกดสละสิทธิ์ได้เมื่อไม่มีเลขที่เลือกได้"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        st = User.objects.get(student_id='99999')
        room = BingoRoom.objects.create(
            title='Bingo ทดสอบสิทธิ์เลขที่ X', is_grade_wide=True, size=3,
            number_range='1-20', win_patterns='horizontal', allow_student_pick=True, created_by=teacher,
        )
        # 3x3: corners={0,2,6,8}->1,3,7,9; center={4}->5; non-special={1:2, 3:4, 5:6, 7:8}
        BingoCard.objects.create(room=room, student=st, card_data=json.dumps([1, 2, 3, 4, 5, 6, 7, 8, 9]), is_locked=True)

        cs = Client()
        cs.force_login(st)
        ct = Client()
        ct.force_login(teacher)

        # หน้าเล่นฝั่งนักเรียนต้องไม่แสดงข้อความ "คุณยังไม่ได้รับสิทธิ์ออกเลขจากครูผู้สอนในขณะนี้"
        r_play = cs.get(reverse('core:student_bingo_play', args=[room.id]))
        self.assertEqual(r_play.status_code, 200)
        h_play = r_play.content.decode('utf-8')
        self.assertNotIn('คุณยังไม่ได้รับสิทธิ์ออกเลขจากครูผู้สอนในขณะนี้', h_play)
        self.assertNotIn('ยังไม่ได้รับสิทธิ์ออกเลขจากครูผู้สอน', h_play)

        # 1. ยังไม่ได้รับสิทธิ์จากครู -> กดเลือกเลข 2 ต้องถูกปฏิเสธ (403) และกดรับสิทธิ์เองก็ 403
        r_denied = cs.post(reverse('core:api_bingo_student_pick', args=[room.id]), {'number': 2})
        self.assertEqual(r_denied.status_code, 403)
        r_acc_denied = cs.post(reverse('core:api_bingo_student_accept', args=[room.id]))
        self.assertEqual(r_acc_denied.status_code, 403)

        # 2. ครูให้สิทธิ์นักเรียนเลขที่ st.class_number ในห้อง 01 -> ต้องกดรับสิทธิ์ก่อน (ยังออกเลขไม่ได้)
        r_grant = ct.post(reverse('core:api_bingo_grant_picker', args=[room.id]), {
            'action': 'grant', 'room_code': room01.code, 'class_number': st.class_number,
        }).json()
        self.assertTrue(r_grant['ok'])
        self.assertGreater(r_grant['valid_pick_count'], 0)

        r_before_accept = cs.post(reverse('core:api_bingo_student_pick', args=[room.id]), {'number': 2})
        self.assertEqual(r_before_accept.status_code, 403)

        r_accept = cs.post(reverse('core:api_bingo_student_accept', args=[room.id])).json()
        self.assertTrue(r_accept['ok'])
        room.refresh_from_db()
        self.assertTrue(room.active_picker_accepted)

        # 3. เลือกมุม (1) ไม่ได้, เลือกเลข 2 ได้ -> หลังเลือก สิทธิ์ถูกเคลียร์อัตโนมัติและต้องขอสิทธิ์ใหม่
        r_corner = cs.post(reverse('core:api_bingo_student_pick', args=[room.id]), {'number': 1}).json()
        self.assertFalse(r_corner['ok'])
        r_ok = cs.post(reverse('core:api_bingo_student_pick', args=[room.id]), {'number': 2}).json()
        self.assertTrue(r_ok['ok'])
        self.assertEqual(r_ok['number'], 2)
        room.refresh_from_db()
        self.assertFalse(room.active_picker_accepted)

        r_again = cs.post(reverse('core:api_bingo_student_pick', args=[room.id]), {'number': 4})
        self.assertEqual(r_again.status_code, 403)

        # 4. เปิดเลข 4, 5, 6, 8 ออกไปแล้ว -> เหลือแค่มุม 1,3,7,9 ไม่มีเลขที่เลือกได้ตามกติกา -> รับสิทธิ์แล้วกดสละสิทธิ์ได้
        for n in (4, 5, 6, 8):
            BingoDrawnNumber.objects.get_or_create(room=room, target_room_code='01', number=n, defaults={'drawn_by_mode': 'normal'})
        r_grant2 = ct.post(reverse('core:api_bingo_grant_picker', args=[room.id]), {
            'action': 'grant', 'room_code': room01.code, 'class_number': st.class_number,
        }).json()
        self.assertEqual(r_grant2['valid_pick_count'], 0)

        r_acc2 = cs.post(reverse('core:api_bingo_student_accept', args=[room.id])).json()
        self.assertTrue(r_acc2['ok'])
        r_forfeit = cs.post(reverse('core:api_bingo_student_forfeit', args=[room.id])).json()
        self.assertTrue(r_forfeit['ok'])
        room.refresh_from_db()
        self.assertIsNone(room.active_picker_student)
        self.assertFalse(room.active_picker_accepted)
        self.assertIn('สละสิทธิ์', room.last_picker_event)

    def test_id_lotto_rename_and_four_filters(self):
        """ID LOTTO พร้อมตัวกรอง 4 เงื่อนไข: ส่งงานกิจกรรม, ไม่เป็น X คนสุดท้าย, ตรวจสอบแล้ว, ได้นำเสนอกิจกรรม"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        room01 = Classroom.objects.get(code='01')
        st = list(User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number')[:3])
        today = timezone.localdate()

        act = LearningActivity.objects.create(title='ใบงานกรอง ID LOTTO', max_score=5.0, created_by=teacher)
        # st[0] และ st[1] ส่งงาน, แต่ st[1] เข้าสาย(-1), st[0] และ st[2] ถูกตรวจสอบสมุดหนังสือ, เฉพาะ st[0] นำเสนอกิจกรรม
        ActivitySubmission.objects.create(activity=act, student=st[0], work_title='ส่ง 0')
        ActivitySubmission.objects.create(activity=act, student=st[1], work_title='ส่ง 1')
        ScoreLog.objects.create(student=st[1], points=-1.0, reason='เข้าสาย 1 คนสุดท้าย', source_module='attendance', awarded_by=teacher)
        StudentMaterialCheck.objects.create(classroom=room01, student=st[0], check_date=today, has_book=True, has_notebook=True)
        StudentMaterialCheck.objects.create(classroom=room01, student=st[2], check_date=today, has_book=True, has_notebook=True)
        ActivityPresentationDraw.objects.create(activity=act, student=st[0], classroom=room01, draw_date=today, is_removed=False)

        sess = LottoSession.objects.create(
            title='ID LOTTO ห้อง 4/1',
            classroom=room01,
            filter_submitted_activity=True,
            filter_activity=act,
            filter_exclude_last_x=True,
            filter_last_x_count=1,
            filter_material_checked=True,
            filter_presented_activity=True,
            filter_check_date=today,
            created_by=teacher,
        )

        ct = Client()
        ct.force_login(teacher)
        r_page = ct.get(reverse('core:teacher_lotto_manage') + f'?session_id={sess.id}')
        self.assertEqual(r_page.status_code, 200)
        h_page = r_page.content.decode('utf-8')
        self.assertIn('ID LOTTO', h_page)
        self.assertNotIn('Debsirin Lotto', h_page)
        for lbl in (
            'เฉพาะคนที่ส่งงานกิจกรรม',
            'เฉพาะคนที่ไม่ได้เป็น',
            'เฉพาะคนที่ตรวจสอบแล้ว',
            'เฉพาะคนที่ได้นำเสนอกิจกรรม',
        ):
            self.assertIn(lbl, h_page)

        draw_res = ct.post(reverse('core:api_lotto_draw', args=[sess.id])).json()
        self.assertTrue(draw_res['ok'])
        self.assertEqual(draw_res['student_id'], (st[0].student_id or '').zfill(5))

    def test_sorting_dropdown_delete_recalculates_score_and_typed_top_x(self):
        """เรียงลำดับ: dropdown กิจกรรมทั้งหมดลบได้พร้อมคำนวณคะแนนใหม่ (บล็อกเมื่อช่วงคะแนนล็อก), และพิมพ์จำนวนคนสูงสุดเอง without type=number"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st = User.objects.get(student_id='99999')
        st.recalculate_total_score()
        base_score = float(st.total_score)

        act = SortingActivity.objects.create(
            title='สอบเรียงลำดับทดสอบลบ', mode='bubble_asc', is_active=True,
            is_exam_mode=True, top_x_reward=5, session_no=1, created_by=teacher,
        )
        SortingScore.objects.create(
            activity=act, student=st, session_no=act.session_no, play_round_no=1,
            score=10.0, cumulative_score=10.0, time_used=8.5, status='completed',
        )

        ct = Client()
        ct.force_login(teacher)

        r_manage = ct.get(reverse('core:teacher_sorting_manage') + f'?activity_id={act.id}')
        r_sum = ct.get(reverse('core:teacher_sorting_summary', args=[act.id]))
        for resp in (r_manage, r_sum):
            self.assertEqual(resp.status_code, 200)
            html = resp.content.decode('utf-8')
            self.assertNotIn('type="number"', html)
            self.assertIn('ให้คะแนน', html)
            self.assertIn('คน สูงสุด', html)
            self.assertIn('name="top_x_reward"', html)
        self.assertIn('sortingAllActivitiesCollapse', r_manage.content.decode('utf-8'))
        self.assertIn('กิจกรรมทั้งหมด', r_manage.content.decode('utf-8'))

        # พิมพ์จำนวนคนสูงสุด = 2 คน แล้วให้คะแนน
        r_reward = ct.post(reverse('core:teacher_sorting_manage') + f'?activity_id={act.id}', {
            'action': 'reward_top_x',
            'activity_id': act.id,
            'top_x_reward': '2',
            'room': 'all',
        }, follow=True)
        self.assertEqual(r_reward.status_code, 200)
        act.refresh_from_db()
        st.refresh_from_db()
        self.assertEqual(act.top_x_reward, 2)
        self.assertGreater(float(st.total_score), base_score)
        linked_logs = ScoreLog.objects.filter(student=st, source_module='sorting', reason__contains=f'[SORT_ACT:{act.id}]')
        self.assertTrue(linked_logs.exists())

        # ทดสอบลบกิจกรรมเมื่ออยู่ในช่วงคะแนนที่ล็อก -> ต้องถูกบล็อก
        final_period = ScorePeriod.objects.create(name='ช่วงล็อกทดสอบ Sorting', order_no=99, max_score=10, is_finalized=True)
        linked_logs.update(score_period=final_period)
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'delete_activity', 'activity_id': act.id,
        })
        self.assertTrue(SortingActivity.objects.filter(pk=act.id).exists())

        # ปลดล็อกช่วงคะแนน แล้วลบกิจกรรม -> ScoreLog ของกิจกรรมหายไปและคะแนนนักเรียนคืนค่าเดิม
        final_period.is_finalized = False
        final_period.save(update_fields=['is_finalized'])
        ct.post(reverse('core:teacher_sorting_manage'), {
            'action': 'delete_activity', 'activity_id': act.id,
        }, follow=True)
        self.assertFalse(SortingActivity.objects.filter(pk=act.id).exists())
        self.assertFalse(ScoreLog.objects.filter(student=st, source_module='sorting', reason__contains=f'[SORT_ACT:{act.id}]').exists())
        st.refresh_from_db()
        self.assertAlmostEqual(float(st.total_score), base_score, places=2)

    def test_bingo_control_back_button_pills_only_scope_colors_and_reset(self):
        """วงล้อ Bingo: ปุ่มกลับหน้าแรก Bingo, ใช้เฉพาะปุ่มห้อง (ไม่มี combobox), แยกสีเขียว(ระดับ)/เหลือง(ห้อง) และรีเซ็ตสถานะรับสิทธิ์"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st = User.objects.get(student_id='99999')
        room = BingoRoom.objects.create(
            title='Bingo ทดสอบสีและปุ่มห้อง', is_grade_wide=True, size=3,
            number_range='1-20', win_patterns='horizontal', allow_student_pick=True,
            active_picker_student=st, active_picker_room_code=st.room_code, active_picker_accepted=True,
            created_by=teacher,
        )
        BingoDrawnNumber.objects.create(room=room, target_room_code='all', number=3, drawn_by_mode='normal')
        BingoDrawnNumber.objects.create(room=room, target_room_code='01', number=7, drawn_by_mode='normal')

        ct = Client()
        ct.force_login(teacher)
        r_ctrl = ct.get(reverse('core:teacher_bingo_control', args=[room.id]) + '?room_filter=01')
        self.assertEqual(r_ctrl.status_code, 200)
        h_ctrl = r_ctrl.content.decode('utf-8')
        self.assertIn(reverse('core:teacher_bingo_manage'), h_ctrl)
        self.assertNotIn('id="roomFilterSelect"', h_ctrl)
        self.assertIn('id="roomFilterPillsBox"', h_ctrl)
        self.assertIn('.num-chip.drawn-all', h_ctrl)
        self.assertIn('.num-chip.drawn-room', h_ctrl)
        self.assertIn('bg-success', h_ctrl)
        self.assertIn('bg-warning', h_ctrl)

        state = ct.get(reverse('core:api_bingo_state', args=[room.id]) + '?room_filter=01').json()
        self.assertTrue(state['ok'])
        self.assertEqual(state['drawn_scope_map'].get('3'), 'all')
        self.assertEqual(state['drawn_scope_map'].get('7'), 'room')
        r01_entry = next((x for x in state['drawn_by_room'] if x['room_code'] == '01'), None)
        self.assertIsNotNone(r01_entry)
        self.assertIn({'number': 3, 'scope': 'all'}, r01_entry['effective_items'])
        self.assertIn({'number': 7, 'scope': 'room'}, r01_entry['effective_items'])

        ct.post(reverse('core:teacher_bingo_control', args=[room.id]), {
            'action': 'reset_drawn', 'reset_scope': 'all', 'room_filter': '01',
        })
        room.refresh_from_db()
        self.assertIsNone(room.active_picker_student)
        self.assertFalse(room.active_picker_accepted)

    def test_student_three_dropdown_sidebar_and_dashboard_lotto_stats(self):
        """เมนูนักเรียน: แบ่ง 3 dropdown (ข้อมูลส่วนตัว, บทเรียน กิจกรรม, กิจกรรมเกม), ไม่มีเมนู ID LOTTO แต่แสดงสถิติ ID LOTTO ในแดชบอร์ด"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st = User.objects.get(student_id='99999')
        lotto_sess = LottoSession.objects.create(title='Lotto ทดสอบสถิติ', created_by=teacher)
        LottoResult.objects.create(session=lotto_sess, student=st, order_index=1, points_awarded=2.0, is_awarded=True)

        cs = Client()
        cs.force_login(st)
        r_dash = cs.get(reverse('core:student_dashboard'))
        self.assertEqual(r_dash.status_code, 200)
        h_dash = r_dash.content.decode('utf-8')
        self.assertIn('id="studentMainNav"', h_dash)
        self.assertIn('id="studentLearnNav"', h_dash)
        self.assertIn('id="studentGamesNav"', h_dash)
        self.assertIn('ข้อมูลส่วนตัว', h_dash)
        self.assertIn('บทเรียน กิจกรรม', h_dash)
        self.assertIn('กิจกรรมเกม', h_dash)
        self.assertNotIn(f'href="{reverse("core:student_lotto")}"', h_dash)
        self.assertIn('สถิติการสุ่ม ID LOTTO ของคุณ', h_dash)
        self.assertIn('1 ครั้ง', h_dash)
        self.assertIn('+2.0', h_dash)

    def test_score_rank_tiers_defaults_boundaries_admin_edit_and_student_scores(self):
        """ระดับตามช่วงคะแนน (% รวม 2 ช่วง): 7 ขั้นมาตรฐาน Member..Crown, แก้ไข/อัปโหลดไอคอนโดย Admin และแสดงผลฝั่งนักเรียน"""
        tiers = ensure_default_score_rank_tiers(reset=True)
        self.assertEqual([t.code for t in tiers], ['member', 'bronze', 'silver', 'gold', 'platinum', 'diamond', 'crown'])

        expected_cases = [
            (0.0, 'member'),
            (20.0, 'member'),
            (20.1, 'bronze'),
            (40.0, 'bronze'),
            (40.1, 'silver'),
            (50.0, 'silver'),
            (50.1, 'gold'),
            (70.0, 'gold'),
            (70.1, 'platinum'),
            (80.0, 'platinum'),
            (80.1, 'diamond'),
            (94.9, 'diamond'),
            (95.0, 'crown'),
            (100.0, 'crown'),
        ]
        for pct, expected_code in expected_cases:
            resolved = resolve_score_rank(pct, tiers)
            self.assertEqual(resolved.code, expected_code, f'percent={pct}')

        # ทดสอบคำนวณ % รวม 2 ช่วงคะแนน (ช่วงละ 50 คะแนน = เต็ม 100 คะแนน, รวมได้ 85 -> 85% -> Diamond)
        st = User.objects.get(student_id='99999')
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        ScoreLog.objects.filter(student=st).delete()
        ScorePeriod.objects.all().delete()
        p1 = ScorePeriod.objects.create(name='กลางภาค (50)', order_no=1, max_score=50.0, score_calc_mode=ScorePeriod.CALC_DIRECT)
        p2 = ScorePeriod.objects.create(name='ปลายภาค (50)', order_no=2, max_score=50.0, score_calc_mode=ScorePeriod.CALC_DIRECT)
        ScoreLog.objects.create(student=st, score_period=p1, points=45.0, reason='คะแนนช่วง 1', source_module='bonus', awarded_by=teacher)
        ScoreLog.objects.create(student=st, score_period=p2, points=40.0, reason='คะแนนช่วง 2', source_module='bonus', awarded_by=teacher)

        overview = compute_student_rank_overview(st)
        self.assertEqual(overview['overall_max'], 100.0)
        self.assertEqual(overview['overall_score'], 85.0)
        self.assertEqual(overview['overall_percent'], 85.0)
        self.assertEqual(overview['overall_rank'].code, 'diamond')

        cs = Client()
        cs.force_login(st)
        r_scores = cs.get(reverse('core:student_scores'))
        self.assertEqual(r_scores.status_code, 200)
        h_scores = r_scores.content.decode('utf-8')
        self.assertIn('Diamond', h_scores)
        self.assertIn('85.0%', h_scores)
        self.assertIn('/static/ranks/trophy_diamond.png', h_scores)
        for t in tiers:
            self.assertTrue(t.icon_url.endswith('.png'))
            png_file = Path(settings.BASE_DIR) / t.icon_url.lstrip('/')
            self.assertTrue(png_file.is_file(), f'Missing trophy icon: {png_file}')

        # ทดสอบ Admin แก้ไขระดับคะแนน + อัปโหลดไอคอน + คืนค่ามาตรฐาน
        admin = User.objects.get(username='admin')
        ca = Client()
        ca.force_login(admin)
        r_get = ca.get(reverse('core:admin_rank_settings'))
        self.assertEqual(r_get.status_code, 200)
        self.assertIn('Member', r_get.content.decode('utf-8'))

        member_tier = ScoreRankTier.objects.get(code='member')
        buf = io.BytesIO()
        Image.new('RGBA', (16, 16), (255, 0, 0, 255)).save(buf, format='PNG')
        upload_png = SimpleUploadedFile('custom_member.png', buf.getvalue(), content_type='image/png')

        post_data = {'action': 'save_ranks', f'custom_icon_{member_tier.id}': upload_png}
        for t in ScoreRankTier.objects.all():
            post_data[f'name_{t.id}'] = 'Newcomer' if t.id == member_tier.id else t.name
            post_data[f'operator_{t.id}'] = t.operator
            post_data[f'min_percent_{t.id}'] = '5' if t.id == member_tier.id else str(t.min_percent)
            post_data[f'color_{t.id}'] = '#123456' if t.id == member_tier.id else t.color_hex
        r_post = ca.post(reverse('core:admin_rank_settings'), post_data, follow=True)
        self.assertEqual(r_post.status_code, 200)
        member_tier.refresh_from_db()
        self.assertEqual(member_tier.name, 'Newcomer')
        self.assertEqual(member_tier.min_percent, 5.0)
        self.assertEqual(member_tier.color_hex, '#123456')
        self.assertTrue(bool(member_tier.custom_icon))

        # คืนค่าระดับเริ่มต้น 7 ระดับ
        r_reset = ca.post(reverse('core:admin_rank_settings'), {'action': 'reset_defaults'}, follow=True)
        self.assertEqual(r_reset.status_code, 200)
        member_tier.refresh_from_db()
        self.assertEqual(member_tier.name, 'Member')
        self.assertEqual(member_tier.min_percent, 0.0)
        self.assertFalse(bool(member_tier.custom_icon))

    def test_student_period_trophy_cards_anonymous_ranked_tabs_and_persistent_prefs(self):
        """ถ้วย 2 ช่วงขนาดใหญ่ + เกณฑ์ 7 ระดับบนสุด + สถิติ 2 Tab เรียงตามคะแนนไม่ระบุชื่อเพื่อน + จดจำการใช้งานข้ามการ Login"""
        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        r01_students = list(
            User.objects.filter(role=User.ROLE_STUDENT, room_code='01').order_by('class_number', 'student_id')[:3]
        )
        self.assertEqual(len(r01_students), 3)
        st_low, st_me, st_high = r01_students
        st_other_r02 = User.objects.filter(role=User.ROLE_STUDENT, room_code='02').first()

        ScoreLog.objects.filter(student__in=[st_low, st_me, st_high]).delete()
        ScorePeriod.objects.all().delete()
        p1 = ScorePeriod.objects.create(name='ช่วง 1', order_no=1, max_score=50.0, score_calc_mode=ScorePeriod.CALC_DIRECT)
        p2 = ScorePeriod.objects.create(name='ช่วง 2', order_no=2, max_score=50.0, score_calc_mode=ScorePeriod.CALC_DIRECT)

        # ช่วง 1: st_high(48.0) > st_me(30.0) > st_low(12.0)
        ScoreLog.objects.create(student=st_high, score_period=p1, points=48.0, reason='p1 high', source_module='bonus', awarded_by=teacher)
        ScoreLog.objects.create(student=st_me, score_period=p1, points=30.0, reason='p1 me', source_module='bonus', awarded_by=teacher)
        ScoreLog.objects.create(student=st_low, score_period=p1, points=12.0, reason='p1 low', source_module='bonus', awarded_by=teacher)

        # ช่วง 2: st_low(44.0) > st_me(22.0) > st_high(8.0)
        ScoreLog.objects.create(student=st_low, score_period=p2, points=44.0, reason='p2 low', source_module='bonus', awarded_by=teacher)
        ScoreLog.objects.create(student=st_me, score_period=p2, points=22.0, reason='p2 me', source_module='bonus', awarded_by=teacher)
        ScoreLog.objects.create(student=st_high, score_period=p2, points=8.0, reason='p2 high', source_module='bonus', awarded_by=teacher)
        for st in (st_low, st_me, st_high):
            st.recalculate_total_score()

        cs = Client()
        cs.force_login(st_me)

        # 1. หน้า /student/scores/ -> เกณฑ์ 7 ระดับอยู่บนสุด (58px), การ์ดถ้วย 2 ช่วง (84px), 2 ถ้วยแถบข้าง (36px), สถิติ 2 Tab ไม่เปิดเผยชื่อเพื่อน
        for url in (
            reverse('core:student_scores'),
            reverse('core:student_scores') + '?room=02',
        ):
            r_sc = cs.get(url)
            self.assertEqual(r_sc.status_code, 200)
            h_sc = r_sc.content.decode('utf-8')
            idx_legend = h_sc.find('เกณฑ์ระดับถ้วยรางวัลทั้ง 7 ระดับ')
            idx_p1_card = h_sc.find('ระดับถ้วยรางวัลประจำช่วงคะแนน')
            self.assertGreater(idx_legend, 0)
            self.assertLess(idx_legend, idx_p1_card)
            self.assertIn('calc(0.76rem + 2px)', h_sc)
            nav_end = h_sc.find('</nav>')
            self.assertGreater(nav_end, 0)
            self.assertNotIn('/static/ranks/trophy_', h_sc[:nav_end])
            self.assertIn('width:58px;height:58px', h_sc)
            self.assertIn('width:84px;height:84px', h_sc)
            self.assertIn('width:20px;height:20px', h_sc)
            self.assertNotIn('<select name="room"', h_sc)
            self.assertNotIn(st_high.get_full_name(), h_sc)
            self.assertNotIn(st_low.get_full_name(), h_sc)
            if st_other_r02:
                self.assertNotIn(st_other_r02.get_full_name(), h_sc)

            # ตรวจการเรียงคะแนนแต่ละ Tab (ช่วง 1: 48.0 -> 30.0 -> 12.0, ช่วง 2: 44.0 -> 22.0 -> 8.0)
            idx_t1 = h_sc.find(f'id="scoresTabPane{p1.id}"')
            idx_t2 = h_sc.find(f'id="scoresTabPane{p2.id}"')
            self.assertGreater(idx_t1, 0)
            self.assertGreater(idx_t2, idx_t1)
            pane1_html = h_sc[idx_t1:idx_t2]
            pane2_html = h_sc[idx_t2:]
            self.assertIn(st_me.get_full_name(), pane1_html)
            self.assertLess(pane1_html.find('48.0 คะแนน'), pane1_html.find('30.0 คะแนน'))
            self.assertLess(pane1_html.find('30.0 คะแนน'), pane1_html.find('12.0 คะแนน'))
            self.assertIn(st_me.get_full_name(), pane2_html)
            self.assertLess(pane2_html.find('44.0 คะแนน'), pane2_html.find('22.0 คะแนน'))
            self.assertLess(pane2_html.find('22.0 คะแนน'), pane2_html.find('8.0 คะแนน'))

        # 2. หน้า /student/dashboard/ -> การ์ดถ้วย 2 ช่วง (72px) + สถิติ 2 Tab ไม่แสดงชื่อเพื่อน
        r_dash = cs.get(reverse('core:student_dashboard'))
        self.assertEqual(r_dash.status_code, 200)
        h_dash = r_dash.content.decode('utf-8')
        self.assertIn('width:72px;height:72px', h_dash)
        self.assertNotIn(st_high.get_full_name(), h_dash)
        self.assertNotIn(st_low.get_full_name(), h_dash)
        self.assertIn(f'id="dashTabPane{p1.id}"', h_dash)
        self.assertIn(f'id="dashTabPane{p2.id}"', h_dash)

        # 3. จดจำการใช้งานข้ามการ Logout -> Login ใหม่ (period tabs, lesson, sorting, sidebar open state)
        lesson1 = LessonContent.objects.create(unit_number=81, title='บทเรียนทดสอบจำ 1', content_html='เนื้อหา 1', is_published=True)
        lesson2 = LessonContent.objects.create(unit_number=82, title='บทเรียนทดสอบจำ 2', content_html='เนื้อหา 2', is_published=True)
        sort1 = SortingActivity.objects.create(title='เรียงลำดับทดสอบ 1', mode='bubble_asc', is_active=True, created_by=teacher)
        sort2 = SortingActivity.objects.create(title='เรียงลำดับทดสอบ 2', mode='bubble_asc', is_active=True, created_by=teacher)
        SortingScore.objects.create(
            activity=sort2, student=st_high, session_no=1, play_round_no=1,
            score=88.88, cumulative_score=88.88, time_used=5.0, status='completed',
        )
        SortingScore.objects.create(
            activity=sort2, student=st_me, session_no=1, play_round_no=1,
            score=33.33, cumulative_score=33.33, time_used=6.0, status='completed',
        )

        # บันทึก preferences via API & GET params
        r_pref = cs.post(reverse('core:api_student_ui_pref'), data=json.dumps({
            'scores_period_id': p2.id,
            'dashboard_period_id': p2.id,
            'lessons_lesson_id': lesson2.id,
            'sorting_activity_id': sort2.id,
            'sidebar_nav_id': 'studentGamesNav',
            'sidebar_is_open': True,
        }), content_type='application/json')
        self.assertEqual(r_pref.status_code, 200)
        self.assertTrue(r_pref.json()['ok'])

        # ออกจากระบบและ Login ใหม่ด้วย Client ใหม่ทั้งหมด
        cs.logout()
        cs2 = Client()
        cs2.force_login(st_me)

        h_sc2 = cs2.get(reverse('core:student_scores')).content.decode('utf-8')
        self.assertIn(f'tab-pane fade show active"\n         id="scoresTabPane{p2.id}"', h_sc2)

        h_dash2 = cs2.get(reverse('core:student_dashboard')).content.decode('utf-8')
        self.assertIn(f'tab-pane fade show active"\n         id="dashTabPane{p2.id}"', h_dash2)
        self.assertIn('class="collapse show" id="studentGamesNav"', h_dash2)

        h_less2 = cs2.get(reverse('core:student_lessons')).content.decode('utf-8')
        less_content = h_less2[h_less2.find('<div class="content-area">'):]
        self.assertLess(less_content.find('บทเรียนทดสอบจำ 2'), less_content.find('บทเรียนทดสอบจำ 1'))

        h_sort2 = cs2.get(reverse('core:student_sorting')).content.decode('utf-8')
        self.assertIn('เรียงลำดับทดสอบ 2', h_sort2)
        self.assertIn('33.33', h_sort2)
        self.assertNotIn('88.88', h_sort2)
        self.assertNotIn(st_high.get_full_name(), h_sort2)

    def test_activity_other_file_upload_and_gdrive_verification_approval_gate(self):
        """กิจกรรมอัปโหลดไฟล์อื่นๆ + เก็บใน Google Drive Folder ต้องผ่านการตรวจสอบจากระบบก่อนอนุมัติใช้งาน"""
        from unittest.mock import patch

        teacher = User.objects.filter(role=User.ROLE_TEACHER).first()
        st = User.objects.get(student_id='99999')
        ct = Client()
        ct.force_login(teacher)
        cs = Client()
        cs.force_login(st)

        # 1. Google Drive Folder เข้าถึงไม่ได้ / ติดหน้า Login -> ต้องไม่อนุมัติให้ใช้งาน
        bad_url = 'https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWx'
        with patch('core.services.verify_gdrive_folder_access', return_value={
            'ok': False,
            'folder_id': '1AbCdEfGhIjKlMnOpQrStUvWx',
            'folder_url': bad_url,
            'folder_name': '',
            'mode': 'shared_folder',
            'message': 'โฟลเดอร์นี้จำกัดสิทธิ์และบังคับเข้าสู่ระบบ',
        }):
            r_bad_api = ct.post(reverse('core:api_activity_verify_gdrive'), {'gdrive_folder_url': bad_url}).json()
            self.assertFalse(r_bad_api['ok'])

        with patch('core.views.verify_gdrive_folder_access', return_value={
            'ok': False,
            'folder_id': '1AbCdEfGhIjKlMnOpQrStUvWx',
            'folder_url': bad_url,
            'folder_name': '',
            'mode': 'shared_folder',
            'message': 'โฟลเดอร์นี้จำกัดสิทธิ์และบังคับเข้าสู่ระบบ',
        }):
            r_create_fail = ct.post(reverse('core:teacher_activity_create'), {
                'title': 'ใบงานส่งไฟล์ Python',
                'activity_type': LearningActivity.TYPE_OTHER_FILE,
                'academic_year': '2569',
                'allowed_file_extensions': '.py,.pdf',
                'storage_destination': LearningActivity.STORAGE_GDRIVE,
                'gdrive_folder_url': bad_url,
                'max_score': '5',
                'is_active': '1',
                'target_rooms_present': '1',
                'target_all_rooms': '1',
                'ans_form_present': '1',
                'tiers_form_present': '1',
            }, follow=True)
            self.assertEqual(r_create_fail.status_code, 200)
            act = LearningActivity.objects.get(title='ใบงานส่งไฟล์ Python')
            self.assertFalse(act.gdrive_verified)
            self.assertEqual(act.storage_destination, LearningActivity.STORAGE_LOCAL)

        # 2. Google Drive Folder ตรวจสอบผ่านจริง -> อนุมัติให้ใช้งานได้
        class FakeHttpResp:
            status = 200
            def __init__(self, final_url, body_bytes):
                self._url = final_url
                self._body = body_bytes
            def geturl(self):
                return self._url
            def read(self):
                return self._body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        good_html = b'<html><head><title>CS Works 2569 - Google Drive</title></head><body>Folder OK</body></html>'
        with patch('core.services.urllib.request.urlopen', return_value=FakeHttpResp(bad_url, good_html)):
            r_ok_api = ct.post(reverse('core:api_activity_verify_gdrive'), {'gdrive_folder_url': bad_url}).json()
            self.assertTrue(r_ok_api['ok'])
            self.assertEqual(r_ok_api['folder_name'], 'CS Works 2569')

            r_edit_ok = ct.post(reverse('core:teacher_activity_edit', args=[act.id]), {
                'title': 'ใบงานส่งไฟล์ Python',
                'activity_type': LearningActivity.TYPE_OTHER_FILE,
                'academic_year': '2569',
                'allowed_file_extensions': '.py,.pdf',
                'storage_destination': LearningActivity.STORAGE_GDRIVE,
                'gdrive_folder_url': bad_url,
                'max_score': '5',
                'is_active': '1',
                'target_rooms_present': '1',
                'target_all_rooms': '1',
                'ans_form_present': '1',
                'tiers_form_present': '1',
            }, follow=True)
            self.assertEqual(r_edit_ok.status_code, 200)
            act.refresh_from_db()
            self.assertTrue(act.gdrive_verified)
            self.assertEqual(act.storage_destination, LearningActivity.STORAGE_GDRIVE)
            self.assertEqual(act.gdrive_folder_id, '1AbCdEfGhIjKlMnOpQrStUvWx')

        # 3. นักเรียนอัปโหลดไฟล์ต้องห้าม (.exe) -> ปฏิเสธ, อัปโหลดไฟล์ .py -> สำเร็จและแสดงดาวน์โหลด + Google Drive
        bad_file = SimpleUploadedFile('malware.exe', b'MZ', content_type='application/octet-stream')
        r_bad_ext = cs.post(reverse('core:student_activities'), {
            'action': 'submit_work', 'activity_id': act.id, 'work_title': 'ลองส่งผิดสกุล', 'work_file': bad_file,
        }, follow=True)
        self.assertIn('ประเภทไฟล์ไม่ถูกต้อง', r_bad_ext.content.decode('utf-8'))
        self.assertFalse(ActivitySubmission.objects.filter(activity=act, student=st).exists())

        py_file = SimpleUploadedFile('homework.py', b'print("Hello DEBSIRIN")\n', content_type='text/x-python')
        r_ok_sub = cs.post(reverse('core:student_activities'), {
            'action': 'submit_work', 'activity_id': act.id, 'work_title': 'โค้ด Python', 'work_file': py_file,
        }, follow=True)
        self.assertEqual(r_ok_sub.status_code, 200)
        sub = ActivitySubmission.objects.get(activity=act, student=st)
        self.assertEqual(sub.work_file_name, 'homework.py')
        self.assertGreater(sub.work_file_size, 0)
        self.assertIn('1AbCdEfGhIjKlMnOpQrStUvWx', sub.gdrive_file_url)
        self.assertFalse(sub.is_image_file)

        h_st = cs.get(reverse('core:student_activities') + f'?activity_id={act.id}').content.decode('utf-8')
        self.assertIn('homework.py', h_st)
        self.assertIn('เก็บใน Google Drive', h_st)
        self.assertIn('Google Drive', h_st)
        self.assertNotIn('homework.py" class="rounded', h_st)

        h_sub = ct.get(reverse('core:teacher_submissions') + f'?activity_id={act.id}').content.decode('utf-8')
        self.assertIn('homework.py', h_sub)
        self.assertIn('Drive', h_sub)

        from .services import delete_activity_files_for_year
        delete_activity_files_for_year(file_target=f'sub:{sub.id}')

    def test_academic_year_settings_and_admin_data_files_password_protected_delete(self):
        """ตั้งค่าปีการศึกษา + เมนูไฟล์ข้อมูล แยกเก็บทั้งปี + ลบไฟล์/ลบทั้งปีได้เมื่อยืนยันด้วยรหัสผ่าน Admin ล่าสุด + ลบโฟลเดอร์อ้างอิงแล้ว"""
        from .services import delete_activity_files_for_year

        self.assertFalse((Path(settings.BASE_DIR) / 'อ้างอิง').exists())

        admin = User.objects.get(username='admin')
        st = User.objects.get(student_id='99999')
        room01 = Classroom.objects.get(code='01')
        ca = Client()
        ca.force_login(admin)

        # เมนูตั้งค่าระบบต้องมี "ไฟล์ข้อมูล" และไม่อนุญาตให้เปลี่ยนเป็นปีที่ยังไม่ได้กดสร้างใน oauth_settings
        r_menu = ca.get(reverse('core:admin_data_files'))
        self.assertEqual(r_menu.status_code, 200)
        h_menu = r_menu.content.decode('utf-8')
        self.assertIn('ไฟล์ข้อมูล', h_menu)
        self.assertIn(reverse('core:admin_data_files'), h_menu)
        self.assertIn('รหัสผ่าน Admin ล่าสุด', h_menu)

        r_uncreated = ca.post(reverse('core:admin_oauth_settings'), {
            'action': 'save_all',
            'allowed_oauth_domain': 'debsirin.ac.th',
            'ai_provider': 'gemini',
            'current_academic_year': '2570',
        }, follow=True)
        self.assertEqual(SystemSetting.get().current_academic_year, '2569')
        self.assertIn('ขึ้นปีการศึกษาใหม่', r_uncreated.content.decode('utf-8'))

        # สร้างข้อมูลปีการศึกษา 2568 (ทดสอบตอนสรุปล็อกแล้วด้วย) และ 2569 เพื่อทดสอบแยกปี
        p_2568 = ScorePeriod.objects.create(name='เทอมปี 2568', academic_year='2568', order_no=50, max_score=20, is_finalized=False)
        p_2569 = ScorePeriod.objects.create(name='เทอมปี 2569', academic_year='2569', order_no=51, max_score=20, is_finalized=False)
        act_68 = LearningActivity.objects.create(
            title='ใบงานปี 2568', academic_year='2568', score_period=p_2568, max_score=5.0, created_by=admin,
            worksheet_pdf=SimpleUploadedFile('ws68.pdf', b'%PDF-1.4\n%68\n', content_type='application/pdf'),
        )
        act_69 = LearningActivity.objects.create(
            title='ใบงานปี 2569', academic_year='2569', score_period=p_2569, max_score=5.0, created_by=admin,
            worksheet_pdf=SimpleUploadedFile('ws69.pdf', b'%PDF-1.4\n%69\n', content_type='application/pdf'),
        )
        sub_68 = ActivitySubmission.objects.create(
            activity=act_68, student=st, academic_year='2568', work_title='งานปี 68',
            work_file=SimpleUploadedFile('sub68.txt', b'work68', content_type='text/plain'),
            work_file_name='sub68.txt', work_file_size=6,
        )
        sub_69 = ActivitySubmission.objects.create(
            activity=act_69, student=st, academic_year='2569', work_title='งานปี 69',
            work_file=SimpleUploadedFile('sub69.txt', b'work69', content_type='text/plain'),
            work_file_name='sub69.txt', work_file_size=6,
        )
        ScoreLog.objects.create(student=st, score_period=p_2568, academic_year='2568', points=5.0, reason='คะแนนปี 68', source_module='activity', awarded_by=admin)
        p_2568.is_finalized = True
        p_2568.save(update_fields=['is_finalized'])
        ScoreLog.objects.create(student=st, score_period=p_2569, academic_year='2569', points=4.0, reason='คะแนนปี 69', source_module='activity', awarded_by=admin)
        AttendanceSession.objects.create(classroom=room01, title='เช็คชื่อปี 68', academic_year='2568', check_in_code='680001', created_by=admin)
        SortingActivity.objects.create(title='Sorting 68', academic_year='2568', created_by=admin)
        BingoRoom.objects.create(title='Bingo 68', academic_year='2568', created_by=admin)
        LottoSession.objects.create(title='Lotto 68', academic_year='2568', created_by=admin)

        # เปลี่ยนรหัสผ่าน Admin เป็นรหัสล่าสุดใหม่
        admin.set_password('NewLatestPass#2569!')
        admin.save()
        ca.force_login(admin)

        # 1. ใส่รหัสผ่านเก่า/ผิด -> ต้องถูกปฏิเสธ ข้อมูลและไฟล์ปี 2568 ยังอยู่ครบ
        r_wrong = ca.post(reverse('core:admin_data_files'), {
            'action': 'delete_year_files',
            'target_year': '2568',
            'admin_password': 'wrong_or_old_password',
        }, follow=True)
        self.assertIn('รหัสผ่าน Admin ไม่ถูกต้อง', r_wrong.content.decode('utf-8'))
        sub_68.refresh_from_db()
        self.assertTrue(bool(sub_68.work_file))

        # 2. ลบไฟล์เฉพาะปี 2568 ด้วยรหัสผ่านล่าสุด -> ไฟล์ปี 2568 หาย แต่ไฟล์ปี 2569 ยังอยู่
        ca.post(reverse('core:admin_data_files'), {
            'action': 'delete_year_files',
            'target_year': '2568',
            'admin_password': 'NewLatestPass#2569!',
        }, follow=True)
        sub_68.refresh_from_db()
        act_68.refresh_from_db()
        sub_69.refresh_from_db()
        act_69.refresh_from_db()
        self.assertFalse(bool(sub_68.work_file))
        self.assertFalse(bool(act_68.worksheet_pdf))
        self.assertTrue(bool(sub_69.work_file))
        self.assertTrue(bool(act_69.worksheet_pdf))

        # 3. ลบข้อมูลทั้งปีการศึกษา 2568 ด้วยรหัสผ่านล่าสุด -> ลบครบทุกโมดูลของปี 2568 โดยคงปี 2569 และนักเรียนไว้
        ca.post(reverse('core:admin_data_files'), {
            'action': 'delete_year_data',
            'target_year': '2568',
            'confirm_delete_year': '1',
            'admin_password': 'NewLatestPass#2569!',
        }, follow=True)
        self.assertFalse(LearningActivity.objects.filter(academic_year='2568').exists())
        self.assertFalse(ScorePeriod.objects.filter(academic_year='2568').exists())
        self.assertFalse(ScoreLog.objects.filter(academic_year='2568').exists())
        self.assertFalse(AttendanceSession.objects.filter(academic_year='2568').exists())
        self.assertFalse(SortingActivity.objects.filter(academic_year='2568').exists())
        self.assertFalse(BingoRoom.objects.filter(academic_year='2568').exists())
        self.assertFalse(LottoSession.objects.filter(academic_year='2568').exists())

        self.assertTrue(LearningActivity.objects.filter(pk=act_69.id).exists())
        self.assertTrue(ScorePeriod.objects.filter(pk=p_2569.id).exists())
        self.assertTrue(User.objects.filter(pk=st.id).exists())

        # ทำความสะอาดไฟล์ทดสอบปี 2569
        delete_activity_files_for_year('2569')

    def test_expanded_system_settings_theme_colors_and_general_options(self):
        """ตั้งค่าระบบ: ปรับแต่งข้อมูลทั่วไป ขนาดหัวข้อเมนูข้าง เมนูเกมหลัก และสีธีมทุกส่วน (ผู้ดูแล/นักเรียน/ผังที่นั่ง/กลุ่ม/Bingo) พร้อมรีเซ็ตมาตรฐาน"""
        admin = User.objects.get(username='admin')
        st99 = User.objects.get(student_id='99999', academic_year='2569')
        ca = Client()
        ca.force_login(admin)

        r_page = ca.get(reverse('core:admin_oauth_settings'))
        self.assertEqual(r_page.status_code, 200)
        page_html = r_page.content.decode('utf-8')
        self.assertIn('theme_seat_online_bg_1', page_html)
        self.assertIn('theme_group_activity_bg', page_html)
        self.assertIn('theme_bingo_all_color', page_html)
        self.assertIn('sidebar_heading_size_px', page_html)

        resp = ca.post(reverse('core:admin_oauth_settings'), {
            'action': 'save_all',
            'site_title': 'COMSC4 PRO',
            'site_subtitle': 'วิทยาการคำนวณ ม.4 เทพศิรินทร์',
            'sidebar_heading_size_px': '16',
            'last_activity_max_score': '10',
            'game_toggles_present': '1',
            'sorting_menu_open': '1',
            'bingo_menu_open': '0',
            'lotto_menu_open': '1',
            'allowed_oauth_domain': 'debsirin.ac.th',
            'ai_provider': 'gemini',
            'theme_teacher_card_bg': '#f8fafc',
            'theme_teacher_topbar_text': '#111827',
            'theme_seat_default_bg': '#f3f4f6',
            'theme_seat_online_bg_1': '#86efac',
            'theme_seat_online_bg_2': '#22c55e',
            'theme_seat_online_border': '#14532d',
            'theme_group_activity_bg': '#fef3c7',
            'theme_group_activity_border': '#f59e0b',
            'theme_bingo_all_color': '#059669',
            'theme_bingo_room_color': '#d97706',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        setting = SystemSetting.get()
        self.assertEqual(setting.site_title, 'COMSC4 PRO')
        self.assertEqual(setting.sidebar_heading_size_px, 16)
        self.assertEqual(setting.last_activity_max_score, 10.0)
        self.assertTrue(setting.sorting_menu_open)
        self.assertFalse(setting.bingo_menu_open)
        self.assertTrue(setting.lotto_menu_open)

        dash_html = ca.get(reverse('core:teacher_dashboard')).content.decode('utf-8')
        self.assertIn('COMSC4 PRO', dash_html)
        self.assertIn('font-size: 16px;', dash_html)
        self.assertIn('--seat-online-bg-1: #86efac', dash_html)
        self.assertIn('--group-activity-bg: #fef3c7', dash_html)
        self.assertIn('--bingo-all-color: #059669', dash_html)
        self.assertIn('--card-bg: #f8fafc', dash_html)

        att_html = ca.get(reverse('core:teacher_attendance')).content.decode('utf-8')
        self.assertIn('var(--seat-online-bg-1', att_html)
        ed_html = ca.get(reverse('core:teacher_activity_create')).content.decode('utf-8')
        self.assertIn('var(--group-activity-bg', ed_html)

        # เมนู Bingo ถูกปิดไว้ -> นักเรียนเข้าหน้า Bingo จะถูกพากลับ dashboard พร้อมเตือน
        cs = Client()
        cs.force_login(st99)
        r_bingo = cs.get(reverse('core:student_bingo_list'))
        self.assertEqual(r_bingo.status_code, 302)

        # รีเซ็ตสีกลับเป็นมาตรฐาน
        ca.post(reverse('core:admin_oauth_settings'), {'action': 'reset_theme_colors'})
        self.assertEqual(SystemSetting.get().theme_colors_json, '{}')

    def test_new_academic_year_create_copy_lessons_activities_duplicate_student_id_and_reset_games(self):
        """ขึ้นปีการศึกษาใหม่: ต้องกดสร้าง, รีเซ็ตเกมทั้งหมด, เลือกคัดลอกบทเรียน/กิจกรรมจากปีเก่า (ค่าเริ่มต้น=ทั้งหมด), และอัปโหลดนักเรียนรหัสซ้ำกับปีเก่าได้"""
        from .services import delete_activity_files_for_year

        admin = User.objects.get(username='admin')
        room01 = Classroom.objects.get(code='01')
        period_69 = ScorePeriod.objects.filter(academic_year='2569').first()
        st99_old = User.objects.get(role=User.ROLE_STUDENT, academic_year='2569', student_id='99999')
        st_51443_old = User.objects.filter(role=User.ROLE_STUDENT, academic_year='2569', student_id='51443').first()
        if not st_51443_old:
            st_51443_old = User.objects.create_user(
                username='s51443', student_id='51443', academic_year='2569',
                first_name='สรวิชญ์', last_name='เก่า', classroom=room01, room_code='01',
                class_number=1, role=User.ROLE_STUDENT, password='51443',
            )

        # สร้างบทเรียน กิจกรรม ผลงาน และข้อมูลเกมทั้ง 3 เกมในปึ 2569
        lesson_69 = LessonContent.objects.create(
            academic_year='2569', unit_number=9, title='บทเรียนปี 2569 สำหรับคัดลอก',
            content_html='<p>เนื้อหาปี 2569</p>', created_by=admin,
            attachment=SimpleUploadedFile('les69.txt', b'lesson-2569-data', content_type='text/plain'),
        )
        act_69 = LearningActivity.objects.create(
            academic_year='2569', title='ใบงานคัดลอกปี 2569', description='รายละเอียด',
            score_period=period_69, max_score=10.0, created_by=admin,
            worksheet_pdf=SimpleUploadedFile('ws69_copy.pdf', b'%PDF-1.4\n%copy\n', content_type='application/pdf'),
        )
        ActivityAnswerKeyItem.objects.create(
            activity=act_69, order_no=1, title='เฉลยข้อ 1', content_html='<p>คำตอบ</p>', is_revealed=True,
        )
        ActivityGradingTier.objects.create(activity=act_69, round_no=1, score=10.0, label='รอบ 1')
        ActivitySubmission.objects.create(
            activity=act_69, student=st99_old, academic_year='2569', work_title='งานเก่าของ 99999',
        )
        ScoreLog.objects.create(
            student=st_51443_old, score_period=period_69, academic_year='2569',
            points=8.0, reason='คะแนนเก่าปี 69', source_module='manual', awarded_by=admin,
        )

        # สร้างข้อมูลเกมปี 2569
        sort_69 = SortingActivity.objects.create(title='Sort 2569', academic_year='2569', is_active=True, is_exam_mode=True, created_by=admin)
        SortingScore.objects.create(activity=sort_69, student=st99_old, status='completed', score=95.0, cumulative_score=95.0)
        SortingExamRecord.objects.create(activity=sort_69, title='สอบ 69', room_label='ทุกห้อง', participant_count=1)
        bingo_69 = BingoRoom.objects.create(title='Bingo 2569', academic_year='2569', status=BingoRoom.STATUS_PLAYING, created_by=admin)
        BingoWinner.objects.create(room=bingo_69, student=st99_old, room_code='01', win_pattern='แนวนอน')
        lotto_69 = LottoSession.objects.create(title='Lotto 2569', academic_year='2569', is_open=True, created_by=admin)
        LottoResult.objects.create(session=lotto_69, student=st99_old, order_index=1)

        ca = Client()
        ca.force_login(admin)

        # 1. กดสร้างปีการศึกษาใหม่ 2570
        r_create = ca.post(reverse('core:admin_data_files'), {
            'action': 'create_academic_year',
            'new_year': '2570',
            'source_year': '2569',
            'title': 'ปีการศึกษา 2570 ทดสอบ',
        }, follow=True)
        self.assertEqual(r_create.status_code, 200)
        self.assertEqual(SystemSetting.get().current_academic_year, '2570')
        self.assertTrue(AcademicYear.objects.filter(year='2570', is_current=True).exists())
        self.assertTrue(AcademicYear.objects.filter(year='2569', is_current=False).exists())

        # ตรวจการรีเซ็ตเกมทั้งหมดเมื่อเริ่มปีการศึกษาใหม่
        sort_69.refresh_from_db()
        bingo_69.refresh_from_db()
        lotto_69.refresh_from_db()
        self.assertFalse(sort_69.is_active)
        self.assertFalse(sort_69.is_exam_mode)
        self.assertEqual(bingo_69.status, BingoRoom.STATUS_CLOSED)
        self.assertFalse(lotto_69.is_open)
        self.assertEqual(SortingActivity.objects.filter(academic_year='2570').count(), 1)
        self.assertEqual(SortingScore.objects.filter(activity__academic_year='2570').count(), 0)
        self.assertEqual(SortingExamRecord.objects.filter(activity__academic_year='2570').count(), 0)
        self.assertEqual(BingoRoom.objects.filter(academic_year='2570').count(), 0)
        self.assertEqual(LottoSession.objects.filter(academic_year='2570').count(), 0)

        # ช่วงคะแนนใหม่ของปี 2570 ต้องถูกสร้างพร้อมใช้งาน (ยังไม่ถูกล็อก) และนักเรียนปีเก่าถูกพักสถานะ
        self.assertGreaterEqual(ScorePeriod.objects.filter(academic_year='2570', is_finalized=False).count(), 1)
        st99_old.refresh_from_db()
        self.assertFalse(st99_old.is_active)
        self.assertEqual(User.objects.filter(role=User.ROLE_STUDENT, academic_year='2570', is_active=True).count(), 0)

        # 2. หน้าคัดลอกบทเรียน/กิจกรรมต้องติ๊กเลือกทั้งหมดเป็นค่าเริ่มต้น
        copy_html = r_create.content.decode('utf-8')
        self.assertIn('id="chkAllLessons" checked', copy_html)
        self.assertIn('id="chkAllActivities" checked', copy_html)
        self.assertRegex(copy_html, rf'name="lesson_ids"\s+value="{lesson_69.id}"[^>]*checked')
        self.assertRegex(copy_html, rf'name="activity_ids"\s+value="{act_69.id}"[^>]*checked')

        # 3. ยืนยันคัดลอกบทเรียน & กิจกรรมที่เลือกเข้าสู่ปี 2570
        ca.post(reverse('core:admin_data_files'), {
            'action': 'copy_from_year',
            'source_year': '2569',
            'target_year': '2570',
            'lesson_ids': [str(lesson_69.id)],
            'activity_ids': [str(act_69.id)],
        }, follow=True)
        lesson_70 = LessonContent.objects.filter(academic_year='2570', copied_from=lesson_69).first()
        act_70 = LearningActivity.objects.filter(academic_year='2570', copied_from=act_69).first()
        self.assertIsNotNone(lesson_70)
        self.assertIsNotNone(act_70)
        self.assertEqual(act_70.score_period.academic_year, '2570')
        self.assertEqual(act_70.answer_key_items.count(), 1)
        self.assertFalse(act_70.answer_key_items.first().is_revealed)
        self.assertEqual(act_70.grading_tiers.count(), 1)
        self.assertEqual(ActivitySubmission.objects.filter(activity=act_70).count(), 0)
        self.assertNotEqual(lesson_70.attachment.name, lesson_69.attachment.name)
        self.assertNotEqual(act_70.worksheet_pdf.name, act_69.worksheet_pdf.name)

        # 4. อัปโหลดรายชื่อนักเรียนใหม่ในปี 2570 ที่มีเลขประจำตัวซ้ำกับปี 2569 (51443 และ 99999)
        csv_content = (
            'ชั้น,ห้อง,เลขที่,เลขประจำตัว,ชื่อ-นามสกุล\n'
            '4,1,1,51443,นายสรวิชญ์ ใหม่ปี70\n'
            '4,1,2,99999,นายสมชาย ปี70\n'
        ).encode('utf-8')
        up_csv = SimpleUploadedFile('students_2570.csv', csv_content, content_type='text/csv')
        prev = preview_students(up_csv, academic_year='2570')
        self.assertEqual(prev['valid_count'], 2)
        self.assertTrue(all(r['from_prev_year'] for r in prev['rows']))
        self.assertTrue(all(not r['is_update'] for r in prev['rows']))

        imp_res = import_students_from_preview(prev, academic_year='2570')
        self.assertEqual(imp_res['created'], 2)
        self.assertEqual(imp_res['errors'], [])

        st_51443_new = User.objects.get(role=User.ROLE_STUDENT, academic_year='2570', student_id='51443')
        st99_new = User.objects.get(role=User.ROLE_STUDENT, academic_year='2570', student_id='99999')
        self.assertNotEqual(st_51443_new.id, st_51443_old.id)
        self.assertEqual(st_51443_new.total_score, 0.0)
        st_51443_old.refresh_from_db()
        self.assertGreater(st_51443_old.total_score, 0.0)

        # นักเรียน 51443 ล็อกอินด้วย Google OAuth ในปี 2570 ต้องเข้าสู่บัญชีปี 2570
        cs_new = Client()
        r_oauth = cs_new.post(reverse('core:google_oauth_login'), {'google_email': 's51443@debsirin.ac.th'}, follow=True)
        self.assertEqual(r_oauth.status_code, 200)
        self.assertEqual(int(cs_new.session['_auth_user_id']), st_51443_new.id)

        # ทำความสะอาดไฟล์และสลับกลับปี 2569 ท้ายเทสต์
        delete_activity_files_for_year('2569')
        delete_activity_files_for_year('2570')









