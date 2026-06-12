from django.contrib import admin
from django.db.models import Avg

from .models import (
    ClientAIAnalysis,
    ClubReview,
    ClientProfile,
    CompletedExercise,
    CompletedWorkout,
    Exercise,
    Meal,
    MealItem,
    Membership,
    ManagementAIAnalysis,
    NutritionTarget,
    ScheduleSlot,
    Product,
    ProgressRecord,
    TrainerComment,
    TrainerAIAnalysis,
    TrainerProfile,
    TrainerReview,
    TrainingAppointment,
    WeightForecastAnalysis,
    WorkoutDay,
    WorkoutExercise,
    WorkoutPlan,
)


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "goal", "trainer", "status")
    list_filter = ("goal", "status", "trainer")
    search_fields = ("full_name", "phone", "user__email")


@admin.register(ClubReview)
class ClubReviewAdmin(admin.ModelAdmin):
    list_display = ("client", "rating", "title", "is_published", "created_at")
    list_filter = ("rating", "is_published", "created_at")
    search_fields = ("client__full_name", "title", "text")


@admin.register(TrainerReview)
class TrainerReviewAdmin(admin.ModelAdmin):
    list_display = ("trainer", "client", "rating", "title", "is_published", "created_at")
    list_filter = ("rating", "is_published", "trainer", "created_at")
    search_fields = ("trainer__full_name", "client__full_name", "title", "text")


@admin.register(ManagementAIAnalysis)
class ManagementAIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("requested_by", "status", "provider", "model", "created_at", "finished_at")
    list_filter = ("status", "provider", "created_at")
    search_fields = ("requested_by__username", "provider", "model", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "payload", "result")


@admin.register(TrainerAIAnalysis)
class TrainerAIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("trainer", "requested_by", "status", "provider", "model", "created_at", "finished_at")
    list_filter = ("status", "provider", "created_at", "trainer")
    search_fields = ("trainer__full_name", "requested_by__username", "provider", "model", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "payload", "result")


@admin.register(ClientAIAnalysis)
class ClientAIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("client", "requested_by", "status", "provider", "model", "created_at", "finished_at")
    list_filter = ("status", "provider", "created_at", "client")
    search_fields = ("client__full_name", "requested_by__username", "provider", "model", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "payload", "result")


@admin.register(WeightForecastAnalysis)
class WeightForecastAnalysisAdmin(admin.ModelAdmin):
    list_display = ("client", "requested_by", "status", "provider", "model", "created_at", "finished_at")
    list_filter = ("status", "provider", "created_at", "client")
    search_fields = ("client__full_name", "requested_by__username", "provider", "model", "error")
    readonly_fields = ("created_at", "started_at", "finished_at", "payload", "result")


@admin.register(ScheduleSlot)
class ScheduleSlotAdmin(admin.ModelAdmin):
    list_display = ("trainer", "start_at", "end_at", "is_active", "created_by")
    list_filter = ("is_active", "trainer", "start_at")
    search_fields = ("trainer__full_name", "note")


@admin.register(TrainingAppointment)
class TrainingAppointmentAdmin(admin.ModelAdmin):
    list_display = ("client", "trainer_name", "start_at", "status", "booked_at", "cancelled_at")
    list_filter = ("status", "slot__trainer", "slot__start_at")
    search_fields = ("client__full_name", "slot__trainer__full_name", "cancel_reason")

    @admin.display(description="Тренер")
    def trainer_name(self, obj):
        return obj.slot.trainer

    @admin.display(description="Начало")
    def start_at(self, obj):
        return obj.slot.start_at


@admin.register(TrainerProfile)
class TrainerProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "specialization", "phone", "status", "clients_total", "average_rating", "reviews_count")
    search_fields = ("full_name", "specialization", "phone")

    @admin.display(description="Средняя оценка")
    def average_rating(self, obj):
        value = obj.trainer_reviews.filter(is_published=True).aggregate(avg=Avg("rating"))["avg"]
        return round(value, 1) if value else "-"

    @admin.display(description="Оценок")
    def reviews_count(self, obj):
        return obj.trainer_reviews.filter(is_published=True).count()


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("client", "type", "start_date", "end_date", "visits_left", "status")
    list_filter = ("status", "type")
    search_fields = ("client__full_name",)


@admin.register(Exercise)
class ExerciseAdmin(admin.ModelAdmin):
    list_display = ("name", "muscle_group", "exercise_type", "is_active")
    list_filter = ("muscle_group", "exercise_type", "is_active")
    search_fields = ("name", "technique_description")


class WorkoutExerciseInline(admin.TabularInline):
    model = WorkoutExercise
    extra = 0


@admin.register(WorkoutDay)
class WorkoutDayAdmin(admin.ModelAdmin):
    list_display = ("title", "workout_plan", "day_number")
    inlines = [WorkoutExerciseInline]


@admin.register(WorkoutPlan)
class WorkoutPlanAdmin(admin.ModelAdmin):
    list_display = ("title", "client", "trainer", "goal", "status", "start_date", "end_date")
    list_filter = ("goal", "status", "is_template")
    search_fields = ("title", "client__full_name", "trainer__full_name")


@admin.register(CompletedWorkout)
class CompletedWorkoutAdmin(admin.ModelAdmin):
    list_display = ("client", "workout_day", "completed_at", "mood")
    list_filter = ("completed_at",)
    search_fields = ("client__full_name", "comment")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "calories_per_100g", "protein_per_100g", "fat_per_100g", "carbs_per_100g", "is_active")
    search_fields = ("name",)


class MealItemInline(admin.TabularInline):
    model = MealItem
    extra = 0


@admin.register(Meal)
class MealAdmin(admin.ModelAdmin):
    list_display = ("client", "meal_date", "meal_type")
    list_filter = ("meal_date", "meal_type")
    inlines = [MealItemInline]


admin.site.register(CompletedExercise)
admin.site.register(NutritionTarget)
admin.site.register(ProgressRecord)
admin.site.register(TrainerComment)

# Register your models here.
