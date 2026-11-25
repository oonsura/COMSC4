"""
Models ของระบบ COMSC4 (ครบทุกส่วนงานและตารางตามสเปกข้อ 5)
- User (รองรับ 3 บทบาท: admin, teacher, student)
- UserProfile (ตาราง user_profiles: user_id, profile_image_url, updated_at)
- UserSession (ตาราง user_sessions: user_id, session_token, device_info, created_at, last_active)
- SystemSetting (ตั้งค่า Google OAuth @debsirin.ac.th, 1-Session limit, AI API Keys, การเปิด/ปิดเมนูเกมหลัก)
- Classroom (ห้องเรียน 01-14 และแผนผังที่นั่ง)
- LessonContent & AIQuiz & AIQuizQuestion & AIQuizAttempt (เนื้อหาและแบบทดสอบ AI)
- LearningActivity & ActivitySubmission (กิจกรรมการเรียนรู้และสุ่มนำเสนอผลงานพร้อมรูปประจำตัว)
- AttendanceSession & AttendanceRecord & StudentMaterialCheck (เช็คชื่อด้วยโค้ด + ตรวจ IP + แผนผังที่นั่ง + เช็คหนังสือ/สมุด)
- ScoreRule & ScoreLog (กฎคะแนนและประวัติการบวก/ลบคะแนนอัตโนมัติ)
- SortingActivity & SortingScore (ตาราง sorting_activities, sorting_scores สำหรับ dsbasicsorting)
- BingoRoom, BingoCard, BingoDrawnNumber, BingoWinner (ตาราง bingo_rooms, bingo_cards, bingo_drawn_numbers, bingo_winners)
- LottoSession, LottoResult (ตาราง lotto_sessions, lotto_results สำหรับ Debsirin Lotto)
"""
import json
import re
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Max, Q, Sum
from django.utils import timezone


STUDENT_EMAIL_REGEX = re.compile(r'^s(\d{5})@([a-zA-Z0-9.-]+)$', re.IGNORECASE)


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, username, password=None, **extra_fields):
        if not username:
            raise ValueError('ต้องระบุ username หรือรหัสประจำตัว')
        username = str(username).strip()
        user = self.model(username=username, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault('role', User.ROLE_ADMIN)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(username, password or settings.DEFAULT_ADMIN_PASSWORD, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    ROLE_ADMIN = 'admin'
    ROLE_TEACHER = 'teacher'
    ROLE_STUDENT = 'student'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin (ผู้ดูแลระบบสูงสุด)'),
        (ROLE_TEACHER, 'ผู้ดูแล / ครูผู้สอน'),
        (ROLE_STUDENT, 'นักเรียน'),
    ]

    username = models.CharField('ชื่อผู้ใช้ / รหัสอ้างอิง', max_length=100, unique=True)
    student_id = models.CharField('เลขประจำตัวนักเรียน (5 หลัก)', max_length=15, null=True, blank=True, db_index=True)
    email = models.EmailField('อีเมลโรงเรียน (@debsirin.ac.th)', max_length=150, blank=True, db_index=True)
    title_prefix = models.CharField('คำนำหน้า', max_length=30, blank=True)
    first_name = models.CharField('ชื่อ', max_length=150, blank=True)
    last_name = models.CharField('นามสกุล', max_length=150, blank=True)
    classroom = models.ForeignKey(
        'Classroom', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='students', verbose_name='ชั้น/ห้อง'
    )
    room_code = models.CharField('รหัสห้อง (เช่น 01-14)', max_length=20, blank=True, db_index=True)
    class_number = models.PositiveIntegerField('เลขที่', default=0, db_index=True)
    group_name = models.CharField('กลุ่มเรียน/กลุ่มย่อย', max_length=50, blank=True, default='A')
    role = models.CharField('บทบาท', max_length=15, choices=ROLE_CHOICES, default=ROLE_STUDENT, db_index=True)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, blank=True, default='2569', db_index=True)
    profile_photo = models.ImageField('รูปประจำตัว', upload_to='profiles/', null=True, blank=True)
    total_score = models.FloatField('คะแนนรวม', default=0.0, db_index=True)
    allow_google_oauth = models.BooleanField('อนุญาตล็อกอินด้วย Google OAuth', default=True)
    allow_password_login = models.BooleanField('อนุญาตล็อกอินด้วยรหัสผ่าน (กรณีพิเศษ)', default=False)
    current_session_key = models.CharField('Session ปัจจุบัน', max_length=100, blank=True)
    current_device_info = models.CharField('อุปกรณ์ปัจจุบัน', max_length=255, blank=True)
    is_active = models.BooleanField('เปิดใช้งาน', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = 'ผู้ใช้'
        verbose_name_plural = 'ผู้ใช้'
        ordering = ['room_code', 'class_number', 'student_id', 'username']
        constraints = [
            models.UniqueConstraint(
                fields=['student_id', 'academic_year'],
                condition=Q(role='student', student_id__isnull=False) & ~Q(student_id=''),
                name='uniq_student_id_per_academic_year',
            ),
        ]

    def __str__(self):
        if self.role == self.ROLE_STUDENT and self.student_id:
            return f'{self.student_id} {self.get_full_name()} ({self.room_display})'
        return f'{self.username} ({self.get_role_display()})'

    def get_full_name(self):
        parts = [self.title_prefix, self.first_name, self.last_name]
        name = ' '.join(p.strip() for p in parts if p and p.strip())
        return name or self.username

    def get_short_name(self):
        return self.first_name or self.username

    @property
    def room_display(self):
        if self.classroom:
            return self.classroom.name
        if self.room_code:
            return f'ม.4/{int(self.room_code)}' if self.room_code.isdigit() else self.room_code
        return '-'

    @property
    def is_staff(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_TEACHER) and self.is_active

    @property
    def is_admin_role(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_teacher_role(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_TEACHER)

    @property
    def is_student_role(self):
        return self.role == self.ROLE_STUDENT

    @property
    def avatar_url(self):
        if self.profile_photo:
            try:
                return self.profile_photo.url
            except Exception:
                pass
        if hasattr(self, 'profile_record') and self.profile_record.profile_image_url:
            return self.profile_record.profile_image_url
        return ''

    @property
    def avatar_initial(self):
        if self.first_name:
            return self.first_name[0].upper()
        if self.username:
            return self.username[0].upper()
        return 'S'

    def recalculate_total_score(self):
        per_period_raw = {}
        period_ids = set()
        for pid, src, pts in self.score_logs.values_list('score_period_id', 'source_module', 'points'):
            val = float(pts or 0.0)
            bucket = per_period_raw.setdefault(pid, {'activity': 0.0, 'other': 0.0})
            if src == 'activity':
                bucket['activity'] += val
            else:
                bucket['other'] += val
            if pid:
                period_ids.add(pid)

        period_map = (
            {p.id: p for p in ScorePeriod.objects.filter(pk__in=period_ids)}
            if period_ids else {}
        )
        prop_pids = [pid for pid, p in period_map.items() if p.score_calc_mode == ScorePeriod.CALC_PROPORTIONAL]
        act_max_map = {}
        if prop_pids:
            for row in (
                LearningActivity.objects.filter(score_period_id__in=prop_pids)
                .values('score_period_id')
                .annotate(total_max=Sum('max_score'))
            ):
                act_max_map[row['score_period_id']] = float(row['total_max'] or 0.0)

        new_total = 0.0
        for pid, b in per_period_raw.items():
            act_pts = b['activity']
            if pid and pid in period_map and period_map[pid].score_calc_mode == ScorePeriod.CALC_PROPORTIONAL:
                raw_max_sum = act_max_map.get(pid, 0.0)
                if raw_max_sum > 0:
                    eff_act = round(act_pts * float(period_map[pid].max_score or 0.0) / raw_max_sum, 2)
                else:
                    eff_act = act_pts
            else:
                eff_act = act_pts
            new_total += eff_act + b['other']

        new_total = round(new_total, 2)
        if self.total_score != new_total:
            self.total_score = new_total
            User.objects.filter(pk=self.pk).update(total_score=self.total_score)
        return self.total_score

    def save(self, *args, **kwargs):
        if self.role in (self.ROLE_ADMIN, self.ROLE_TEACHER):
            self.academic_year = ''
        else:
            if not (self.academic_year or '').strip():
                self.academic_year = (
                    SystemSetting.objects.values_list('current_academic_year', flat=True).first() or '2569'
                )
            if self.student_id and not self.email:
                self.email = f's{self.student_id}@debsirin.ac.th'
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    """ตาราง user_profiles ตามข้อ 5 ในสเปก"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile_record')
    profile_image_url = models.CharField('URL รูปประจำตัว', max_length=500, blank=True)
    bio = models.CharField('ข้อความแนะนำตัว', max_length=255, blank=True)
    phone = models.CharField('เบอร์ติดต่อ', max_length=30, blank=True)
    ui_preferences_json = models.TextField('การตั้งค่าการใช้งานล่าสุด (JSON)', blank=True, default='{}')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_profiles'
        verbose_name = 'โปรไฟล์ผู้ใช้'
        verbose_name_plural = 'โปรไฟล์ผู้ใช้'

    def __str__(self):
        return f'Profile of {self.user.username}'

    def get_ui_preferences(self):
        raw = self.ui_preferences_json or '{}'
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        sidebar_open = data.get('sidebar_open')
        if not isinstance(sidebar_open, dict):
            data['sidebar_open'] = {}
        return data

    def update_ui_preferences(self, updates):
        data = self.get_ui_preferences()
        if not isinstance(updates, dict):
            return data
        for key, val in updates.items():
            if key == 'sidebar_open' and isinstance(val, dict):
                sb = dict(data.get('sidebar_open') or {})
                for nav_id, flag in val.items():
                    sb[str(nav_id)] = bool(flag)
                data['sidebar_open'] = sb
            else:
                data[key] = val
        self.ui_preferences_json = json.dumps(data, ensure_ascii=False)
        self.save(update_fields=['ui_preferences_json', 'updated_at'])
        return data


class UserSession(models.Model):
    """ตาราง user_sessions ตามข้อ 5 ในสเปก สำหรับควบคุม 1 Session ต่อบัญชีนักเรียน"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_sessions')
    session_token = models.CharField('Session Token', max_length=120, db_index=True)
    device_info = models.CharField('ข้อมูลอุปกรณ์/เบราว์เซอร์', max_length=300, blank=True)
    ip_address = models.CharField('IP Address', max_length=64, blank=True)
    is_active = models.BooleanField('ใช้งานอยู่', default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_sessions'
        ordering = ['-last_active']
        verbose_name = 'เซสชันผู้ใช้'
        verbose_name_plural = 'เซสชันผู้ใช้'

    def __str__(self):
        return f'{self.user.username} @ {self.device_info} ({self.session_token[:8]})'


class SystemSetting(models.Model):
    """ตั้งค่าระบบส่วนกลางสำหรับ Admin และครูผู้สอน"""
    site_title = models.CharField('ชื่อระบบหลัก', max_length=150, default='COMSC4 DEBSIRIN')
    site_subtitle = models.CharField('คำอธิบายระบบ', max_length=200, default='ระบบจัดการการเรียนการสอนวิทยาการคำนวณ โรงเรียนเทพศิรินทร์')
    allowed_oauth_domain = models.CharField('โดเมน Google OAuth ที่อนุญาต', max_length=100, default='debsirin.ac.th')
    google_client_id = models.CharField('Google OAuth Client ID', max_length=255, blank=True)
    google_client_secret = models.CharField('Google OAuth Client Secret', max_length=255, blank=True)
    enforce_single_session_student = models.BooleanField('บังคับนักเรียนใช้ได้ 1 Session ต่อบัญชี', default=True)
    enforce_single_session_staff = models.BooleanField('บังคับครู/Admin ใช้ได้ 1 Session ต่อบัญชี', default=False)
    # AI API Setting (Module B)
    ai_provider = models.CharField(
        'ผู้ให้บริการ AI สร้างข้อสอบ', max_length=20,
        choices=[('gemini', 'Google Gemini API'), ('claude', 'Anthropic Claude API')],
        default='gemini'
    )
    gemini_api_key = models.CharField('Gemini API Key', max_length=255, blank=True)
    claude_api_key = models.CharField('Claude API Key', max_length=255, blank=True)
    # เปิด/ปิดเมนูเกมหลักสำหรับนักเรียน
    sorting_menu_open = models.BooleanField('เปิดเมนูกิจกรรมเรียงลำดับ (DSBasicSorting)', default=True)
    bingo_menu_open = models.BooleanField('เปิดเมนูเกม Bingo', default=True)
    lotto_menu_open = models.BooleanField('เปิดเมนูระบบสุ่ม Debsirin Lotto', default=True)
    # ตรวจสอบ IP ห้องเรียนเมื่อเช็คชื่อ
    attendance_check_ip_prefix = models.CharField(
        'Prefix IP ที่อนุญาตให้เช็คชื่อ (คั่นด้วยจุลภาค, เว้นว่าง=ทุก IP)',
        max_length=200, blank=True, default=''
    )
    theme_colors_json = models.TextField('ตั้งค่าสีธีมระบบ (JSON)', default='{}', blank=True)
    sidebar_heading_size_px = models.PositiveSmallIntegerField('ขนาดตัวอักษรหัวข้อใหญ่เมนูข้าง (px)', default=14)
    last_activity_max_score = models.FloatField('คะแนนกิจกรรมล่าสุด', default=5.0)
    current_academic_year = models.CharField('ปีการศึกษาปัจจุบัน (พ.ศ.)', max_length=20, default='2569')
    gdrive_service_account_json = models.TextField(
        'Google Drive Service Account JSON (สำหรับอัปโหลดอัตโนมัติ)',
        blank=True, default=''
    )
    gdrive_default_folder_url = models.CharField('Google Drive Folder เริ่มต้น', max_length=500, blank=True, default='')
    gdrive_default_folder_verified = models.BooleanField('ตรวจสอบ Google Drive Folder เริ่มต้นผ่านแล้ว', default=False)
    updated_at = models.DateTimeField(auto_now=True)

    DEFAULT_THEME_COLORS = {
        'teacher_sidebar_bg_1': '#0f4c5c',
        'teacher_sidebar_bg_2': '#1a759f',
        'teacher_primary': '#0f4c5c',
        'teacher_accent': '#facc15',
        'teacher_topbar_bg': '#ffffff',
        'teacher_topbar_text': '#0f172a',
        'teacher_body_bg': '#f1f5f9',
        'teacher_card_bg': '#ffffff',
        'teacher_card_header_bg': '#ffffff',
        'teacher_checkin_bg': '#ecfdf5',
        'student_sidebar_bg_1': '#14532d',
        'student_sidebar_bg_2': '#166534',
        'student_primary': '#15803d',
        'student_accent': '#eab308',
        'student_topbar_bg': '#ffffff',
        'student_topbar_text': '#0f172a',
        'student_body_bg': '#f1f5f9',
        'student_card_bg': '#ffffff',
        'student_card_header_bg': '#ffffff',
        'student_checkin_bg': '#ecfdf5',
        'seat_default_bg': '#ffffff',
        'seat_online_bg_1': '#bbf7d0',
        'seat_online_bg_2': '#86efac',
        'seat_online_border': '#15803d',
        'seat_checked_bg': '#dcfce7',
        'group_activity_bg': '#fefce8',
        'group_activity_border': '#fde047',
        'bingo_all_color': '#16a34a',
        'bingo_room_color': '#eab308',
    }

    class Meta:
        verbose_name = 'ตั้งค่าระบบ'
        verbose_name_plural = 'ตั้งค่าระบบ'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def get_theme_colors(self):
        colors = dict(self.DEFAULT_THEME_COLORS)
        try:
            parsed = json.loads(self.theme_colors_json or '{}')
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if k in colors and isinstance(v, str) and re.match(r'^#[0-9a-fA-F]{6}$', v.strip()):
                        colors[k] = v.strip()
        except Exception:
            pass
        return colors

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AcademicYear(models.Model):
    """ปีการศึกษาที่ Admin กดสร้างอย่างเป็นทางการ (ใช้แยกนักเรียน กิจกรรม บทเรียน คะแนน และรีเซ็ตเกม)"""
    year = models.CharField('ปีการศึกษา (พ.ศ.)', max_length=20, unique=True, db_index=True)
    title = models.CharField('คำอธิบายปีการศึกษา', max_length=120, blank=True, default='')
    is_current = models.BooleanField('ปีการศึกษาปัจจุบัน', default=False, db_index=True)
    copied_from_year = models.CharField('คัดลอกจากปีการศึกษา', max_length=20, blank=True, default='')
    lessons_copied_count = models.PositiveIntegerField('จำนวนบทเรียนที่คัดลอกมา', default=0)
    activities_copied_count = models.PositiveIntegerField('จำนวนกิจกรรมที่คัดลอกมา', default=0)
    games_reset_at = models.DateTimeField('เวลาที่รีเซ็ตเกมทั้งหมด', null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-year', '-created_at']
        verbose_name = 'ปีการศึกษา'
        verbose_name_plural = 'ปีการศึกษา'

    def __str__(self):
        return f'ปีการศึกษา {self.year}'


class LabRoomLayout(models.Model):
    """แผนผังห้องเรียนคอมพิวเตอร์กลาง (เช่น ห้อง 134, 135, 136) ที่ทุกห้องเรียนเลือกใช้งานร่วมกันได้"""
    BOARD_BOTTOM = 'bottom'
    BOARD_TOP = 'top'
    BOARD_CHOICES = [
        (BOARD_BOTTOM, 'กระดานดำ / หน้าชั้นเรียนอยู่ด้านล่างสุด (มุมมองจากหลังห้องไปหน้าห้อง)'),
        (BOARD_TOP, 'กระดานดำ / หน้าชั้นเรียนอยู่ด้านบนสุด'),
    ]
    name = models.CharField('ชื่อแผนผังห้อง (เช่น ห้อง 134)', max_length=100, unique=True)
    seat_rows = models.PositiveSmallIntegerField('จำนวนแถวแนวนอน', default=6)
    seat_cols = models.PositiveSmallIntegerField('จำนวนแถวแนวตั้ง', default=7)
    board_position = models.CharField(
        'ตำแหน่งกระดานดำ', max_length=15, choices=BOARD_CHOICES, default=BOARD_BOTTOM
    )
    layout_config = models.TextField('การตั้งค่าผังห้อง (JSON)', blank=True, default='{}')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'แผนผังห้องเรียน'
        verbose_name_plural = 'แผนผังห้องเรียน'

    def __str__(self):
        return f'{self.name} ({self.seat_rows}×{self.seat_cols})'

    def get_layout_dict(self):
        try:
            d = json.loads(self.layout_config or '{}')
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def get_disabled_seats_set(self):
        d = self.get_layout_dict()
        disabled = d.get('disabled_seats', [])
        return set(str(x) for x in disabled)

    def get_row_order(self):
        d = self.get_layout_dict()
        default_order = 'bottom_to_top' if self.board_position == self.BOARD_BOTTOM else 'top_to_bottom'
        order = d.get('row_order', default_order)
        return order if order in ('bottom_to_top', 'top_to_bottom') else default_order

    def get_rows_sequence(self):
        if self.get_row_order() == 'bottom_to_top':
            return list(range(self.seat_rows, 0, -1))
        return list(range(1, self.seat_rows + 1))

    def get_machine_labels_dict(self):
        d = self.get_layout_dict()
        labels = d.get('machine_labels')
        if not isinstance(labels, dict):
            labels = d.get('custom_labels', {})
        return labels if isinstance(labels, dict) else {}

    def get_default_seat_numbers_dict(self, valid_class_numbers=None):
        """คำนวณเลขที่นักเรียนเริ่มต้นตามเลขเครื่องของห้องนี้ (ถ้าเลขเครื่องตรงกับเลขที่นักเรียน)"""
        disabled = self.get_disabled_seats_set()
        labels = self.get_machine_labels_dict()
        result = {}
        for key, raw_lbl in labels.items():
            skey = str(key)
            if skey in disabled or '_' not in skey:
                continue
            r_str, c_str = skey.split('_', 1)
            if not (r_str.isdigit() and c_str.isdigit()):
                continue
            r, c = int(r_str), int(c_str)
            if not (1 <= r <= self.seat_rows and 1 <= c <= self.seat_cols):
                continue
            s = str(raw_lbl or '').strip()
            if not s:
                continue
            if s.isdigit():
                num = int(s)
            else:
                m = re.search(r'(\d+)', s)
                num = int(m.group(1)) if m else 0
            if num > 0 and (valid_class_numbers is None or num in valid_class_numbers):
                result[skey] = num
        return result

    def sync_linked_classrooms(self):
        self.classrooms.update(
            seat_rows=self.seat_rows,
            seat_cols=self.seat_cols,
            board_position=self.board_position,
        )


class Classroom(models.Model):
    """ห้องเรียน (เช่น ห้อง 01 ถึง 14) พร้อมขนาดแผนผังที่นั่งและตำแหน่งกระดานดำที่ปรับแก้ไขได้"""
    BOARD_BOTTOM = 'bottom'
    BOARD_TOP = 'top'
    BOARD_CHOICES = [
        (BOARD_BOTTOM, 'กระดานดำ / หน้าชั้นเรียนอยู่ด้านล่างสุด (มุมมองจากหลังห้องไปหน้าห้อง)'),
        (BOARD_TOP, 'กระดานดำ / หน้าชั้นเรียนอยู่ด้านบนสุด'),
    ]
    code = models.CharField('รหัสห้อง (เช่น 01)', max_length=10, unique=True)
    name = models.CharField('ชื่อห้องเรียน (เช่น ม.4/1)', max_length=100)
    grade_level = models.CharField('ระดับชั้น', max_length=20, default='4')
    lab_room = models.ForeignKey(
        LabRoomLayout, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='classrooms', verbose_name='แผนผังห้องที่ใช้เรียน'
    )
    seat_rows = models.PositiveSmallIntegerField('จำนวนแถวที่นั่ง', default=6)
    seat_cols = models.PositiveSmallIntegerField('จำนวนคอลัมน์ที่นั่ง', default=7)
    board_position = models.CharField(
        'ตำแหน่งกระดานดำ', max_length=15, choices=BOARD_CHOICES, default=BOARD_BOTTOM
    )
    layout_config = models.TextField(
        'การตั้งค่าผังที่นั่งเพิ่มเติม (JSON: disabled_seats, custom_labels, aisle_cols)',
        blank=True, default='{}'
    )
    is_active = models.BooleanField('เปิดใช้งาน', default=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'ห้องเรียน'
        verbose_name_plural = 'ห้องเรียน'

    def __str__(self):
        return f'{self.name} (ห้อง {self.code})'

    def get_layout_dict(self):
        try:
            d = json.loads(self.layout_config or '{}')
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def get_effective_rows(self):
        if self.lab_room_id:
            return int(self.lab_room.seat_rows or 6)
        return int(self.seat_rows or 6)

    def get_effective_cols(self):
        if self.lab_room_id:
            return int(self.lab_room.seat_cols or 7)
        return int(self.seat_cols or 7)

    def get_effective_board_position(self):
        if self.lab_room_id:
            return self.lab_room.board_position or self.BOARD_BOTTOM
        return self.board_position or self.BOARD_BOTTOM

    def get_disabled_seats_set(self):
        if self.lab_room_id:
            return self.lab_room.get_disabled_seats_set()
        d = self.get_layout_dict()
        disabled = d.get('disabled_seats', [])
        return set(str(x) for x in disabled)

    def get_row_order(self):
        if self.lab_room_id:
            return self.lab_room.get_row_order()
        d = self.get_layout_dict()
        default_order = 'bottom_to_top' if self.board_position == self.BOARD_BOTTOM else 'top_to_bottom'
        order = d.get('row_order', default_order)
        return order if order in ('bottom_to_top', 'top_to_bottom') else default_order

    def get_custom_labels_dict(self):
        if self.lab_room_id:
            return self.lab_room.get_machine_labels_dict()
        d = self.get_layout_dict()
        labels = d.get('custom_labels', {})
        return labels if isinstance(labels, dict) else {}

    def get_rows_sequence(self):
        rows = self.get_effective_rows()
        if self.get_row_order() == 'bottom_to_top':
            return list(range(rows, 0, -1))
        return list(range(1, rows + 1))

    def has_custom_seat_override(self):
        if not self.lab_room_id:
            return False
        overrides = self.get_layout_dict().get('seat_overrides_by_lab') or {}
        return isinstance(overrides, dict) and str(self.lab_room_id) in overrides

    def get_seat_numbers_dict(self, valid_class_numbers=None):
        """คืน mapping {'row_col': class_number} โดยใช้ผังปรับเฉพาะห้อง (ถ้ามี) หรือค่าเริ่มต้นตามเลขเครื่องของ LabRoomLayout"""
        d = self.get_layout_dict()
        if self.lab_room_id:
            overrides = d.get('seat_overrides_by_lab') or {}
            lab_key = str(self.lab_room_id)
            if isinstance(overrides, dict) and lab_key in overrides:
                raw = overrides.get(lab_key) or {}
                disabled = self.lab_room.get_disabled_seats_set()
                max_r = self.lab_room.seat_rows
                max_c = self.lab_room.seat_cols
                result = {}
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        skey = str(k)
                        if skey in disabled or '_' not in skey:
                            continue
                        r_str, c_str = skey.split('_', 1)
                        if not (r_str.isdigit() and c_str.isdigit()):
                            continue
                        if not (1 <= int(r_str) <= max_r and 1 <= int(c_str) <= max_c):
                            continue
                        try:
                            num = int(v)
                            if num > 0:
                                result[skey] = num
                        except (TypeError, ValueError):
                            pass
                return result
            return self.lab_room.get_default_seat_numbers_dict(valid_class_numbers=valid_class_numbers)

        raw = d.get('seat_numbers', {})
        result = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:
                    num = int(v)
                    if num > 0:
                        result[str(k)] = num
                except (TypeError, ValueError):
                    pass
        return result

    def get_seat_for_class_number(self, class_number):
        """ค้นหา (row, col) ที่กำหนดให้กับเลขที่นักเรียนนี้ (ถ้าไม่ได้ระบุคืน None)"""
        try:
            target = int(class_number)
        except (TypeError, ValueError):
            return None
        if target <= 0:
            return None
        for key, num in self.get_seat_numbers_dict().items():
            if num == target and '_' in key:
                parts = key.split('_', 1)
                if parts[0].isdigit() and parts[1].isdigit():
                    return int(parts[0]), int(parts[1])
        return None


# =============================================================================
# ระบบเนื้อหาและแบบทดสอบ AI
# =============================================================================
class LessonContent(models.Model):
    title = models.CharField('หัวข้อบทเรียน', max_length=200)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    unit_number = models.PositiveSmallIntegerField('หน่วยการเรียนรู้ที่', default=1)
    summary = models.TextField('สรุปเนื้อหา / คำอธิบาย', blank=True)
    content_html = models.TextField('เนื้อหาบทเรียน', blank=True)
    attachment = models.FileField('ไฟล์สื่อประกอบ', upload_to='activities/', null=True, blank=True)
    video_url = models.URLField('ลิงก์วิดีโอ/สื่อภายนอก', blank=True)
    is_published = models.BooleanField('เผยแพร่ให้นักเรียนเห็น', default=True)
    copied_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='copied_children', verbose_name='คัดลอกจากบทเรียน'
    )
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_lessons')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['unit_number', 'id']
        verbose_name = 'เนื้อหาบทเรียน'
        verbose_name_plural = 'เนื้อหาบทเรียน'

    def __str__(self):
        return f'หน่วยที่ {self.unit_number}: {self.title}'


class AIQuiz(models.Model):
    lesson = models.ForeignKey(LessonContent, null=True, blank=True, on_delete=models.SET_NULL, related_name='quizzes')
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    title = models.CharField('ชื่อแบบทดสอบ', max_length=200)
    topic_prompt = models.TextField('หัวข้อ/คำสั่งที่ใช้ให้ AI สร้างข้อสอบ', blank=True)
    ai_engine_used = models.CharField('สร้างโดย AI', max_length=50, default='Gemini / Smart CS Generator')
    time_limit_minutes = models.PositiveSmallIntegerField('เวลาทำข้อสอบ (นาที)', default=15)
    points_per_question = models.FloatField('คะแนนต่อข้อ', default=1.0)
    sync_to_total_score = models.BooleanField('นำคะแนนเข้าสู่ระบบคะแนนรวมอัตโนมัติ', default=True)
    is_active = models.BooleanField('เปิดให้นักเรียนทำ', default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_quizzes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'แบบทดสอบ AI'
        verbose_name_plural = 'แบบทดสอบ AI'

    def __str__(self):
        return self.title


class AIQuizQuestion(models.Model):
    quiz = models.ForeignKey(AIQuiz, on_delete=models.CASCADE, related_name='questions')
    order_no = models.PositiveSmallIntegerField('ข้อที่', default=1)
    question_text = models.TextField('โจทย์คำถาม')
    choice_a = models.CharField('ตัวเลือก ก', max_length=300)
    choice_b = models.CharField('ตัวเลือก ข', max_length=300)
    choice_c = models.CharField('ตัวเลือก ค', max_length=300)
    choice_d = models.CharField('ตัวเลือก ง', max_length=300)
    correct_choice = models.CharField('ข้อที่ถูก (A/B/C/D)', max_length=1, default='A')
    explanation = models.TextField('คำอธิบายเฉลยจาก AI', blank=True)

    class Meta:
        ordering = ['order_no', 'id']

    def __str__(self):
        return f'{self.quiz.title} - ข้อ {self.order_no}'


class AIQuizAttempt(models.Model):
    quiz = models.ForeignKey(AIQuiz, on_delete=models.CASCADE, related_name='attempts')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quiz_attempts')
    score = models.FloatField('คะแนนที่ได้', default=0)
    max_score = models.FloatField('คะแนนเต็ม', default=0)
    correct_count = models.PositiveSmallIntegerField('จำนวนข้อที่ถูก', default=0)
    total_questions = models.PositiveSmallIntegerField('จำนวนข้อทั้งหมด', default=0)
    answers_json = models.TextField('คำตอบที่เลือก (JSON)', default='{}')
    time_spent_seconds = models.PositiveIntegerField('เวลาที่ใช้ (วินาที)', default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# =============================================================================
# กิจกรรมการเรียนรู้ & สุ่มนำเสนอผลงานพร้อมรูปประจำตัว
# =============================================================================
class LearningActivity(models.Model):
    TYPE_QA = 'qa'
    TYPE_IMAGE = 'image'
    TYPE_OTHER_FILE = 'other_file'
    TYPE_GENERAL = 'general'
    TYPE_CHOICES = [
        (TYPE_QA, 'ถาม-ตอบ (ข้อความ)'),
        (TYPE_IMAGE, 'แนบรูปภาพผลงาน'),
        (TYPE_OTHER_FILE, 'อัปโหลดไฟล์อื่นๆ (PDF, DOCX, ZIP, PY ฯลฯ)'),
        (TYPE_GENERAL, 'ทั่วไป (ข้อความ/ไฟล์)'),
    ]

    STORAGE_LOCAL = 'local'
    STORAGE_GDRIVE = 'gdrive'
    STORAGE_CHOICES = [
        (STORAGE_LOCAL, 'จัดเก็บในเครื่องเซิร์ฟเวอร์ (Local Media)'),
        (STORAGE_GDRIVE, 'จัดเก็บใน Google Drive Folder (ต้องตรวจสอบผ่านก่อนอนุมัติ)'),
    ]

    PDF_EMBED = 'embed'
    PDF_DOWNLOAD = 'download'
    PDF_DISPLAY_CHOICES = [
        (PDF_EMBED, 'แสดง PDF ผ่านหน้าใบงาน + ดาวน์โหลดได้'),
        (PDF_DOWNLOAD, 'แสดงลิงก์ดาวน์โหลด PDF อย่างเดียว'),
    ]

    title = models.CharField('ชื่อใบงาน/กิจกรรม', max_length=200)
    description = models.TextField('คำชี้แจงกิจกรรม', blank=True)
    content_html = models.TextField('เนื้อหาใบงานแบบตบแต่ง (Rich HTML)', blank=True)
    worksheet_pdf = models.FileField('ไฟล์ PDF ประกอบใบงาน', upload_to='activities/pdfs/', null=True, blank=True)
    pdf_display_mode = models.CharField('รูปแบบการแสดง PDF', max_length=20, choices=PDF_DISPLAY_CHOICES, default=PDF_EMBED)
    activity_type = models.CharField('ประเภทกิจกรรม', max_length=20, choices=TYPE_CHOICES, default=TYPE_GENERAL)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    allowed_file_extensions = models.CharField(
        'นามสกุลไฟล์ที่อนุญาต (คั่นด้วยจุลภาค, เว้นว่าง=ทุกไฟล์)',
        max_length=255, blank=True, default=''
    )
    storage_destination = models.CharField('แหล่งเก็บไฟล์ผลงาน', max_length=20, choices=STORAGE_CHOICES, default=STORAGE_LOCAL)
    gdrive_folder_url = models.CharField('ลิงก์ / Folder ID ของ Google Drive', max_length=500, blank=True, default='')
    gdrive_folder_id = models.CharField('Google Drive Folder ID', max_length=120, blank=True, default='')
    gdrive_verified = models.BooleanField('ตรวจสอบ Google Drive ผ่านและอนุมัติใช้งานแล้ว', default=False)
    gdrive_verified_at = models.DateTimeField('เวลาตรวจสอบ Google Drive ล่าสุด', null=True, blank=True)
    gdrive_verify_message = models.CharField('ผลการตรวจสอบ Google Drive', max_length=300, blank=True, default='')
    score_period = models.ForeignKey(
        'ScorePeriod', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='activities', verbose_name='ช่วงคะแนน'
    )
    max_score = models.FloatField('คะแนนเต็ม', default=5.0)
    due_date = models.DateTimeField('กำหนดส่ง', null=True, blank=True)
    is_active = models.BooleanField('เปิดรับงาน', default=True)
    is_group_activity = models.BooleanField('เป็นกิจกรรมกลุ่ม', default=False)
    group_count = models.PositiveSmallIntegerField('แบ่งเป็นจำนวนกลุ่ม', default=0)
    max_group_size = models.PositiveSmallIntegerField('จำนวนสมาชิกสูงสุดต่อกลุ่ม', default=4)
    group_selection_open = models.BooleanField('เปิดให้นักเรียนเลือก/ย้ายกลุ่มได้', default=True)
    target_rooms_json = models.TextField('ห้องเรียนที่กำหนดให้ทำใบงาน (JSON, ว่าง=ทุกห้อง)', default='[]', blank=True)
    require_group_complete = models.BooleanField('พิจารณาแบบกลุ่ม (ต้องส่งครบทั้งกลุ่ม)', default=False)
    require_material_check = models.BooleanField('พิจารณาตรวจสอบ (ต้องถูกกดตรวจสอบสมุด/หนังสือ)', default=False)
    group_source_activity = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='กิจกรรมกลุ่มที่ใช้อ้างอิงสมาชิก'
    )
    copied_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='copied_children', verbose_name='คัดลอกจากกิจกรรม'
    )
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_activities')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @property
    def has_revealed_answers(self):
        return self.answer_key_items.filter(is_revealed=True).exists()

    def get_allowed_extensions_list(self):
        raw = (self.allowed_file_extensions or '').strip()
        if not raw:
            return []
        exts = []
        for part in re.split(r'[,;\s]+', raw):
            p = part.strip().lower().lstrip('*')
            if not p:
                continue
            if not p.startswith('.'):
                p = '.' + p
            if p not in exts:
                exts.append(p)
        return exts

    def get_target_rooms_list(self):
        try:
            raw = json.loads(self.target_rooms_json or '[]')
        except Exception:
            raw = []
        if not isinstance(raw, list):
            return []
        codes = []
        for item in raw:
            s = str(item or '').strip()
            if not s or s.lower() == 'all':
                continue
            if s.isdigit():
                s = s.zfill(2)
            codes.append(s)
        return sorted(set(codes))

    def applies_to_room(self, room_code):
        targets = self.get_target_rooms_list()
        if not targets:
            return True
        rc = str(room_code or '').strip()
        if not rc:
            return True
        if rc.isdigit():
            rc = rc.zfill(2)
        return rc in targets

    def get_target_rooms_display(self):
        targets = self.get_target_rooms_list()
        if not targets:
            return 'ทุกชั้น/ทุกห้อง'
        target_set = set(targets)
        all_rooms = list(Classroom.objects.all().order_by('code'))
        if all_rooms and target_set >= {c.code for c in all_rooms}:
            return 'ทุกชั้น/ทุกห้อง'
        grade_map = {}
        name_map = {}
        for c in all_rooms:
            name_map[c.code] = c.name
            if c.grade_level:
                grade_map.setdefault(c.grade_level, set()).add(c.code)
        full_grades = [f'ม.{g}' for g, rset in sorted(grade_map.items()) if rset and target_set == rset]
        if full_grades:
            return f'ระดับชั้น {" / ".join(full_grades)} ทั้งหมด'
        names = [name_map.get(c, c) for c in targets]
        if len(names) <= 4:
            return ', '.join(names)
        return ', '.join(names[:4]) + f' (+{len(names) - 4} ห้อง)'


class ActivityAnswerKeyItem(models.Model):
    """เฉลยใบงานแต่ละข้อ (ตบแต่ง Rich HTML / แนบรูปภาพ / PDF และเปิด-ปิดเฉลยรายข้อในเมนูนำเสนอใบงาน)"""
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='answer_key_items')
    order_no = models.PositiveSmallIntegerField('ข้อที่', default=1)
    title = models.CharField('หัวข้อ / ชื่อข้อเฉลย', max_length=200, default='ข้อที่ 1')
    content_html = models.TextField('เนื้อหาเฉลย (Rich HTML / รูปภาพ)', blank=True)
    pdf_file = models.FileField('ไฟล์ PDF เฉลย (ถ้ามี)', upload_to='activities/answer_pdfs/', null=True, blank=True)
    pdf_display_mode = models.CharField(
        'รูปแบบแสดง PDF เฉลย', max_length=20, choices=LearningActivity.PDF_DISPLAY_CHOICES, default=LearningActivity.PDF_EMBED
    )
    is_revealed = models.BooleanField('เปิดเฉลยให้นักเรียนเห็นแล้ว', default=False, db_index=True)
    revealed_at = models.DateTimeField('เวลาที่เปิดเฉลย', null=True, blank=True)

    class Meta:
        db_table = 'activity_answer_key_items'
        ordering = ['order_no', 'id']

    def __str__(self):
        return f'{self.activity.title} - ข้อ {self.order_no}: {self.title}'


class ActivityGradingTier(models.Model):
    """ตารางกำหนดคะแนนแต่ละครั้งที่ตรวจของกิจกรรม (ครั้งที่ 1, ครั้งที่ 2, ...)"""
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='grading_tiers')
    round_no = models.PositiveSmallIntegerField('ครั้งที่ตรวจ', default=1)
    score = models.FloatField('คะแนนที่ได้รับ')
    label = models.CharField('คำอธิบายรอบตรวจ', max_length=150, blank=True)

    class Meta:
        db_table = 'activity_grading_tiers'
        unique_together = [('activity', 'round_no')]
        ordering = ['round_no', 'id']

    def __str__(self):
        return f'{self.activity.title} - ครั้งที่ {self.round_no}: {self.score:g} คะแนน'


class ActivityExtensionRequest(models.Model):
    """คำร้องขอส่งงานล่าช้าเฉพาะรายนักเรียน (อนุมัติแล้วเปิดให้ส่งภายใน 2 วันหรือตามกำหนด)"""
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'รอครูอนุมัติ'),
        (STATUS_APPROVED, 'อนุมัติให้ส่งเพิ่ม'),
        (STATUS_REJECTED, 'ไม่อนุมัติ'),
    ]

    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='extension_requests')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_extension_requests')
    reason = models.TextField('เหตุผลที่ขอส่งงานล่าช้า', blank=True)
    status = models.CharField('สถานะคำร้อง', max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    approved_until = models.DateTimeField('ส่งได้ถึงวันที่/เวลา', null=True, blank=True)
    teacher_note = models.CharField('หมายเหตุจากครู', max_length=255, blank=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='reviewed_extension_requests')
    reviewed_at = models.DateTimeField('เวลาที่พิจารณา', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_extension_requests'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.student.student_id} -> {self.activity.title} ({self.get_status_display()})'


class ActivityGroup(models.Model):
    """กลุ่มย่อยสำหรับใบงาน/กิจกรรมกลุ่ม (แยกตามห้องเรียน เรียงสมาชิกตามเลขที่)"""
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='groups')
    room_code = models.CharField('รหัสห้อง (01-14)', max_length=20, db_index=True)
    group_no = models.PositiveSmallIntegerField('กลุ่มที่', default=1)
    name = models.CharField('ชื่อกลุ่ม', max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_groups'
        unique_together = [('activity', 'room_code', 'group_no')]
        ordering = ['room_code', 'group_no', 'id']

    def save(self, *args, **kwargs):
        if not (self.name or '').strip():
            self.name = f'กลุ่มที่ {self.group_no}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.activity.title} ({self.room_code}) - {self.name}'


class ActivityGroupMember(models.Model):
    """สมาชิกนักเรียนในกลุ่มกิจกรรม (คนละไม่เกิน 1 กลุ่มต่อกิจกรรม)"""
    group = models.ForeignKey(ActivityGroup, on_delete=models.CASCADE, related_name='members')
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='group_members')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_group_memberships')
    joined_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'activity_group_members'
        unique_together = [('activity', 'student')]
        ordering = ['student__class_number', 'student__student_id', 'id']

    def __str__(self):
        return f'{self.group.name}: {self.student.class_number} {self.student.get_full_name()}'


class ActivitySubmission(models.Model):
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_submissions')
    group = models.ForeignKey(ActivityGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name='submissions')
    work_title = models.CharField('หัวข้อผลงาน', max_length=200, blank=True)
    work_text = models.TextField('รายละเอียด / โค้ด / ลิงก์ผลงาน', blank=True)
    work_file = models.FileField('ไฟล์ผลงานแนบ', upload_to='activities/', null=True, blank=True)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    work_file_name = models.CharField('ชื่อไฟล์เดิมที่อัปโหลด', max_length=255, blank=True, default='')
    work_file_size = models.BigIntegerField('ขนาดไฟล์ (bytes)', default=0)
    gdrive_file_id = models.CharField('Google Drive File ID', max_length=120, blank=True, default='')
    gdrive_file_url = models.CharField('ลิงก์ไฟล์บน Google Drive', max_length=500, blank=True, default='')
    score_awarded = models.FloatField('คะแนนที่ได้', null=True, blank=True)
    graded_round_no = models.PositiveSmallIntegerField('ครั้งที่ตรวจ', null=True, blank=True)
    feedback = models.CharField('ความคิดเห็นครูผู้สอน', max_length=300, blank=True)
    is_presented = models.BooleanField('สุ่มนำเสนอแล้ว', default=False)
    ai_review_status = models.CharField('สถานะตรวจด้วย AI', max_length=20, blank=True, default='')
    ai_review_feedback = models.TextField('คำแนะนำจาก AI', blank=True, default='')
    similarity_warning = models.CharField('คำเตือนคำตอบคล้ายเพื่อน', max_length=255, blank=True, default='')
    similarity_score = models.FloatField('ความคล้ายคลึงสูงสุด', default=0.0)
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('activity', 'student')]
        ordering = ['-submitted_at']

    @property
    def is_image_file(self):
        raw = (self.work_file_name or (self.work_file.name if self.work_file else '') or '').lower()
        ext = '.' + raw.rsplit('.', 1)[-1] if '.' in raw else ''
        return ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')

    @property
    def effective_file_url(self):
        if self.work_file:
            try:
                return self.work_file.url
            except Exception:
                pass
        return self.gdrive_file_url or ''

    @property
    def effective_file_label(self):
        if self.work_file_name:
            return self.work_file_name
        if self.work_file:
            return (self.work_file.name or '').rsplit('/', 1)[-1]
        if self.gdrive_file_url:
            return 'ไฟล์บน Google Drive'
        return ''

    @property
    def file_size_display(self):
        sz = int(self.work_file_size or 0)
        if not sz and self.work_file:
            try:
                sz = int(self.work_file.size or 0)
            except Exception:
                sz = 0
        if sz <= 0:
            return ''
        if sz < 1024:
            return f'{sz} B'
        if sz < 1024 * 1024:
            return f'{sz / 1024:.1f} KB'
        return f'{sz / (1024 * 1024):.2f} MB'


class ActivityPresentationDraw(models.Model):
    """รายการสุ่มนักเรียนนำเสนอผลงาน (แยกตามรอบการสุ่ม แสดงใน List ตรวจสอบก่อนให้คะแนน และนำออกได้)"""
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='presentation_draws')
    submission = models.ForeignKey(ActivitySubmission, null=True, blank=True, on_delete=models.SET_NULL, related_name='presentation_draws')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='presentation_draws')
    classroom = models.ForeignKey(Classroom, null=True, blank=True, on_delete=models.SET_NULL)
    draw_date = models.DateField('วันที่สุ่ม', default=timezone.localdate, db_index=True)
    round_no = models.PositiveIntegerField('รอบการสุ่ม', default=1, db_index=True)
    order_index = models.PositiveSmallIntegerField('ลำดับที่สุ่ม', default=1)
    is_removed = models.BooleanField('นำรายชื่อออกแล้ว', default=False, db_index=True)
    removed_reason = models.CharField('เหตุผลที่นำออก', max_length=255, blank=True)
    is_graded = models.BooleanField('ให้คะแนนแล้ว', default=False)
    score_awarded = models.FloatField('คะแนนที่ให้', null=True, blank=True)
    feedback = models.CharField('ข้อเสนอแนะ', max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_presentation_draws'
        ordering = ['order_index', 'id']


class ActivityBulkGradeBatch(models.Model):
    """ประวัติการให้คะแนนทั้งหมดรายกิจกรรม/ห้อง (ครั้งแรกเต็ม ครั้งถัดไปลดทีละ 1 จนต่ำสุด 1 คะแนน)"""
    activity = models.ForeignKey(LearningActivity, on_delete=models.CASCADE, related_name='bulk_grade_batches')
    room_code = models.CharField('รหัสห้อง (01-14 หรือ all)', max_length=20, default='all', db_index=True)
    batch_no = models.PositiveSmallIntegerField('ครั้งที่ให้คะแนน', default=1)
    score_given = models.FloatField('คะแนนที่ให้ในครั้งนี้')
    graded_count = models.PositiveIntegerField('จำนวนนักเรียนที่ได้รับคะแนนครั้งนี้', default=0)
    score_period = models.ForeignKey('ScorePeriod', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    awarded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'activity_bulk_grade_batches'
        ordering = ['-created_at', '-id']


# =============================================================================
# เช็คชื่อด้วยโค้ด + ตรวจ IP + ผูกแผนผังที่นั่งห้องเรียน
# =============================================================================
class AttendanceSession(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='attendance_sessions')
    title = models.CharField('หัวข้อคาบเรียน', max_length=150, default='คาบเรียนวิทยาการคำนวณ')
    check_in_code = models.CharField('รหัสเช็คชื่อ 6 หลัก', max_length=12, db_index=True)
    allowed_ip_prefix = models.CharField('ล็อก IP ห้องเรียน (เว้นว่าง=ไม่จำกัด)', max_length=100, blank=True)
    attendance_points = models.FloatField('คะแนนเข้าเรียน', default=1.0)
    is_open = models.BooleanField('เปิดให้เช็คชื่ออยู่', default=True)
    opened_at = models.DateTimeField('เวลาที่เปิดเช็คชื่อล่าสุด', null=True, blank=True)
    auto_close_minutes = models.PositiveSmallIntegerField('ปิดอัตโนมัติ (นาที)', default=25)
    is_summarized = models.BooleanField('สรุปการเช็คชื่อแล้ว', default=False, db_index=True)
    summarized_at = models.DateTimeField('เวลาที่สรุปการเช็คชื่อ', null=True, blank=True)
    registered_count_snapshot = models.PositiveSmallIntegerField('จำนวนคนลงทะเบียนตอนสรุป', default=0)
    absent_count_snapshot = models.PositiveSmallIntegerField('จำนวนคนขาดตอนสรุป', default=0)
    total_students_snapshot = models.PositiveSmallIntegerField('จำนวนคนทั้งหมดตอนสรุป', default=0)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def expires_at(self):
        base_dt = self.opened_at or self.created_at or timezone.now()
        mins = int(self.auto_close_minutes or 25)
        return base_dt + timedelta(minutes=mins)

    @property
    def remaining_seconds(self):
        if not self.is_open:
            return 0
        return max(0, int((self.expires_at - timezone.now()).total_seconds()))

    @property
    def is_expired(self):
        return bool(self.is_open and timezone.now() >= self.expires_at)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.classroom.name} - {self.title} (Code: {self.check_in_code})'


class AttendanceRecord(models.Model):
    STATUS_PRESENT = 'present'
    STATUS_LATE = 'late'
    STATUS_ABSENT = 'absent'
    STATUS_CHOICES = [
        (STATUS_PRESENT, 'มาเรียน'),
        (STATUS_LATE, 'มาสาย'),
        (STATUS_ABSENT, 'ขาดเรียน'),
    ]
    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name='records')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_records')
    seat_row = models.PositiveSmallIntegerField('แถวที่นั่ง', default=1)
    seat_col = models.PositiveSmallIntegerField('คอลัมน์ที่นั่ง', default=1)
    ip_address = models.CharField('IP ที่เช็คชื่อ', max_length=64, blank=True)
    status = models.CharField('สถานะ', max_length=15, choices=STATUS_CHOICES, default=STATUS_PRESENT)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('session', 'student')]
        ordering = ['seat_row', 'seat_col']

    @property
    def seat_code(self):
        row_letter = chr(64 + max(1, min(26, int(self.seat_row or 1))))
        return f'{row_letter}{self.seat_col}'


class StudentMaterialCheck(models.Model):
    """บันทึกการเช็คหนังสือ/สมุดของนักเรียนตามแผนผังที่นั่งพร้อมเชื่อมระบบคะแนนอัตโนมัติ"""
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name='material_checks')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='material_checks')
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    check_date = models.DateField('วันที่เช็ค', default=timezone.localdate, db_index=True)
    session = models.ForeignKey(AttendanceSession, null=True, blank=True, on_delete=models.SET_NULL, related_name='material_checks')
    is_summarized = models.BooleanField('สรุปการเช็คแล้ว', default=False, db_index=True)
    seat_row = models.PositiveSmallIntegerField('แถวที่นั่ง', default=0)
    seat_col = models.PositiveSmallIntegerField('คอลัมน์ที่นั่ง', default=0)
    has_book = models.BooleanField('นำหนังสือมา', default=False)
    has_notebook = models.BooleanField('นำสมุดมา', default=False)
    book_score_log = models.ForeignKey('ScoreLog', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notebook_score_log = models.ForeignKey('ScoreLog', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField('หมายเหตุ', max_length=200, blank=True)
    checked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'student_material_checks'
        ordering = ['-check_date', 'student__class_number']

    def __str__(self):
        return f'{self.classroom.name} {self.student.get_full_name()} ({self.check_date})'


# =============================================================================
# ระบบคะแนน ช่วงคะแนน และกฎคะแนน
# =============================================================================
class ScorePeriod(models.Model):
    """ช่วงคะแนนของรายวิชา (ทุกช่วงมีกิจกรรม + คะแนนบวก/ลบ + เลือกรวมตรงหรือเฉลี่ยสัดส่วนได้ และล็อกเมื่อกดสรุปคะแนน)"""
    CALC_DIRECT = 'direct'
    CALC_PROPORTIONAL = 'proportional'
    CALC_MODE_CHOICES = [
        (CALC_DIRECT, 'รวมแบบไม่เฉลี่ยสัดส่วน (นำคะแนนกิจกรรมบวกกันตรงๆ ไม่หาร)'),
        (CALC_PROPORTIONAL, 'รวมแบบเฉลี่ยสัดส่วน (เทียบสัดส่วนเข้าคะแนนเต็มของช่วงนี้)'),
    ]

    name = models.CharField('ชื่อช่วงคะแนน', max_length=150)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    order_no = models.PositiveSmallIntegerField('ลำดับช่วงที่', default=1, db_index=True)
    max_score = models.FloatField('คะแนนเต็มของช่วงนี้', default=20.0)
    score_calc_mode = models.CharField(
        'วิธีรวมคะแนนกิจกรรมในช่วงนี้',
        max_length=20,
        choices=CALC_MODE_CHOICES,
        default=CALC_DIRECT,
    )
    is_finalized = models.BooleanField('สรุปคะแนนแล้ว (ล็อกห้ามบวก/ลบ)', default=False, db_index=True)
    finalized_at = models.DateTimeField('เวลาที่สรุปคะแนน', null=True, blank=True)
    finalized_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField('หมายเหตุ', max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'score_periods'
        ordering = ['order_no', 'id']
        verbose_name = 'ช่วงคะแนน'
        verbose_name_plural = 'ช่วงคะแนน'

    def __str__(self):
        status = 'สรุปแล้ว' if self.is_finalized else 'เปิดใช้งาน'
        return f'ช่วงที่ {self.order_no}: {self.name} ({status})'


class ScoreRule(models.Model):
    TYPE_PLUS = 'plus'
    TYPE_MINUS = 'minus'
    TYPE_CHOICES = [
        (TYPE_PLUS, 'บวก (+)'),
        (TYPE_MINUS, 'ลบ (-)'),
    ]
    code = models.CharField('รหัสกฎอ้างอิงระบบ', max_length=50, unique=True)
    name = models.CharField('ชื่อกฎคะแนน', max_length=200)
    rule_type = models.CharField('ประเภท', max_length=10, choices=TYPE_CHOICES, default=TYPE_PLUS)
    points = models.FloatField('ค่าคะแนน (เช่น 1, 2, 5)', default=1.0)
    note = models.CharField('หมายเหตุ', max_length=255, blank=True)
    is_auto = models.BooleanField('ระบบให้อัตโนมัติ', default=False)
    is_active = models.BooleanField('เปิดใช้งาน', default=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'กฎคะแนน'
        verbose_name_plural = 'กฎคะแนน'

    def __str__(self):
        sign = '+' if self.rule_type == self.TYPE_PLUS else '-'
        return f'[{sign}{abs(self.points):g}] {self.name}'

    @property
    def signed_points(self):
        val = abs(float(self.points))
        return val if self.rule_type == self.TYPE_PLUS else -val


class ScoreLog(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='score_logs')
    rule = models.ForeignKey(ScoreRule, null=True, blank=True, on_delete=models.SET_NULL, related_name='logs')
    score_period = models.ForeignKey(
        'ScorePeriod', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='score_logs', verbose_name='ช่วงคะแนน'
    )
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    points = models.FloatField('คะแนนที่ได้รับ (+ หรือ -)')
    reason = models.CharField('รายละเอียด/ที่มาของคะแนน', max_length=255)
    source_module = models.CharField(
        'ที่มาของคะแนน', max_length=30,
        choices=[
            ('manual', 'ครูให้คะแนน'),
            ('material', 'เช็คหนังสือ/สมุด'),
            ('sorting', 'กิจกรรมเรียงลำดับ (Sorting)'),
            ('bingo', 'เกม Bingo'),
            ('lotto', 'ระบบสุ่ม Lotto'),
            ('quiz', 'แบบทดสอบ AI'),
            ('activity', 'กิจกรรมการเรียนรู้'),
            ('attendance', 'เช็คชื่อเข้าเรียน'),
        ],
        default='manual', db_index=True
    )
    awarded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='awarded_score_logs')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.score_period_id:
            if ScorePeriod.objects.filter(pk=self.score_period_id, is_finalized=True).exists():
                raise ValueError('ช่วงคะแนนนี้ถูกสรุปคะแนนและล็อกไว้แล้ว ไม่สามารถบวกหรือลบคะแนนในส่วนนี้ได้ กรุณาไปดำเนินการในช่วงคะแนนถัดไป')
        elif self.pk is None:
            target_yr = (self.academic_year or '').strip() or (SystemSetting.get().current_academic_year or '2569')
            if not (self.academic_year or '').strip():
                self.academic_year = target_yr
            open_period = (
                ScorePeriod.objects.filter(academic_year=target_yr, is_finalized=False)
                .order_by('order_no', 'id')
                .first()
            )
            if not open_period:
                max_ord = ScorePeriod.objects.filter(academic_year=target_yr).aggregate(m=Max('order_no'))['m'] or 0
                open_period = ScorePeriod.objects.create(
                    name=f'ช่วงคะแนนที่ {max_ord + 1}',
                    academic_year=target_yr,
                    order_no=max_ord + 1,
                    max_score=20.0,
                )
            self.score_period = open_period
        super().save(*args, **kwargs)
        self.student.recalculate_total_score()

    def delete(self, *args, **kwargs):
        if self.score_period_id and ScorePeriod.objects.filter(pk=self.score_period_id, is_finalized=True).exists():
            raise ValueError('ไม่สามารถลบหรือแก้ไขคะแนนในช่วงคะแนนที่สรุปและล็อกไว้แล้ว')
        st = self.student
        super().delete(*args, **kwargs)
        st.recalculate_total_score()


# =============================================================================
# ระบบกิจกรรมเรียงลำดับ (dsbasicsorting)
# ตาราง sorting_activities และ sorting_scores ตามสเปกข้อ 5
# =============================================================================
class SortingActivity(models.Model):
    MODE_ASC = 'asc'
    MODE_DESC = 'desc'
    MODE_BUBBLE_ASC = 'bubble_asc'
    MODE_BUBBLE_DESC = 'bubble_desc'
    MODE_SELECTION_ASC = 'selection_asc'
    MODE_SELECTION_DESC = 'selection_desc'
    MODE_INSERTION_ASC = 'insertion_asc'
    MODE_INSERTION_DESC = 'insertion_desc'
    MODE_CHOICES = [
        (MODE_ASC, 'ลาก/คลิกเรียงลำดับ น้อยไปมาก (Ascending)'),
        (MODE_DESC, 'ลาก/คลิกเรียงลำดับ มากไปน้อย (Descending)'),
        (MODE_BUBBLE_ASC, 'Bubble Sort การเรียงแบบฟองสบู่ (น้อยไปมาก)'),
        (MODE_BUBBLE_DESC, 'Bubble Sort การเรียงแบบฟองสบู่ (มากไปน้อย)'),
        (MODE_SELECTION_ASC, 'Selection Sort การเรียงแบบเลือก (น้อยไปมาก)'),
        (MODE_SELECTION_DESC, 'Selection Sort การเรียงแบบเลือก (มากไปน้อย)'),
        (MODE_INSERTION_ASC, 'Insertion Sort การเรียงแบบแทรก (น้อยไปมาก)'),
        (MODE_INSERTION_DESC, 'Insertion Sort การเรียงแบบแทรก (มากไปน้อย)'),
    ]

    title = models.CharField('ชื่อรอบ/กิจกรรมเรียงลำดับ', max_length=200)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    dataset = models.CharField(
        'ชุดข้อมูล (คั่นด้วยจุลภาค เช่น 12, 5, 9, 1, 15 หรือเว้นว่างเพื่อสุ่มอัตโนมัติ)',
        max_length=500, blank=True, default=''
    )
    unit_count = models.PositiveSmallIntegerField('จำนวนตัวเลขที่เรียง (5-10 ตัว)', default=7)
    mode = models.CharField('รูปแบบการเรียง', max_length=30, choices=MODE_CHOICES, default=MODE_BUBBLE_ASC)
    time_limit = models.PositiveIntegerField('จำกัดเวลาต่อรอบ (วินาที)', default=180)
    is_active = models.BooleanField('เปิดให้นักเรียนทำกิจกรรม', default=True, db_index=True)
    is_exam_mode = models.BooleanField('เปิดโหมดสอบ (Exam Mode)', default=False, db_index=True)
    top_x_reward = models.PositiveSmallIntegerField('จำนวนผู้ได้คะแนนสูงสุดที่จะได้รับคะแนนบวก (X คน)', default=5)
    exam_closed_rewarded = models.BooleanField('แจกคะแนนโหมดสอบรอบนี้แล้ว', default=False)
    session_no = models.PositiveIntegerField('หมายเลขเซสชันสด', default=1)
    broadcast_seq = models.PositiveIntegerField('ลำดับคำสั่ง PLAY จากผู้ดูแล', default=0)
    broadcast_sort = models.CharField('อัลกอริทึมที่ครูสั่ง (bubble/selection/insertion)', max_length=20, default='bubble')
    broadcast_order = models.CharField('ทิศทางที่ครูสั่ง (asc/desc)', max_length=10, default='asc')
    broadcast_unit = models.PositiveSmallIntegerField('จำนวนตัวเลขที่ครูสั่ง (4=สุ่ม5-9, 5-10)', default=7)
    broadcast_room_code = models.CharField('เฉพาะห้องที่ครูสั่ง PLAY (เว้นว่าง=ทุกห้อง)', max_length=200, blank=True, default='')
    classroom = models.ForeignKey(Classroom, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='เฉพาะห้องเรียน (เว้นว่าง=ทุกห้อง)')
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_sorting_activities')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sorting_activities'
        ordering = ['-created_at']
        verbose_name = 'กิจกรรมเรียงลำดับ'
        verbose_name_plural = 'กิจกรรมเรียงลำดับ'

    def __str__(self):
        return f'{self.title} ({self.get_mode_display()})'

    def get_numbers_list(self):
        if self.dataset and self.dataset.strip():
            nums = []
            for part in re.split(r'[,\s]+', self.dataset.strip()):
                if part:
                    try:
                        nums.append(int(part))
                    except ValueError:
                        pass
            if len(nums) >= 3:
                return nums[:10]
        import random
        cnt = max(4, min(10, int(self.unit_count or 7)))
        return random.sample(range(1, 16), cnt)


class SortingScore(models.Model):
    """ตาราง sorting_scores ตามสเปกข้อ 5 + ข้อมูลความคืบหน้าสด Real-time"""
    activity = models.ForeignKey(SortingActivity, on_delete=models.CASCADE, related_name='scores')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sorting_scores')
    session_no = models.PositiveIntegerField('เซสชันที่', default=1, db_index=True)
    play_round_no = models.PositiveSmallIntegerField('ครั้งที่เล่น (re)', default=1)
    score = models.FloatField('คะแนนที่ได้', default=0.0, db_index=True)
    cumulative_score = models.FloatField('คะแนนสะสมรวม', default=0.0)
    time_used = models.FloatField('เวลาที่ใช้ (วินาที)', default=0.0)
    attempts = models.PositiveIntegerField('จำนวนครั้งที่ลอง/ผิด', default=0)
    right_rounds = models.PositiveSmallIntegerField('จำนวนรอบที่ถูก', default=0)
    current_round = models.PositiveSmallIntegerField('รอบปัจจุบัน (Real-time)', default=1)
    total_rounds = models.PositiveSmallIntegerField('จำนวนรอบทั้งหมด', default=7)
    status = models.CharField(
        'สถานะ', max_length=20,
        choices=[('online', 'ออนไลน์ (พร้อมเล่น)'), ('playing', 'กำลังทำ'), ('completed', 'เสร็จแล้ว')],
        default='completed'
    )
    algorithm_detail = models.CharField('รายละเอียดอัลกอริทึม (เช่น BUB 7 L)', max_length=100, blank=True)
    is_exam_mode = models.BooleanField('ทำในโหมดสอบ', default=False, db_index=True)
    is_top_rewarded = models.BooleanField('ได้รับคะแนนบวก Top X แล้ว', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'sorting_scores'
        ordering = ['-score', 'time_used', 'attempts']
        verbose_name = 'คะแนนกิจกรรมเรียงลำดับ'
        verbose_name_plural = 'คะแนนกิจกรรมเรียงลำดับ'

    def save(self, *args, **kwargs):
        if self.status == 'completed' and self.cumulative_score == 0.0 and self.score != 0.0 and self.play_round_no == 1:
            self.cumulative_score = self.score
        super().save(*args, **kwargs)


class SortingExamRecord(models.Model):
    """สถิติรอบการสอบเรียงลำดับ (เปิดสอบเมื่อกด PLAY, จบการสอบเก็บสถิติ, ปิด/เปิดสอบซ้ำ, หรือลบได้)"""
    STATUS_ACTIVE = 'active'
    STATUS_PAUSED = 'paused'
    STATUS_FINISHED = 'finished'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'กำลังสอบ'),
        (STATUS_PAUSED, 'ปิดรับชั่วคราว'),
        (STATUS_FINISHED, 'จบการสอบแล้ว'),
    ]

    activity = models.ForeignKey(SortingActivity, on_delete=models.CASCADE, related_name='exam_records')
    session_no = models.PositiveIntegerField('รอบสอบที่', default=1, db_index=True)
    title = models.CharField('ชื่อรอบการสอบ', max_length=200)
    room_filter = models.CharField('ห้องที่สอบ', max_length=200, blank=True, default='')
    room_label = models.CharField('ชื่อห้องที่สอบ', max_length=200, blank=True, default='')
    sort_type = models.CharField('รูปแบบการเรียง', max_length=30, default='bubble')
    sort_order = models.CharField('ทิศทาง', max_length=10, default='asc')
    unit_count = models.PositiveSmallIntegerField('จำนวนตัวเลข', default=7)
    status = models.CharField('สถานะรอบสอบ', max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    participant_count = models.PositiveIntegerField('จำนวนผู้เข้าสอบ', default=0)
    avg_score = models.FloatField('คะแนนเฉลี่ย', default=0.0)
    max_score = models.FloatField('คะแนนสูงสุด', default=0.0)
    stats_json = models.TextField('รายละเอียดสถิติและอันดับ (JSON)', default='[]', blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField('เวลาจบการสอบ', null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        db_table = 'sorting_exam_records'
        ordering = ['-started_at', '-id']

    def get_stats_list(self):
        try:
            data = json.loads(self.stats_json or '[]')
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def __str__(self):
        return f'{self.title} (รอบ #{self.session_no} - {self.room_label or "ทุกห้อง"})'


# =============================================================================
# ระบบเกม Bingo
# ตาราง bingo_rooms, bingo_cards, bingo_drawn_numbers, bingo_winners ตามสเปกข้อ 5
# =============================================================================
class BingoRoom(models.Model):
    STATUS_WAITING = 'waiting'
    STATUS_OPEN = 'open'
    STATUS_PLAYING = 'playing'
    STATUS_FINISHED = 'finished'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_OPEN, 'เปิดให้นักเรียนจัดตาราง & เล่นเกม'),
        (STATUS_PLAYING, 'กำลังออกเลข Bingo'),
        (STATUS_FINISHED, 'มีผู้ชนะแล้ว / จบรอบ'),
        (STATUS_CLOSED, 'ปิดห้อง'),
    ]

    title = models.CharField('ชื่อห้อง/รอบ Bingo', max_length=200)
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    classroom = models.ForeignKey(Classroom, null=True, blank=True, on_delete=models.SET_NULL, related_name='bingo_rooms', verbose_name='เฉพาะห้องเรียน (เว้นว่าง=เล่นพร้อมกันทั้งระดับสายชั้น)')
    is_grade_wide = models.BooleanField('เล่นพร้อมกันทั้งระดับสายชั้น (ทุกห้อง)', default=True)
    continuous_play = models.BooleanField('หาผู้ชนะแต่ละห้องไปเรื่อยๆ ไม่หยุดเล่นจนกว่าจะกดเริ่มใหม่', default=True)
    group_filter = models.CharField('กลุ่มเรียน (เว้นว่าง=ทุกกลุ่ม)', max_length=50, blank=True)
    size = models.PositiveSmallIntegerField('ขนาดตาราง N×N (กำหนดเองได้ เช่น 3 ถึง 10)', default=5)
    number_range = models.CharField('ช่วงตัวเลข (เช่น 1-36 หรือ 1-50)', max_length=30, default='1-36')
    win_patterns = models.CharField(
        'รูปแบบการชนะ (horizontal,vertical,diagonal,all,four_corners)',
        max_length=120, default='all'
    )
    special_corner_center_rule = models.CharField(
        'เงื่อนไขพิเศษตำแหน่งมุมและตรงกลาง', max_length=30,
        choices=[
            ('system_auto', 'ระบบสุ่ม/ล็อกเลขตำแหน่งมุมและตรงกลางให้อัตโนมัติ'),
            ('even_corners_odd_center', 'มุมต้องเป็นเลขคู่ ตรงกลางต้องเป็นเลขคี่'),
            ('student_fill_protected', 'นักเรียนเลือกใส่เองได้ แต่ห้ามโหวตเลือกออกเลขในโหมดนักเรียนเลือก'),
        ],
        default='system_auto'
    )
    draw_condition = models.CharField(
        'เงื่อนไขการสุ่มมีเงื่อนไข', max_length=40,
        choices=[
            ('normal', 'สุ่มปกติทุกตัวเลขในช่วง'),
            ('no_even', 'ไม่ออกเลขคู่ (ออกเฉพาะเลขคี่)'),
            ('no_odd', 'ไม่ออกเลขคี่ (ออกเฉพาะเลขคู่)'),
            ('only_low_half', 'ออกเฉพาะครึ่งแรกของช่วงตัวเลข'),
            ('only_high_half', 'ออกเฉพาะครึ่งหลังของช่วงตัวเลข'),
        ],
        default='normal'
    )
    allow_student_pick = models.BooleanField('เปิดโหมดให้นักเรียนกดเลือกตัวเลขได้ (พร้อม Guard ตรวจสอบ)', default=True)
    active_picker_student = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='active_bingo_picker_rooms', verbose_name='นักเรียนที่ได้รับสิทธิ์ออกเลข'
    )
    active_picker_room_code = models.CharField('ห้องของนักเรียนที่ได้รับสิทธิ์ออกเลข', max_length=20, blank=True, default='')
    active_picker_granted_at = models.DateTimeField('เวลาที่ครูให้สิทธิ์ออกเลข', null=True, blank=True)
    active_picker_accepted = models.BooleanField('นักเรียนกดรับสิทธิ์ออกเลขแล้ว', default=False)
    last_picker_event = models.CharField('สถานะการเลือก/สละสิทธิ์ล่าสุด', max_length=255, blank=True, default='')
    status = models.CharField('สถานะห้อง', max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_bingo_rooms')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bingo_rooms'
        ordering = ['-created_at']
        verbose_name = 'ห้องเกม Bingo'
        verbose_name_plural = 'ห้องเกม Bingo'

    def __str__(self):
        return f'{self.title} ({self.size}x{self.size}, ช่วง {self.number_range})'

    def get_min_max_range(self):
        try:
            parts = self.number_range.replace('–', '-').split('-')
            mn = int(parts[0].strip())
            mx = int(parts[1].strip())
            if mx < mn + (self.size * self.size) - 1:
                mx = mn + (self.size * self.size) + 5
            return mn, mx
        except Exception:
            return 1, max(36, self.size * self.size + 5)

    def get_corner_and_center_indices(self):
        """คืนชุด index (0..N*N-1) ที่เป็นตำแหน่งมุมทั้ง 4 และตำแหน่งตรงกลาง"""
        n = int(self.size or 5)
        corners = {0, n - 1, n * (n - 1), n * n - 1}
        centers = set()
        if n % 2 == 1:
            mid = n // 2
            centers.add(mid * n + mid)
        else:
            m1, m2 = (n // 2) - 1, (n // 2)
            for r in (m1, m2):
                for c in (m1, m2):
                    centers.add(r * n + c)
        return corners, centers, (corners | centers)


class BingoCard(models.Model):
    """ตาราง bingo_cards ตามสเปกข้อ 5"""
    room = models.ForeignKey(BingoRoom, on_delete=models.CASCADE, related_name='cards')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bingo_cards')
    card_data = models.TextField('ข้อมูลตัวเลขในตาราง (JSON Array ขนาด N*N)')
    is_locked = models.BooleanField('ยืนยันตารางแล้ว', default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bingo_cards'
        unique_together = [('room', 'student')]
        ordering = ['created_at']

    def get_grid_list(self):
        try:
            data = json.loads(self.card_data)
            if isinstance(data, list):
                return [int(x) for x in data]
        except Exception:
            pass
        return []


class BingoDrawnNumber(models.Model):
    """ตาราง bingo_drawn_numbers ตามสเปกข้อ 5"""
    MODE_NORMAL = 'normal'
    MODE_CONDITIONAL = 'conditional'
    MODE_STUDENT = 'student_pick'
    MODE_MANUAL = 'teacher_manual'
    MODE_CHOICES = [
        (MODE_NORMAL, 'สุ่มแบบปกติ'),
        (MODE_CONDITIONAL, 'สุ่มแบบมีเงื่อนไข'),
        (MODE_STUDENT, 'นักเรียนเลือกตัวเลข'),
        (MODE_MANUAL, 'ผู้ดูแลระบุตัวเลข'),
    ]

    room = models.ForeignKey(BingoRoom, on_delete=models.CASCADE, related_name='drawn_numbers')
    target_room_code = models.CharField('ห้องที่ออกเลข (all=ทั้งระดับ, 01-14=รายห้อง)', max_length=20, default='all', db_index=True)
    number = models.IntegerField('ตัวเลขที่ออก', db_index=True)
    drawn_by_mode = models.CharField('รูปแบบการออกเลข', max_length=25, choices=MODE_CHOICES, default=MODE_NORMAL)
    drawn_by_student = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='picked_bingo_numbers', verbose_name='นักเรียนที่เลือก'
    )
    hit_count = models.PositiveIntegerField('จำนวนนักเรียนที่ได้รับเลขนี้', default=0)
    eligible_cards_count = models.PositiveIntegerField('จำนวนตารางในห้องที่เลือกตอนออกเลข', default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bingo_drawn_numbers'
        ordering = ['created_at', 'id']
        unique_together = [('room', 'target_room_code', 'number')]


class BingoWinner(models.Model):
    """ตาราง bingo_winners ตามสเปกข้อ 5"""
    room = models.ForeignKey(BingoRoom, on_delete=models.CASCADE, related_name='winners')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bingo_wins')
    room_code = models.CharField('รหัสห้องตอนชนะ', max_length=20, blank=True, db_index=True)
    room_rank_order = models.PositiveSmallIntegerField('อันดับที่ชนะในห้อง', default=1)
    win_pattern = models.CharField('รูปแบบที่ชนะ (เช่น แนวนอนแถวที่ 1, แนวทแยง)', max_length=120)
    winning_number = models.IntegerField('เลขที่ทำให้ชนะ', default=0)
    rank_order = models.PositiveSmallIntegerField('อันดับที่ชนะ', default=1)
    score_awarded = models.FloatField('คะแนนบวกที่ได้รับ', default=2.0)
    won_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bingo_winners'
        unique_together = [('room', 'student')]
        ordering = ['won_at', 'rank_order']


# =============================================================================
# ระบบ Debsirin Lotto
# ตาราง lotto_sessions และ lotto_results ตามสเปกข้อ 5
# =============================================================================
class LottoSession(models.Model):
    title = models.CharField('ชื่อรอบการสุ่ม Lotto', max_length=200, default='ID LOTTO')
    academic_year = models.CharField('ปีการศึกษา', max_length=20, default='2569', db_index=True)
    classroom = models.ForeignKey(
        Classroom, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='lotto_sessions', verbose_name='เลือกห้องเรียน (เว้นว่าง=ทุกห้อง)'
    )
    group_filter = models.CharField('กรองเฉพาะกลุ่ม (เว้นว่าง=ทุกกลุ่ม)', max_length=50, blank=True)
    winner_count = models.PositiveSmallIntegerField('จำนวนผู้โชคดีที่ต้องการสุ่ม', default=5)
    only_never_picked_in_session = models.BooleanField('สุ่มเฉพาะคนที่ยังไม่เคยถูกสุ่มในรอบ/คาบนี้', default=True)
    bonus_points = models.FloatField('คะแนนบวกเมื่อถูกสุ่ม', default=1.0)
    filter_submitted_activity = models.BooleanField('เฉพาะคนที่ส่งงานกิจกรรม', default=False)
    filter_activity = models.ForeignKey(
        LearningActivity, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='กิจกรรมที่ใช้กรอง'
    )
    filter_exclude_last_x = models.BooleanField('เฉพาะคนที่ไม่ได้เป็น X คนสุดท้าย', default=False)
    filter_last_x_count = models.PositiveSmallIntegerField('จำนวน X คนสุดท้ายที่ยกเว้น', default=5)
    filter_material_checked = models.BooleanField('เฉพาะคนที่ตรวจสอบแล้ว', default=False)
    filter_presented_activity = models.BooleanField('เฉพาะคนที่ได้นำเสนอกิจกรรม', default=False)
    filter_check_date = models.DateField('วันที่อ้างอิงการตรวจสอบ/เข้าสาย', null=True, blank=True)
    is_open = models.BooleanField('เปิดแสดงผลให้นักเรียนดูสด', default=True)
    is_finalized = models.BooleanField('สรุปคะแนนรอบนี้แล้ว', default=False)
    finalized_at = models.DateTimeField('เวลาสรุปคะแนนล่าสุด', null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='created_lotto_sessions')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lotto_sessions'
        ordering = ['-created_at']
        verbose_name = 'รอบการสุ่ม Lotto'
        verbose_name_plural = 'รอบการสุ่ม Lotto'

    def __str__(self):
        room_str = self.classroom.name if self.classroom else 'ทุกห้อง'
        return f'{self.title} ({room_str})'


class LottoResult(models.Model):
    """ตาราง lotto_results ตามสเปกข้อ 5"""
    session = models.ForeignKey(LottoSession, on_delete=models.CASCADE, related_name='results')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='lotto_results')
    order_index = models.PositiveSmallIntegerField('ลำดับที่ถูกสุ่ม', default=1)
    removed = models.BooleanField('ถูกลบออกจากรายการสุ่ม', default=False, db_index=True)
    removed_reason = models.CharField('เหตุผลที่ลบออก (เช่น ไม่อยู่ในห้อง / ลาป่วย)', max_length=255, blank=True)
    points_awarded = models.FloatField('คะแนนบวกที่ได้รับ', default=0.0)
    is_awarded = models.BooleanField('สรุปให้คะแนนแล้ว', default=False, db_index=True)
    awarded_at = models.DateTimeField('เวลาที่สรุปให้คะแนน', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lotto_results'
        ordering = ['-created_at', '-order_index']
        verbose_name = 'ผลการสุ่ม Lotto'
        verbose_name_plural = 'ผลการสุ่ม Lotto'


class ScoreRankTier(models.Model):
    """ระดับถ้วยรางวัลตามช่วงคะแนนรวมทั้ง 2 ช่วง คิดเป็น % เต็ม 100% (แก้ไขเกณฑ์ % และอัปโหลดรูปถ้วยรางวัลเองได้)"""
    OP_GTE = 'gte'
    OP_GT = 'gt'
    OP_CHOICES = [
        (OP_GTE, '>='),
        (OP_GT, '>'),
    ]

    order_no = models.PositiveSmallIntegerField('ลำดับระดับ', default=1, db_index=True)
    code = models.CharField('รหัสระดับ', max_length=30, unique=True)
    name = models.CharField('ชื่อระดับ', max_length=60)
    operator = models.CharField('เงื่อนไขเปรียบเทียบ', max_length=10, choices=OP_CHOICES, default=OP_GT)
    min_percent = models.FloatField('เกณฑ์เปอร์เซ็นต์ขั้นต่ำ (%)', default=0.0)
    color_hex = models.CharField('สีประจำระดับ', max_length=20, default='#64748b')
    default_icon_path = models.CharField('พาธรูปถ้วยรางวัลเริ่มต้น', max_length=200, default='/static/ranks/trophy_member.png')
    custom_icon = models.ImageField('อัปโหลดรูปถ้วยรางวัลแทน', upload_to='rank_trophies/', null=True, blank=True)

    class Meta:
        db_table = 'score_rank_tiers'
        ordering = ['order_no', 'min_percent', 'id']
        verbose_name = 'ระดับตามช่วงคะแนน'
        verbose_name_plural = 'ระดับตามช่วงคะแนน'

    def __str__(self):
        return f'{self.name} ({self.condition_display})'

    @property
    def operator_symbol(self):
        return '>=' if self.operator == self.OP_GTE else '>'

    @property
    def condition_display(self):
        val_txt = f'{self.min_percent:g}'
        return f'{self.operator_symbol} {val_txt}%'

    @property
    def icon_url(self):
        if self.custom_icon:
            try:
                return self.custom_icon.url
            except Exception:
                pass
        return self.default_icon_path or '/static/ranks/trophy_member.png'

    def matches(self, percent_val):
        p = float(percent_val or 0.0)
        thr = float(self.min_percent or 0.0)
        if self.operator == self.OP_GTE:
            return p >= thr - 1e-9
        return p > thr + 1e-9
