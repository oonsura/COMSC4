"""
Middleware ควบคุม 1 Session ต่อบัญชี (A.1 & A.2)
- นักเรียน 1 คนใช้ได้ 1 session ในเวลาเดียวกัน
- หากล็อกอินซ้ำจากอุปกรณ์อื่น และผู้ใช้อุปกรณ์ใหม่เลือก "ใช้งานต่อที่อุปกรณ์ใหม่"
  ระบบจะอัปเดต current_session_key เป็นของเครื่องใหม่ และเตะเครื่องเดิมออกทันทีพร้อมแจ้งเตือน
"""
from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from .models import SystemSetting, UserSession


class SingleSessionEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            setting = SystemSetting.get()
            enforce = (
                (user.is_student_role and setting.enforce_single_session_student)
                or (user.is_teacher_role and setting.enforce_single_session_staff)
            )
            current_key = request.session.session_key
            if enforce and user.current_session_key and current_key and user.current_session_key != current_key:
                UserSession.objects.filter(user=user, session_token=current_key).update(is_active=False)
                logout(request)
                messages.warning(
                    request,
                    'บัญชีของคุณถูกเข้าสู่ระบบและยืนยันใช้งานต่อจากอุปกรณ์อื่น ระบบจึงออกจากระบบบนอุปกรณ์นี้'
                )
                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
                    return JsonResponse({'ok': False, 'error': 'session_kicked', 'message': 'ถูกเข้าสู่ระบบจากอุปกรณ์อื่น'}, status=401)
                return redirect('core:login')

            if current_key:
                UserSession.objects.filter(user=user, session_token=current_key, is_active=True).update(
                    ip_address=get_client_ip(request),
                    last_active=timezone.now(),
                )
        return self.get_response(request)


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


def get_device_summary(request):
    ua = request.META.get('HTTP_USER_AGENT', '')
    ip = get_client_ip(request)
    browser = 'Web Browser'
    os_name = 'Device'
    if 'Edg/' in ua:
        browser = 'Microsoft Edge'
    elif 'Chrome/' in ua:
        browser = 'Google Chrome'
    elif 'Firefox/' in ua:
        browser = 'Mozilla Firefox'
    elif 'Safari/' in ua:
        browser = 'Safari'

    if 'Windows' in ua:
        os_name = 'Windows PC'
    elif 'Android' in ua:
        os_name = 'Android'
    elif 'iPhone' in ua or 'iPad' in ua:
        os_name = 'iOS'
    elif 'Macintosh' in ua:
        os_name = 'Mac'
    return f'{browser} บน {os_name} (IP: {ip})'
