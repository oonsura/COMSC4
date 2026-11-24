"""
Django settings for comsc4 (ระบบจัดการการเรียนการสอนวิทยาการคำนวณ โรงเรียนเทพศิรินทร์)
ครอบคลุมโมดูล A-H:
- โมดูล A: ระบบล็อกอิน Google OAuth (@debsirin.ac.th), 1-Session Guard (เลือกเตะเครื่องเก่าหรือคงเครื่องเดิม), โปรไฟล์หมุน/Crop รูป
- โมดูล B: เนื้อหาบทเรียน และแบบทดสอบ AI (Claude API / Gemini API / Smart Generator)
- โมดูล C: กิจกรรมการเรียนรู้ ส่งผลงาน และสุ่มนำเสนอผลงานพร้อมรูปประจำตัว
- โมดูล D: เช็คชื่อด้วยโค้ด + ตรวจ IP + ผูกแผนผังที่นั่งห้องเรียน
- โมดูล E: ระบบคะแนนรวมและกฎคะแนนอัตโนมัติ (Sorting Top X, Bingo Winner, Lotto Pick)
- โมดูล F: ระบบกิจกรรมเรียงลำดับ (dsbasicsorting) พร้อมโหมดสอบและ Real-time Progress
- โมดูล G: ระบบเกม Bingo (สร้างตาราง, สุ่มปกติ/มีเงื่อนไข/นักเรียนเลือกพร้อม Guard ไม่ให้มุม-กลาง-Bingo ทันที, สถิติ)
- โมดูล H: ระบบ Debsirin Lotto (สุ่มเลขประจำตัว 5 หลัก สล็อตแมชชีน, ลบรายชื่อพร้อมเหตุผล, ประวัติการสุ่ม)
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except ImportError:
    pass


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ('1', 'true', 'yes', 'on')


SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-comsc4-debsirin-secret-key-2026-!@#',
)
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',') if h.strip()]
CSRF_TRUSTED_ORIGINS = [h.strip() for h in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'core.apps.CoreConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.SingleSessionEnforcementMiddleware',
]

ROOT_URLCONF = 'comsc4.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'comsc4.wsgi.application'
ASGI_APPLICATION = 'comsc4.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {'timeout': 25},
    }
}

AUTH_USER_MODEL = 'core.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 4}},
]

LOGIN_URL = 'core:login'
LOGIN_REDIRECT_URL = 'core:home'
LOGOUT_REDIRECT_URL = 'core:login'

SESSION_COOKIE_AGE = 86400
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'SAMEORIGIN'

LANGUAGE_CODE = 'th'
TIME_ZONE = 'Asia/Bangkok'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

BACKUP_DIR = BASE_DIR / 'backups'
AUDIT_LOG_DIR = BASE_DIR / 'audit_logs'

DATA_UPLOAD_MAX_NUMBER_FIELDS = 20000
FILE_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = '1234'
