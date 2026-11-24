from django.contrib import admin
from .models import (
    User, UserProfile, UserSession, SystemSetting, LabRoomLayout, Classroom,
    LessonContent, AIQuiz, AIQuizQuestion, AIQuizAttempt,
    LearningActivity, ActivitySubmission, AttendanceSession, AttendanceRecord,
    ScoreRule, ScoreLog, SortingActivity, SortingScore,
    BingoRoom, BingoCard, BingoDrawnNumber, BingoWinner,
    LottoSession, LottoResult,
)

admin.site.register(User)
admin.site.register(UserProfile)
admin.site.register(UserSession)
admin.site.register(SystemSetting)
admin.site.register(LabRoomLayout)
admin.site.register(Classroom)
admin.site.register(LessonContent)
admin.site.register(AIQuiz)
admin.site.register(AIQuizQuestion)
admin.site.register(AIQuizAttempt)
admin.site.register(LearningActivity)
admin.site.register(ActivitySubmission)
admin.site.register(AttendanceSession)
admin.site.register(AttendanceRecord)
admin.site.register(ScoreRule)
admin.site.register(ScoreLog)
admin.site.register(SortingActivity)
admin.site.register(SortingScore)
admin.site.register(BingoRoom)
admin.site.register(BingoCard)
admin.site.register(BingoDrawnNumber)
admin.site.register(BingoWinner)
admin.site.register(LottoSession)
admin.site.register(LottoResult)
