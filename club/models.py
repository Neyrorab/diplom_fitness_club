from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Role:
    ADMIN = "admin"
    TRAINER = "trainer"
    CLIENT = "client"

    CHOICES = (
        (ADMIN, "Администратор"),
        (TRAINER, "Тренер"),
        (CLIENT, "Клиент"),
    )


class ClientStatus(models.TextChoices):
    ACTIVE = "active", "Активен"
    PAUSED = "paused", "Пауза"
    ARCHIVED = "archived", "Архив"


class Goal(models.TextChoices):
    WEIGHT_LOSS = "weight_loss", "Похудение"
    MUSCLE_GAIN = "muscle_gain", "Набор мышечной массы"
    MAINTENANCE = "maintenance", "Поддержание формы"
    ENDURANCE = "endurance", "Улучшение выносливости"
    RECOVERY = "recovery", "Восстановление"


class TrainerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="trainer_profile")
    full_name = models.CharField("ФИО", max_length=160)
    phone = models.CharField("Телефон", max_length=32)
    specialization = models.CharField("Специализация", max_length=160)
    experience_years = models.PositiveSmallIntegerField("Стаж, лет", default=0)
    status = models.CharField("Статус", max_length=16, choices=ClientStatus.choices, default=ClientStatus.ACTIVE)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "профиль тренера"
        verbose_name_plural = "профили тренеров"

    def __str__(self):
        return self.full_name

    @property
    def clients_total(self):
        return self.clients.count()


class ClientProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client_profile")
    full_name = models.CharField("ФИО", max_length=160)
    phone = models.CharField("Телефон", max_length=32, blank=True)
    birth_date = models.DateField("Дата рождения", null=True, blank=True)
    height = models.PositiveSmallIntegerField("Рост, см", null=True, blank=True)
    goal = models.CharField("Цель", max_length=32, choices=Goal.choices, default=Goal.MAINTENANCE)
    training_level = models.CharField("Уровень подготовки", max_length=80, blank=True)
    health_limitations = models.TextField("Ограничения по здоровью", blank=True)
    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.SET_NULL,
        related_name="clients",
        verbose_name="Тренер",
        null=True,
        blank=True,
    )
    status = models.CharField("Статус", max_length=16, choices=ClientStatus.choices, default=ClientStatus.ACTIVE)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "профиль клиента"
        verbose_name_plural = "профили клиентов"

    def __str__(self):
        return self.full_name

    def current_membership(self):
        today = timezone.localdate()
        return self.memberships.filter(start_date__lte=today).order_by("-end_date", "-created_at").first()

    def active_plan(self):
        return self.workout_plans.filter(status=PlanStatus.ACTIVE, is_template=False).order_by("-start_date").first()

    def last_workout(self):
        return self.completed_workouts.order_by("-completed_at").first()

    def last_workout_at(self):
        workout = self.last_workout()
        return workout.completed_at if workout else None

    def is_low_activity(self):
        last = self.last_workout_at()
        if not last:
            return True
        return last.date() < timezone.localdate() - timedelta(days=14)


class ClubReview(models.Model):
    RATING_CHOICES = (
        (5, "5 - отлично"),
        (4, "4 - хорошо"),
        (3, "3 - нормально"),
        (2, "2 - есть замечания"),
        (1, "1 - плохо"),
    )

    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="club_reviews", verbose_name="Клиент")
    rating = models.PositiveSmallIntegerField(
        "Оценка",
        choices=RATING_CHOICES,
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    title = models.CharField("Заголовок", max_length=120)
    text = models.TextField("Текст отзыва")
    is_published = models.BooleanField("Опубликован", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлен", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "отзыв о клубе"
        verbose_name_plural = "отзывы о клубе"

    def __str__(self):
        return f"{self.client} - {self.rating}/5"

    @property
    def rating_percent(self):
        return self.rating * 20


class TrainerReview(models.Model):
    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="trainer_reviews",
        verbose_name="Тренер",
    )
    client = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="trainer_reviews",
        verbose_name="Клиент",
    )
    rating = models.PositiveSmallIntegerField(
        "Оценка",
        choices=ClubReview.RATING_CHOICES,
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    title = models.CharField("Заголовок", max_length=120)
    text = models.TextField("Текст отзыва")
    is_published = models.BooleanField("Опубликован", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлен", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "оценка тренера"
        verbose_name_plural = "оценки тренеров"

    def __str__(self):
        return f"{self.trainer} - {self.client} - {self.rating}/5"

    @property
    def rating_percent(self):
        return self.rating * 20


class ManagementAIAnalysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Анализируется"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="management_ai_analyses",
        verbose_name="Запросил",
    )
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.QUEUED)
    provider = models.CharField("Провайдер", max_length=40)
    model = models.CharField("Модель", max_length=120)
    payload = models.JSONField("Метрики", default=dict)
    result = models.JSONField("Результат", null=True, blank=True)
    error = models.TextField("Ошибка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    started_at = models.DateTimeField("Начат", null=True, blank=True)
    finished_at = models.DateTimeField("Завершен", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ИИ-анализ управления"
        verbose_name_plural = "ИИ-анализы управления"

    def __str__(self):
        return f"{self.requested_by} - {self.get_status_display()} - {self.created_at:%d.%m.%Y %H:%M}"

    @property
    def is_pending(self):
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class TrainerAIAnalysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Анализируется"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="trainer_ai_analyses",
        verbose_name="Запросил",
    )
    trainer = models.ForeignKey(
        TrainerProfile,
        on_delete=models.CASCADE,
        related_name="ai_analyses",
        verbose_name="Тренер",
    )
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.QUEUED)
    provider = models.CharField("Провайдер", max_length=40)
    model = models.CharField("Модель", max_length=120)
    payload = models.JSONField("Данные тренера", default=dict)
    result = models.JSONField("Результат", null=True, blank=True)
    error = models.TextField("Ошибка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    started_at = models.DateTimeField("Начат", null=True, blank=True)
    finished_at = models.DateTimeField("Завершен", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ИИ-ассистент тренера"
        verbose_name_plural = "ИИ-ассистенты тренера"

    def __str__(self):
        return f"{self.trainer} - {self.get_status_display()} - {self.created_at:%d.%m.%Y %H:%M}"

    @property
    def is_pending(self):
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class ClientAIAnalysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Анализируется"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="client_ai_analyses",
        verbose_name="Запросил",
    )
    client = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="ai_analyses",
        verbose_name="Клиент",
    )
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.QUEUED)
    provider = models.CharField("Провайдер", max_length=40)
    model = models.CharField("Модель", max_length=120)
    payload = models.JSONField("Данные клиента", default=dict)
    result = models.JSONField("Результат", null=True, blank=True)
    error = models.TextField("Ошибка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    started_at = models.DateTimeField("Начат", null=True, blank=True)
    finished_at = models.DateTimeField("Завершен", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ИИ-коуч клиента"
        verbose_name_plural = "ИИ-коучи клиентов"

    def __str__(self):
        return f"{self.client} - {self.get_status_display()} - {self.created_at:%d.%m.%Y %H:%M}"

    @property
    def is_pending(self):
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class WeightForecastAnalysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Анализируется"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    requested_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="weight_forecast_analyses",
        verbose_name="Запросил",
    )
    client = models.ForeignKey(
        ClientProfile,
        on_delete=models.CASCADE,
        related_name="weight_forecasts",
        verbose_name="Клиент",
    )
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.QUEUED)
    provider = models.CharField("Провайдер", max_length=40)
    model = models.CharField("Модель", max_length=120)
    payload = models.JSONField("Данные прогноза", default=dict)
    result = models.JSONField("Прогноз", null=True, blank=True)
    error = models.TextField("Ошибка", blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    started_at = models.DateTimeField("Начат", null=True, blank=True)
    finished_at = models.DateTimeField("Завершен", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ИИ-прогноз веса"
        verbose_name_plural = "ИИ-прогнозы веса"

    def __str__(self):
        return f"{self.client} - {self.get_status_display()} - {self.created_at:%d.%m.%Y %H:%M}"

    @property
    def is_pending(self):
        return self.status in {self.Status.QUEUED, self.Status.RUNNING}


class AppointmentStatus(models.TextChoices):
    BOOKED = "booked", "Записано"
    CANCELLED = "cancelled", "Отменено"
    COMPLETED = "completed", "Проведено"


class ScheduleSlot(models.Model):
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.CASCADE, related_name="schedule_slots", verbose_name="Тренер")
    start_at = models.DateTimeField("Начало")
    end_at = models.DateTimeField("Окончание")
    note = models.CharField("Комментарий", max_length=240, blank=True)
    is_active = models.BooleanField("Активен", default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="created_schedule_slots",
        verbose_name="Создал",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["start_at"]
        verbose_name = "слот расписания"
        verbose_name_plural = "слоты расписания"

    def __str__(self):
        return f"{self.trainer} - {self.start_at:%d.%m.%Y %H:%M}"

    def clean(self):
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValidationError("Время окончания должно быть позже времени начала.")
        if self.start_at and self.start_at < timezone.now():
            raise ValidationError("Нельзя создать слот в прошлом.")
        if self.trainer_id and self.start_at and self.end_at and self.is_active:
            overlaps = ScheduleSlot.objects.filter(
                trainer=self.trainer,
                is_active=True,
                start_at__lt=self.end_at,
                end_at__gt=self.start_at,
            ).exclude(pk=self.pk)
            if overlaps.exists():
                raise ValidationError("У тренера уже есть слот в это время.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def booked_appointment(self):
        return self.appointments.filter(status=AppointmentStatus.BOOKED).select_related("client").first()

    @property
    def is_available(self):
        return self.is_active and self.start_at > timezone.now() and self.booked_appointment is None


class TrainingAppointment(models.Model):
    slot = models.ForeignKey(ScheduleSlot, on_delete=models.CASCADE, related_name="appointments", verbose_name="Слот")
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="training_appointments", verbose_name="Клиент")
    status = models.CharField("Статус", max_length=16, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED)
    booked_at = models.DateTimeField("Записан", auto_now_add=True)
    cancelled_at = models.DateTimeField("Отменен", null=True, blank=True)
    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="cancelled_training_appointments",
        verbose_name="Отменил",
        null=True,
        blank=True,
    )
    cancel_reason = models.CharField("Причина отмены", max_length=240, blank=True)

    class Meta:
        ordering = ["slot__start_at"]
        constraints = [
            models.UniqueConstraint(fields=["slot"], condition=Q(status=AppointmentStatus.BOOKED), name="unique_booked_appointment_per_slot")
        ]
        verbose_name = "запись на тренировку"
        verbose_name_plural = "записи на тренировки"

    def __str__(self):
        return f"{self.client} - {self.slot.start_at:%d.%m.%Y %H:%M}"

    def clean(self):
        if not self.slot_id or not self.client_id or self.status != AppointmentStatus.BOOKED:
            return
        if not self.slot.is_active:
            raise ValidationError("Этот слот недоступен для записи.")
        if self.slot.start_at <= timezone.now():
            raise ValidationError("Нельзя записаться на прошедшее время.")
        if self.client.trainer_id != self.slot.trainer_id:
            raise ValidationError("Клиент может записаться только к своему тренеру.")

        booked_in_slot = TrainingAppointment.objects.filter(slot=self.slot, status=AppointmentStatus.BOOKED).exclude(pk=self.pk)
        if booked_in_slot.exists():
            raise ValidationError("На это время уже есть запись.")

        client_overlap = TrainingAppointment.objects.filter(
            client=self.client,
            status=AppointmentStatus.BOOKED,
            slot__start_at__lt=self.slot.end_at,
            slot__end_at__gt=self.slot.start_at,
        ).exclude(pk=self.pk)
        if client_overlap.exists():
            raise ValidationError("У клиента уже есть запись на это время.")

    @property
    def can_cancel_by_client(self):
        return self.status == AppointmentStatus.BOOKED and self.slot.start_at - timezone.now() >= timedelta(hours=24)

    @property
    def can_complete(self):
        return self.status == AppointmentStatus.BOOKED and self.slot.start_at <= timezone.now()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def cancel(self, user, reason=""):
        self.status = AppointmentStatus.CANCELLED
        self.cancelled_by = user
        self.cancel_reason = reason
        self.cancelled_at = timezone.now()
        self.save(update_fields=["status", "cancelled_by", "cancel_reason", "cancelled_at"])


class MembershipStatus(models.TextChoices):
    ACTIVE = "active", "Активен"
    PLANNED = "planned", "Запланирован"
    EXPIRED = "expired", "Завершен"
    FROZEN = "frozen", "Заморожен"


class Membership(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="memberships", verbose_name="Клиент")
    type = models.CharField("Тип абонемента", max_length=120)
    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания")
    visits_total = models.PositiveIntegerField("Всего посещений", default=0)
    visits_left = models.PositiveIntegerField("Осталось посещений", default=0)
    status = models.CharField("Статус", max_length=16, choices=MembershipStatus.choices, default=MembershipStatus.ACTIVE)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["-end_date"]
        verbose_name = "абонемент"
        verbose_name_plural = "абонементы"

    def __str__(self):
        return f"{self.client} - {self.type}"

    def save(self, *args, **kwargs):
        self.clean()
        today = timezone.localdate()
        if self.status != MembershipStatus.FROZEN:
            if self.start_date > today:
                self.status = MembershipStatus.PLANNED
            elif self.end_date < today or self.visits_left == 0:
                self.status = MembershipStatus.EXPIRED
            else:
                self.status = MembershipStatus.ACTIVE
        super().save(*args, **kwargs)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("Дата окончания не может быть раньше даты начала.")
        if self.visits_left > self.visits_total:
            raise ValidationError("Остаток посещений не может быть больше общего количества.")

    @property
    def expires_soon(self):
        today = timezone.localdate()
        return self.status == MembershipStatus.ACTIVE and today <= self.end_date <= today + timedelta(days=7)

    @property
    def days_left(self):
        return max((self.end_date - timezone.localdate()).days, 0)


class Exercise(models.Model):
    name = models.CharField("Название", max_length=140, unique=True)
    muscle_group = models.CharField("Группа мышц", max_length=80)
    exercise_type = models.CharField("Тип упражнения", max_length=80)
    technique_description = models.TextField("Описание техники", blank=True)
    is_active = models.BooleanField("Активно", default=True)

    class Meta:
        ordering = ["muscle_group", "name"]
        verbose_name = "упражнение"
        verbose_name_plural = "упражнения"

    def __str__(self):
        return self.name


class PlanStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    ACTIVE = "active", "Активен"
    COMPLETED = "completed", "Завершен"


class WorkoutPlan(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="workout_plans", verbose_name="Клиент")
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.SET_NULL, related_name="workout_plans", verbose_name="Тренер", null=True)
    title = models.CharField("Название", max_length=160)
    goal = models.CharField("Цель", max_length=32, choices=Goal.choices)
    description = models.TextField("Описание", blank=True)
    start_date = models.DateField("Дата начала")
    end_date = models.DateField("Дата окончания", null=True, blank=True)
    status = models.CharField("Статус", max_length=16, choices=PlanStatus.choices, default=PlanStatus.ACTIVE)
    is_template = models.BooleanField("Шаблон", default=False)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "тренировочный план"
        verbose_name_plural = "тренировочные планы"

    def __str__(self):
        return self.title

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("Дата окончания плана не может быть раньше даты начала.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def completion_percent(self):
        total_days = self.days.count()
        if total_days == 0:
            return 0
        completed_days = CompletedWorkout.objects.filter(
            client=self.client,
            workout_day__workout_plan=self,
            exercises__is_completed=True,
        ).values("workout_day_id").distinct().count()
        return round(completed_days / total_days * 100)


class WorkoutDay(models.Model):
    workout_plan = models.ForeignKey(WorkoutPlan, on_delete=models.CASCADE, related_name="days", verbose_name="План")
    title = models.CharField("Название дня", max_length=120)
    day_number = models.PositiveSmallIntegerField("Номер дня", default=1)
    description = models.TextField("Описание", blank=True)

    class Meta:
        ordering = ["day_number", "id"]
        verbose_name = "тренировочный день"
        verbose_name_plural = "тренировочные дни"

    def __str__(self):
        return f"{self.workout_plan}: {self.title}"


class WorkoutExercise(models.Model):
    workout_day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name="exercises", verbose_name="Тренировочный день")
    exercise = models.ForeignKey(Exercise, on_delete=models.PROTECT, related_name="plan_entries", verbose_name="Упражнение")
    sets_count = models.PositiveSmallIntegerField("Подходы", default=3)
    reps_count = models.PositiveSmallIntegerField("Повторения", default=12)
    recommended_weight = models.DecimalField("Рекомендуемый вес", max_digits=6, decimal_places=1, default=0)
    rest_seconds = models.PositiveSmallIntegerField("Отдых, сек", default=60)
    comment = models.CharField("Комментарий", max_length=240, blank=True)
    order_number = models.PositiveSmallIntegerField("Порядок", default=1)

    class Meta:
        ordering = ["order_number", "id"]
        verbose_name = "упражнение в плане"
        verbose_name_plural = "упражнения в плане"

    def __str__(self):
        return f"{self.exercise} ({self.workout_day})"


class CompletedWorkout(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="completed_workouts", verbose_name="Клиент")
    workout_day = models.ForeignKey(WorkoutDay, on_delete=models.CASCADE, related_name="completed_workouts", verbose_name="Тренировочный день")
    appointment = models.OneToOneField(
        TrainingAppointment,
        on_delete=models.SET_NULL,
        related_name="completed_workout",
        verbose_name="Запись на тренировку",
        null=True,
        blank=True,
    )
    completed_at = models.DateTimeField("Дата выполнения", default=timezone.now)
    mood = models.CharField("Самочувствие", max_length=120, blank=True)
    comment = models.TextField("Комментарий", blank=True)

    class Meta:
        ordering = ["-completed_at"]
        verbose_name = "выполненная тренировка"
        verbose_name_plural = "выполненные тренировки"

    def __str__(self):
        return f"{self.client} - {self.workout_day} - {self.completed_at:%d.%m.%Y}"

    def clean(self):
        if self.workout_day_id and self.client_id and self.workout_day.workout_plan.client_id != self.client_id:
            raise ValidationError("Тренировка должна относиться к плану этого же клиента.")
        if self.appointment_id and self.client_id and self.appointment.client_id != self.client_id:
            raise ValidationError("Запись на тренировку должна принадлежать этому же клиенту.")
        if self.appointment_id and self.workout_day_id:
            trainer_id = self.workout_day.workout_plan.trainer_id
            if trainer_id and self.appointment.slot.trainer_id != trainer_id:
                raise ValidationError("Запись должна быть к тренеру этого тренировочного плана.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class CompletedExercise(models.Model):
    completed_workout = models.ForeignKey(CompletedWorkout, on_delete=models.CASCADE, related_name="exercises", verbose_name="Тренировка")
    workout_exercise = models.ForeignKey(WorkoutExercise, on_delete=models.CASCADE, related_name="completed_entries", verbose_name="Плановое упражнение")
    actual_sets = models.PositiveSmallIntegerField("Фактические подходы", default=0)
    actual_reps = models.PositiveSmallIntegerField("Фактические повторения", default=0)
    actual_weight = models.DecimalField("Фактический вес", max_digits=6, decimal_places=1, default=0)
    is_completed = models.BooleanField("Выполнено", default=False)
    comment = models.CharField("Комментарий", max_length=240, blank=True)

    class Meta:
        verbose_name = "выполненное упражнение"
        verbose_name_plural = "выполненные упражнения"


class Product(models.Model):
    name = models.CharField("Название", max_length=140, unique=True)
    calories_per_100g = models.DecimalField("Ккал на 100 г", max_digits=7, decimal_places=1)
    protein_per_100g = models.DecimalField("Белки на 100 г", max_digits=6, decimal_places=1)
    fat_per_100g = models.DecimalField("Жиры на 100 г", max_digits=6, decimal_places=1)
    carbs_per_100g = models.DecimalField("Углеводы на 100 г", max_digits=6, decimal_places=1)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "продукт"
        verbose_name_plural = "продукты"

    def __str__(self):
        return self.name

    def clean(self):
        fields = {
            "calories_per_100g": self.calories_per_100g,
            "protein_per_100g": self.protein_per_100g,
            "fat_per_100g": self.fat_per_100g,
            "carbs_per_100g": self.carbs_per_100g,
        }
        if any(value is not None and value < 0 for value in fields.values()):
            raise ValidationError("Калории и БЖУ продукта не могут быть отрицательными.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class MealType(models.TextChoices):
    BREAKFAST = "breakfast", "Завтрак"
    LUNCH = "lunch", "Обед"
    DINNER = "dinner", "Ужин"
    SNACK = "snack", "Перекус"


class Meal(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="meals", verbose_name="Клиент")
    meal_date = models.DateField("Дата")
    meal_type = models.CharField("Прием пищи", max_length=16, choices=MealType.choices)
    comment = models.CharField("Комментарий", max_length=240, blank=True)

    class Meta:
        ordering = ["meal_date", "meal_type"]
        unique_together = ("client", "meal_date", "meal_type")
        verbose_name = "прием пищи"
        verbose_name_plural = "приемы пищи"

    def __str__(self):
        return f"{self.client} - {self.get_meal_type_display()} - {self.meal_date:%d.%m.%Y}"

    def totals(self):
        totals = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
        for item in self.items.all():
            totals["calories"] += float(item.calories or 0)
            totals["protein"] += float(item.protein or 0)
            totals["fat"] += float(item.fat or 0)
            totals["carbs"] += float(item.carbs or 0)
        return totals


class MealItem(models.Model):
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name="items", verbose_name="Прием пищи")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, related_name="meal_items", verbose_name="Продукт", null=True, blank=True)
    custom_name = models.CharField("Свое блюдо", max_length=140, blank=True)
    weight_grams = models.DecimalField("Вес, г", max_digits=7, decimal_places=1)
    calories = models.DecimalField("Ккал", max_digits=8, decimal_places=1, default=0)
    protein = models.DecimalField("Белки", max_digits=7, decimal_places=1, default=0)
    fat = models.DecimalField("Жиры", max_digits=7, decimal_places=1, default=0)
    carbs = models.DecimalField("Углеводы", max_digits=7, decimal_places=1, default=0)

    class Meta:
        verbose_name = "позиция питания"
        verbose_name_plural = "позиции питания"

    def __str__(self):
        return self.product.name if self.product else self.custom_name

    def save(self, *args, **kwargs):
        if self.product_id:
            factor = self.weight_grams / 100
            self.calories = self.product.calories_per_100g * factor
            self.protein = self.product.protein_per_100g * factor
            self.fat = self.product.fat_per_100g * factor
            self.carbs = self.product.carbs_per_100g * factor
        super().save(*args, **kwargs)


class NutritionTarget(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="nutrition_targets", verbose_name="Клиент")
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.SET_NULL, related_name="nutrition_targets", verbose_name="Тренер", null=True, blank=True)
    calories_target = models.PositiveIntegerField("Ккал")
    protein_target = models.PositiveIntegerField("Белки")
    fat_target = models.PositiveIntegerField("Жиры")
    carbs_target = models.PositiveIntegerField("Углеводы")
    start_date = models.DateField("Дата начала", default=timezone.localdate)
    end_date = models.DateField("Дата окончания", null=True, blank=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "норма питания"
        verbose_name_plural = "нормы питания"

    def __str__(self):
        return f"{self.client}: {self.calories_target} ккал"


class ProgressRecord(models.Model):
    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="progress_records", verbose_name="Клиент")
    record_date = models.DateField("Дата", default=timezone.localdate)
    weight = models.DecimalField("Вес", max_digits=5, decimal_places=1)
    waist = models.DecimalField("Талия", max_digits=5, decimal_places=1, null=True, blank=True)
    chest = models.DecimalField("Грудь", max_digits=5, decimal_places=1, null=True, blank=True)
    hips = models.DecimalField("Бедра", max_digits=5, decimal_places=1, null=True, blank=True)
    comment = models.CharField("Комментарий", max_length=240, blank=True)

    class Meta:
        ordering = ["record_date"]
        verbose_name = "запись прогресса"
        verbose_name_plural = "записи прогресса"

    def __str__(self):
        return f"{self.client} - {self.record_date:%d.%m.%Y}"

    def clean(self):
        values = [self.weight, self.waist, self.chest, self.hips]
        if any(value is not None and value <= 0 for value in values):
            raise ValidationError("Вес и замеры должны быть положительными числами.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class TrainerComment(models.Model):
    RELATED_TYPES = (
        ("profile", "Профиль"),
        ("workout", "Тренировка"),
        ("nutrition", "Питание"),
        ("progress", "Прогресс"),
    )

    client = models.ForeignKey(ClientProfile, on_delete=models.CASCADE, related_name="trainer_comments", verbose_name="Клиент")
    trainer = models.ForeignKey(TrainerProfile, on_delete=models.SET_NULL, related_name="comments", verbose_name="Тренер", null=True, blank=True)
    related_type = models.CharField("Раздел", max_length=20, choices=RELATED_TYPES, default="profile")
    related_id = models.PositiveIntegerField("ID связанной записи", null=True, blank=True)
    text = models.TextField("Комментарий")
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "комментарий тренера"
        verbose_name_plural = "комментарии тренера"

    def __str__(self):
        return f"{self.client} - {self.get_related_type_display()}"

# Create your models here.
