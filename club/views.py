import json
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time

from .ai_jobs import (
    mark_stale_analysis_failed,
    queue_client_analysis,
    queue_management_analysis,
    queue_trainer_analysis,
    queue_weight_forecast_analysis,
)
from .ai_management import (
    AI_PROVIDER_LABELS,
    ai_provider_label,
    build_local_client_recommendations,
    build_local_management_recommendations,
    build_local_trainer_recommendations,
    build_local_weight_forecast,
    normalize_ai_provider,
)
from .client_assistant import build_client_ai_payload
from .forms import (
    ClubReviewForm,
    ClientCreateForm,
    ClientEditForm,
    ExerciseForm,
    MealItemForm,
    MembershipForm,
    NutritionTargetForm,
    AppointmentCancelForm,
    ProductForm,
    ProgressRecordForm,
    ScheduleSlotForm,
    TrainerCommentForm,
    TrainerCreateForm,
    TrainerEditForm,
    TrainerReviewForm,
    WorkoutDayForm,
    WorkoutExerciseForm,
    WorkoutPlanForm,
)
from .models import (
    ClientAIAnalysis,
    ClubReview,
    ClientProfile,
    CompletedExercise,
    CompletedWorkout,
    Exercise,
    Goal,
    Meal,
    MealItem,
    MealType,
    Membership,
    MembershipStatus,
    ManagementAIAnalysis,
    NutritionTarget,
    AppointmentStatus,
    Product,
    PlanStatus,
    ProgressRecord,
    Role,
    ScheduleSlot,
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
from .recommendations import build_client_recommendations
from .trainer_assistant import build_trainer_ai_payload
from .utils import (
    bootstrap_roles,
    can_manage_client,
    can_view_client,
    client_profile_for,
    clients_available_for,
    dashboard_metrics,
    get_client_for_user,
    is_admin,
    is_client,
    is_trainer,
    trainer_load_queryset,
    trainer_profile_for,
    user_role,
)
from .weight_forecast import build_weight_forecast_payload, weight_forecast_chart, weight_forecast_readiness

CLIENT_CHART_PERIODS = {
    "7": {"label": "7 дней", "days": 7},
    "30": {"label": "30 дней", "days": 30},
    "90": {"label": "90 дней", "days": 90},
    "180": {"label": "180 дней", "days": 180},
    "365": {"label": "1 год", "days": 365},
}
DEFAULT_CLIENT_CHART_PERIOD = "90"
CLIENT_AI_DEFAULT_MODEL_TIER = "fast"


AI_MODEL_TIERS = {
    "fast": {
        "label": "Быстрая",
        "description": "минимальная задержка",
    },
    "balanced": {
        "label": "Оптимальная",
        "description": "баланс скорости и качества",
    },
    "smart": {
        "label": "Умная",
        "description": "самая сильная, медленнее",
    },
}


def role_required(*roles):
    def decorator(view_func):
        @login_required
        def wrapper(request, *args, **kwargs):
            if user_role(request.user) not in roles:
                raise PermissionDenied("Недостаточно прав для этого раздела.")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def render_app(request, template, context=None, status=200):
    context = context or {}
    context.update(
        {
            "role": user_role(request.user),
            "is_admin_role": is_admin(request.user),
            "is_trainer_role": is_trainer(request.user),
            "is_client_role": is_client(request.user),
            "current_client": client_profile_for(request.user),
            "current_trainer": trainer_profile_for(request.user),
        }
    )
    return render(request, template, context, status=status)


def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return redirect("login")


def login_view(request):
    bootstrap_roles()
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        messages.success(request, "Вы вошли в систему.")
        return redirect("dashboard")

    return render(request, "registration/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.info(request, "Вы вышли из системы.")
    return redirect("login")


@login_required
def dashboard(request):
    role = user_role(request.user)
    metrics = dashboard_metrics(request.user)
    context = {"metrics": metrics}

    if role == Role.CLIENT:
        client = client_profile_for(request.user)
        if client is None:
            raise PermissionDenied("К пользователю не привязан профиль клиента.")
        selected_date = timezone.localdate()
        chart_period = client_chart_period_from_request(request)
        nutrition = nutrition_summary(client, selected_date)
        context.update(
            {
                "client": client,
                "membership": client.current_membership(),
                "plan": client.active_plan(),
                "nutrition": nutrition,
                "progress": progress_chart_data(client, chart_period),
                "physical": client_physical_summary(client),
                "client_dashboard_charts": client_dashboard_charts(client, chart_period),
                "client_chart_period": chart_period,
                "client_chart_period_label": client_chart_period_label(chart_period),
                "client_chart_period_options": client_chart_period_options(),
                "ai_recommendations": build_client_recommendations(client),
                "comments": client.trainer_comments.select_related("trainer")[:5],
            }
        )
    elif role == Role.TRAINER:
        trainer = trainer_profile_for(request.user)
        if trainer is None:
            raise PermissionDenied("К пользователю не привязан профиль тренера.")
        trainer_ai_context = trainer_dashboard_ai_context(request, trainer)
        context.update(
            {
                "trainer": trainer,
                "clients": clients_available_for(request.user)[:8],
                "recent_workouts": CompletedWorkout.objects.filter(client__trainer=trainer).select_related(
                    "client", "workout_day", "workout_day__workout_plan"
                )[:8],
                **trainer_ai_context,
            }
        )
    elif role == Role.ADMIN:
        clients = clients_available_for(request.user)
        context.update(
            {
                "trainers": trainer_load_queryset()[:8],
                "goal_stats": goal_stats(clients),
            }
        )
    else:
        raise PermissionDenied("Для пользователя не назначена роль.")

    return render_app(request, "club/dashboard.html", context)


@login_required
def reviews_page(request):
    current_client = client_profile_for(request.user)
    current_trainer = trainer_profile_for(request.user)
    form = ClubReviewForm(initial={"rating": 5}) if is_client(request.user) else None
    trainer_review_form = TrainerReviewForm(initial={"rating": 5}, client=current_client) if is_client(request.user) else None

    if request.method == "POST":
        if not is_client(request.user) or current_client is None:
            raise PermissionDenied("Отзывы могут оставлять только клиенты клуба.")

        if request.POST.get("review_type") == "trainer":
            trainer_review_form = TrainerReviewForm(request.POST, client=current_client)
            if trainer_review_form.is_valid():
                review = trainer_review_form.save(commit=False)
                review.client = current_client
                review.save()
                messages.success(request, "Оценка тренера опубликована.")
                return redirect("reviews")
        else:
            form = ClubReviewForm(request.POST)
            if form.is_valid():
                review = form.save(commit=False)
                review.client = current_client
                review.save()
                messages.success(request, "Спасибо! Отзыв опубликован.")
                return redirect("reviews")

    reviews = ClubReview.objects.select_related("client", "client__user")
    if is_admin(request.user) or is_trainer(request.user):
        visible_reviews = reviews
    elif current_client:
        visible_reviews = reviews.filter(Q(is_published=True) | Q(client=current_client))
    else:
        visible_reviews = reviews.filter(is_published=True)

    published_reviews = ClubReview.objects.filter(is_published=True)
    aggregate = published_reviews.aggregate(avg=Avg("rating"), count=Count("id"))
    reviews_total = aggregate["count"] or 0
    rating_counts = {
        row["rating"]: row["count"]
        for row in published_reviews.values("rating").annotate(count=Count("id")).order_by("rating")
    }
    rating_breakdown = [
        {
            "rating": rating,
            "count": rating_counts.get(rating, 0),
            "percent": round(rating_counts.get(rating, 0) / reviews_total * 100) if reviews_total else 0,
        }
        for rating in range(5, 0, -1)
    ]
    trainer_reviews = TrainerReview.objects.select_related("client", "client__user", "trainer", "trainer__user")
    if is_admin(request.user):
        visible_trainer_reviews = trainer_reviews
    elif current_trainer:
        visible_trainer_reviews = trainer_reviews.filter(Q(is_published=True) | Q(trainer=current_trainer))
    elif current_client:
        visible_trainer_reviews = trainer_reviews.filter(Q(is_published=True) | Q(client=current_client))
    else:
        visible_trainer_reviews = trainer_reviews.filter(is_published=True)

    published_trainer_reviews = TrainerReview.objects.filter(is_published=True)
    trainer_aggregate = published_trainer_reviews.aggregate(avg=Avg("rating"), count=Count("id"))
    trainer_reviews_total = trainer_aggregate["count"] or 0
    trainer_rating_counts = {
        row["rating"]: row["count"]
        for row in published_trainer_reviews.values("rating").annotate(count=Count("id")).order_by("rating")
    }
    trainer_rating_breakdown = [
        {
            "rating": rating,
            "count": trainer_rating_counts.get(rating, 0),
            "percent": round(trainer_rating_counts.get(rating, 0) / trainer_reviews_total * 100) if trainer_reviews_total else 0,
        }
        for rating in range(5, 0, -1)
    ]
    trainer_rating_rows = (
        TrainerProfile.objects.annotate(
            average_rating=Avg("trainer_reviews__rating", filter=Q(trainer_reviews__is_published=True)),
            reviews_count=Count("trainer_reviews", filter=Q(trainer_reviews__is_published=True)),
        )
        .filter(reviews_count__gt=0)
        .order_by("-average_rating", "-reviews_count", "full_name")
    )

    context = {
        "reviews": visible_reviews,
        "trainer_reviews": visible_trainer_reviews,
        "form": form,
        "trainer_review_form": trainer_review_form,
        "average_rating": round(aggregate["avg"] or 0, 1),
        "reviews_total": reviews_total,
        "five_star_total": rating_counts.get(5, 0),
        "rating_breakdown": rating_breakdown,
        "user_reviews_total": current_client.club_reviews.count() if current_client else 0,
        "trainer_average_rating": round(trainer_aggregate["avg"] or 0, 1),
        "trainer_reviews_total": trainer_reviews_total,
        "trainer_five_star_total": trainer_rating_counts.get(5, 0),
        "trainer_rating_breakdown": trainer_rating_breakdown,
        "trainer_rating_rows": trainer_rating_rows,
        "user_trainer_reviews_total": current_client.trainer_reviews.count() if current_client else 0,
    }
    return render_app(request, "club/reviews.html", context)


@login_required
def clients_list(request):
    clients = clients_available_for(request.user)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    trainer_id = request.GET.get("trainer", "").strip()
    activity = request.GET.get("activity", "").strip()

    if query:
        clients = clients.filter(
            Q(full_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__username__icontains=query)
        )
    if status:
        clients = clients.filter(status=status)
    if trainer_id and is_admin(request.user):
        clients = clients.filter(trainer_id=trainer_id)
    if activity == "low":
        clients = clients.annotate(last_activity_at=Max("completed_workouts__completed_at")).filter(
            Q(last_activity_at__isnull=True) | Q(last_activity_at__date__lt=timezone.localdate() - timedelta(days=14))
        )
    elif activity == "active":
        clients = clients.filter(completed_workouts__completed_at__date__gte=timezone.localdate() - timedelta(days=30)).distinct()

    context = {
        "clients": clients.select_related("trainer", "user"),
        "trainers": TrainerProfile.objects.filter(status="active"),
        "filters": {"q": query, "status": status, "trainer": trainer_id, "activity": activity},
        "statuses": ClientProfile._meta.get_field("status").choices,
    }
    return render_app(request, "club/clients_list.html", context)


@role_required(Role.ADMIN)
def client_create(request):
    form = ClientCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        messages.success(request, "Клиент создан.")
        return redirect("client_detail", pk=client.pk)
    return render_app(request, "club/form_page.html", {"form": form, "title": "Новый клиент", "back_url": reverse("clients_list")})


@login_required
def client_detail(request, pk):
    client = get_client_for_user(request.user, pk)
    selected_date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    chart_period = client_chart_period_from_request(request)
    context = {
        "client": client,
        "memberships": client.memberships.all(),
        "plans": client.workout_plans.filter(is_template=False).select_related("trainer").prefetch_related("days__exercises__exercise"),
        "completed_workouts": client.completed_workouts.select_related("workout_day", "workout_day__workout_plan")[:10],
        "nutrition": nutrition_summary(client, selected_date),
        "progress": progress_chart_data(client, chart_period),
        "progress_table_records": sorted(
            chart_progress_records_by_date(client, chart_period).values(),
            key=lambda record: (record.record_date, record.id),
            reverse=True,
        ),
        "client_chart_period": chart_period,
        "client_chart_period_label": client_chart_period_label(chart_period),
        "client_chart_period_options": client_chart_period_options(),
        "ai_recommendations": build_client_recommendations(client),
        "comments": client.trainer_comments.select_related("trainer")[:10],
        "selected_date": selected_date,
        "tab": request.GET.get("tab", "profile"),
    }
    return render_app(request, "club/client_detail.html", context)


@login_required
def client_edit(request, pk):
    client = get_client_for_user(request.user, pk)
    if not can_manage_client(request.user, client):
        raise PermissionDenied("Недостаточно прав для редактирования клиента.")
    form = ClientEditForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Карточка клиента обновлена.")
        return redirect("client_detail", pk=client.pk)
    return render_app(request, "club/form_page.html", {"form": form, "title": "Редактирование клиента", "back_url": reverse("client_detail", args=[client.pk])})


@role_required(Role.ADMIN)
def trainers_list(request):
    query = request.GET.get("q", "").strip()
    trainers = TrainerProfile.objects.all()
    if query:
        trainers = trainers.filter(Q(full_name__icontains=query) | Q(specialization__icontains=query) | Q(phone__icontains=query))
    return render_app(request, "club/trainers_list.html", {"trainers": trainers, "query": query})


@role_required(Role.ADMIN)
def trainer_create(request):
    form = TrainerCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        trainer = form.save()
        messages.success(request, "Тренер создан.")
        return redirect("trainers_list")
    return render_app(request, "club/form_page.html", {"form": form, "title": "Новый тренер", "back_url": reverse("trainers_list")})


@role_required(Role.ADMIN)
def trainer_edit(request, pk):
    trainer = get_object_or_404(TrainerProfile, pk=pk)
    form = TrainerEditForm(request.POST or None, instance=trainer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Профиль тренера обновлен.")
        return redirect("trainers_list")
    return render_app(request, "club/form_page.html", {"form": form, "title": "Редактирование тренера", "back_url": reverse("trainers_list")})


@role_required(Role.ADMIN)
def membership_create(request, client_id):
    client = get_object_or_404(ClientProfile, pk=client_id)
    form = MembershipForm(request.POST or None, initial={"start_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        membership = form.save(commit=False)
        membership.client = client
        membership.save()
        messages.success(request, "Абонемент сохранен.")
        return redirect(f"{reverse('client_detail', args=[client.pk])}?tab=membership")
    return render_app(request, "club/form_page.html", {"form": form, "title": f"Абонемент: {client.full_name}", "back_url": reverse("client_detail", args=[client.pk])})


@role_required(Role.ADMIN)
def membership_edit(request, pk):
    membership = get_object_or_404(Membership, pk=pk)
    form = MembershipForm(request.POST or None, instance=membership)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Абонемент обновлен.")
        return redirect(f"{reverse('client_detail', args=[membership.client_id])}?tab=membership")
    return render_app(request, "club/form_page.html", {"form": form, "title": "Продление абонемента", "back_url": reverse("client_detail", args=[membership.client_id])})


@role_required(Role.ADMIN, Role.TRAINER)
def exercises_list(request):
    group = request.GET.get("group", "").strip()
    exercises = Exercise.objects.all()
    if group:
        exercises = exercises.filter(muscle_group=group)
    form = ExerciseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Упражнение добавлено.")
        return redirect("exercises_list")
    groups = (
        Exercise.objects.exclude(muscle_group="")
        .order_by("muscle_group")
        .values_list("muscle_group", flat=True)
        .distinct()
    )
    return render_app(request, "club/exercises_list.html", {"exercises": exercises, "form": form, "groups": groups, "selected_group": group})


@login_required
def products_list(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.all()
    if query:
        products = products.filter(name__icontains=query)
    form = ProductForm(request.POST or None)
    if request.method == "POST":
        if not (is_admin(request.user) or is_trainer(request.user)):
            raise PermissionDenied("Добавлять продукты может администратор или тренер.")
        if form.is_valid():
            form.save()
            messages.success(request, "Продукт добавлен.")
            return redirect("products_list")
    return render_app(request, "club/products_list.html", {"products": products, "form": form, "query": query})


@login_required
def schedule_page(request):
    role = user_role(request.user)
    now = timezone.now()
    today = timezone.localdate()
    slot_form = None

    if role == Role.TRAINER:
        trainer = trainer_profile_for(request.user)
        if trainer is None:
            raise PermissionDenied("К пользователю не привязан профиль тренера.")
        slot_form = ScheduleSlotForm(
            request.POST or None,
            trainer=trainer,
            created_by=request.user,
            initial={"date": timezone.localdate()},
        )
        if request.method == "POST":
            if slot_form.is_valid():
                created = slot_form.save()
                messages.success(request, f"Создано слотов: {len(created)}.")
                return redirect("schedule")
        slots = ScheduleSlot.objects.filter(trainer=trainer, is_active=True, start_at__gte=now).prefetch_related(
            "appointments__client"
        )
        appointments = TrainingAppointment.objects.filter(
            slot__trainer=trainer,
            status=AppointmentStatus.BOOKED,
            slot__start_at__date__gte=today,
        ).select_related("slot", "client", "client__trainer").order_by("slot__start_at")[:30]
        attach_completion_days(appointments)
        rows = schedule_slot_rows(slots[:60])
        context = {
            "schedule_role": "trainer",
            "slot_form": slot_form,
            "slot_rows": rows,
            "schedule_weeks": schedule_week_groups(rows),
            "appointments": appointments,
            "schedule_stats": schedule_stats(rows, appointments),
        }
    elif role == Role.CLIENT:
        client = client_profile_for(request.user)
        if client is None:
            raise PermissionDenied("К пользователю не привязан профиль клиента.")
        appointments = TrainingAppointment.objects.filter(
            client=client,
            status=AppointmentStatus.BOOKED,
            slot__start_at__date__gte=today,
        ).select_related("slot", "slot__trainer").order_by("slot__start_at")[:20]
        attach_completion_days(appointments)
        available_slots = ScheduleSlot.objects.filter(
            trainer=client.trainer,
            is_active=True,
            start_at__gte=now,
        ).exclude(id__in=booked_slot_ids())
        rows = schedule_slot_rows(available_slots[:40])
        context = {
            "schedule_role": "client",
            "client": client,
            "slot_rows": rows,
            "schedule_weeks": schedule_week_groups(rows),
            "appointments": appointments,
            "cancel_form": AppointmentCancelForm(),
            "schedule_stats": schedule_stats(rows, appointments),
        }
    elif role == Role.ADMIN:
        slots = ScheduleSlot.objects.filter(is_active=True, start_at__gte=now).select_related("trainer").prefetch_related(
            "appointments__client"
        )
        appointments = TrainingAppointment.objects.filter(
            status=AppointmentStatus.BOOKED,
            slot__start_at__date__gte=today,
        ).select_related("slot", "slot__trainer", "client", "client__trainer").order_by("slot__start_at")[:40]
        attach_completion_days(appointments)
        rows = schedule_slot_rows(slots[:80])
        context = {
            "schedule_role": "admin",
            "slot_rows": rows,
            "schedule_weeks": schedule_week_groups(rows),
            "appointments": appointments,
            "schedule_stats": schedule_stats(rows, appointments),
        }
    else:
        raise PermissionDenied("Для пользователя не назначена роль.")

    return render_app(request, "club/schedule.html", context)


@role_required(Role.CLIENT)
def schedule_slot_book(request, pk):
    if request.method != "POST":
        return redirect("schedule")
    client = client_profile_for(request.user)
    if client is None:
        raise PermissionDenied("К пользователю не привязан профиль клиента.")

    with transaction.atomic():
        slot = get_object_or_404(ScheduleSlot.objects.select_for_update(), pk=pk, is_active=True)
        if slot.trainer_id != client.trainer_id:
            raise PermissionDenied("Записаться можно только к закрепленному тренеру.")
        appointment = TrainingAppointment(slot=slot, client=client, status=AppointmentStatus.BOOKED)
        try:
            appointment.full_clean()
            appointment.save()
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("schedule")

    messages.success(request, f"Вы записаны на тренировку {slot.start_at:%d.%m.%Y в %H:%M}.")
    return redirect("schedule")


@role_required(Role.CLIENT)
def schedule_appointment_cancel(request, pk):
    if request.method != "POST":
        return redirect("schedule")
    client = client_profile_for(request.user)
    appointment = get_object_or_404(
        TrainingAppointment.objects.select_related("slot", "slot__trainer"),
        pk=pk,
        client=client,
        status=AppointmentStatus.BOOKED,
    )
    if not appointment.can_cancel_by_client:
        messages.error(request, "Отменить занятие через сайт можно не позднее чем за 24 часа до начала.")
        return redirect("schedule")

    form = AppointmentCancelForm(request.POST)
    reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
    appointment.cancel(request.user, reason)
    messages.success(request, "Запись отменена. Слот снова доступен в расписании тренера.")
    return redirect("schedule")


@role_required(Role.ADMIN, Role.TRAINER)
def schedule_appointment_complete(request, pk):
    if request.method != "POST":
        return redirect("schedule")

    appointment = get_object_or_404(
        TrainingAppointment.objects.select_related("slot", "slot__trainer", "client", "client__trainer"),
        pk=pk,
        status=AppointmentStatus.BOOKED,
    )
    trainer = trainer_profile_for(request.user)
    if is_trainer(request.user) and appointment.slot.trainer_id != getattr(trainer, "id", None):
        raise PermissionDenied("Провести можно только занятие из своего расписания.")
    if not appointment.can_complete:
        messages.error(request, "Будущую запись нельзя отметить как проведенную заранее.")
        return redirect("schedule")

    client = appointment.client
    plan = client.active_plan()
    day = next_uncompleted_workout_day(plan, client) if plan else None
    membership = client.current_membership()
    if not plan or not day:
        messages.error(request, "У клиента нет активного плана с невыполненным тренировочным днем.")
        return redirect("schedule")
    if not membership or membership.status != MembershipStatus.ACTIVE:
        messages.error(request, "Для проведения занятия нужен активный абонемент с доступными посещениями.")
        return redirect("schedule")

    with transaction.atomic():
        workout = CompletedWorkout(
            client=client,
            workout_day=day,
            appointment=appointment,
            completed_at=timezone.now(),
            mood="Проведено тренером" if is_trainer(request.user) else "Проведено администратором",
        )
        workout.full_clean()
        workout.save()
        for plan_exercise in day.exercises.all():
            CompletedExercise.objects.create(
                completed_workout=workout,
                workout_exercise=plan_exercise,
                is_completed=True,
                actual_sets=plan_exercise.sets_count,
                actual_reps=plan_exercise.reps_count,
                actual_weight=plan_exercise.recommended_weight,
            )
        consume_membership_visit(membership)
        appointment.status = AppointmentStatus.COMPLETED
        appointment.save(update_fields=["status"])

    messages.success(request, f"Занятие с клиентом {client.full_name} отмечено как проведенное.")
    return redirect("schedule")


@role_required(Role.TRAINER)
def schedule_slot_deactivate(request, pk):
    if request.method != "POST":
        return redirect("schedule")
    trainer = trainer_profile_for(request.user)
    slot = get_object_or_404(ScheduleSlot, pk=pk, trainer=trainer, is_active=True)
    if slot.booked_appointment:
        messages.error(request, "Нельзя убрать слот, на который уже записан клиент.")
    elif slot.start_at <= timezone.now():
        messages.error(request, "Нельзя убрать прошедший слот.")
    else:
        slot.is_active = False
        slot.save(update_fields=["is_active"])
        messages.success(request, "Свободный слот убран из расписания.")
    return redirect("schedule")


@login_required
def workout_plan_create(request, client_id):
    client = get_client_for_user(request.user, client_id)
    if not can_manage_client(request.user, client):
        raise PermissionDenied("Создавать планы может администратор или закрепленный тренер.")

    form = WorkoutPlanForm(request.POST or None, initial={"goal": client.goal, "start_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            plan = form.save(commit=False)
            plan.client = client
            plan.trainer = trainer_profile_for(request.user) or client.trainer
            plan.save()
            if plan.status == PlanStatus.ACTIVE:
                client.workout_plans.filter(status=PlanStatus.ACTIVE, is_template=False).exclude(pk=plan.pk).update(
                    status=PlanStatus.COMPLETED
                )
            template = form.cleaned_data.get("template")
            if template:
                clone_template_days(template, plan)
            else:
                WorkoutDay.objects.create(workout_plan=plan, title="День 1", day_number=1)
        messages.success(request, "Тренировочный план создан.")
        return redirect("workout_plan_detail", pk=plan.pk)
    return render_app(
        request,
        "club/form_page.html",
        {"form": form, "title": f"Новый план: {client.full_name}", "back_url": reverse("client_detail", args=[client.pk])},
    )


@login_required
def workout_plan_detail(request, pk):
    plan = get_object_or_404(
        WorkoutPlan.objects.select_related("client", "trainer").prefetch_related("days__exercises__exercise"),
        pk=pk,
    )
    if not can_view_client(request.user, plan.client):
        raise PermissionDenied("Недостаточно прав для просмотра плана.")
    context = {
        "plan": plan,
        "day_form": WorkoutDayForm(),
        "exercise_form": WorkoutExerciseForm(),
        "completed_workouts": CompletedWorkout.objects.filter(workout_day__workout_plan=plan).select_related("client", "workout_day")[:10],
    }
    return render_app(request, "club/workout_plan_detail.html", context)


@login_required
def workout_day_create(request, plan_id):
    plan = get_object_or_404(WorkoutPlan.objects.select_related("client"), pk=plan_id)
    if not can_manage_client(request.user, plan.client):
        raise PermissionDenied("Недостаточно прав для изменения плана.")
    form = WorkoutDayForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        day = form.save(commit=False)
        day.workout_plan = plan
        day.save()
        messages.success(request, "Тренировочный день добавлен.")
    return redirect("workout_plan_detail", pk=plan.pk)


@login_required
def workout_exercise_create(request, day_id):
    day = get_object_or_404(WorkoutDay.objects.select_related("workout_plan", "workout_plan__client"), pk=day_id)
    if not can_manage_client(request.user, day.workout_plan.client):
        raise PermissionDenied("Недостаточно прав для изменения плана.")
    form = WorkoutExerciseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        workout_exercise = form.save(commit=False)
        workout_exercise.workout_day = day
        workout_exercise.save()
        messages.success(request, "Упражнение добавлено в план.")
    else:
        messages.error(request, "Проверьте параметры упражнения.")
    return redirect("workout_plan_detail", pk=day.workout_plan_id)


@login_required
def complete_workout(request, day_id):
    day = get_object_or_404(
        WorkoutDay.objects.select_related("workout_plan", "workout_plan__client").prefetch_related("exercises__exercise"),
        pk=day_id,
    )
    client = day.workout_plan.client
    if not (is_client(request.user) and client_profile_for(request.user) == client):
        raise PermissionDenied("Отметить выполнение может только владелец плана.")
    if day.workout_plan.is_template:
        raise PermissionDenied("Шаблонный план нельзя отмечать как выполненный.")
    if day.workout_plan.status != PlanStatus.ACTIVE:
        messages.error(request, "Выполнять можно только активный тренировочный план.")
        return redirect("workout_plan_detail", pk=day.workout_plan_id)

    membership = client.current_membership()
    if not membership or membership.status != MembershipStatus.ACTIVE:
        messages.error(request, "Для отметки тренировки нужен активный абонемент с доступными посещениями.")
        return redirect("workout_plan_detail", pk=day.workout_plan_id)

    linked_appointment = workout_appointment_from_request(request, client, day.workout_plan)
    completed_default = linked_appointment.slot.start_at if linked_appointment else timezone.now()

    if request.method == "POST":
        exercises = list(day.exercises.all())
        completed_flags = [request.POST.get(f"exercise_{item.id}_done") == "on" for item in exercises]
        if exercises and not any(completed_flags):
            messages.error(request, "Отметьте хотя бы одно выполненное упражнение.")
            return redirect("complete_workout", day_id=day.id)

        completed_at = completed_at_from_post(request.POST, completed_default)
        if completed_at > timezone.now() + timedelta(minutes=5):
            messages.error(request, "Нельзя отметить тренировку будущей датой.")
            return redirect("complete_workout", day_id=day.id)
        linked_appointment = workout_appointment_from_request(request, client, day.workout_plan, completed_at)

        with transaction.atomic():
            workout = CompletedWorkout(
                client=client,
                workout_day=day,
                appointment=linked_appointment,
                completed_at=completed_at,
                mood=request.POST.get("mood", "").strip(),
                comment=request.POST.get("comment", "").strip(),
            )
            workout.full_clean()
            workout.save()
            for plan_exercise in exercises:
                prefix = f"exercise_{plan_exercise.id}"
                CompletedExercise.objects.create(
                    completed_workout=workout,
                    workout_exercise=plan_exercise,
                    is_completed=request.POST.get(f"{prefix}_done") == "on",
                    actual_sets=positive_int(request.POST.get(f"{prefix}_sets"), plan_exercise.sets_count),
                    actual_reps=positive_int(request.POST.get(f"{prefix}_reps"), plan_exercise.reps_count),
                    actual_weight=decimal_or_zero(request.POST.get(f"{prefix}_weight")),
                    comment=request.POST.get(f"{prefix}_comment", "").strip(),
                )
            consume_membership_visit(membership)

            if linked_appointment:
                linked_appointment.status = AppointmentStatus.COMPLETED
                linked_appointment.save(update_fields=["status"])
        messages.success(request, "Тренировка сохранена.")
        return redirect(f"{reverse('client_detail', args=[client.pk])}?tab=workouts")

    return render_app(
        request,
        "club/complete_workout.html",
        {
            "day": day,
            "client": client,
            "linked_appointment": linked_appointment,
            "completed_date": timezone.localtime(completed_default).date(),
            "completed_time": timezone.localtime(completed_default).strftime("%H:%M"),
        },
    )


@login_required
def nutrition_page(request, client_id=None):
    client = resolve_client_context(request, client_id)
    selected_date = parse_date(request.GET.get("date") or "") or timezone.localdate()
    form = MealItemForm(request.POST or None, initial={"meal_date": selected_date})
    if request.method == "POST" and form.is_valid():
        item = form.save_for_client(client)
        messages.success(request, f"Добавлено: {item}.")
        return redirect(f"{reverse('nutrition_client', args=[client.pk])}?date={form.cleaned_data['meal_date']}")

    context = {
        "client": client,
        "selected_date": selected_date,
        "nutrition": nutrition_summary(client, selected_date),
        "form": form,
    }
    return render_app(request, "club/nutrition_page.html", context)


@login_required
def meal_item_delete(request, pk):
    if request.method != "POST":
        return redirect("dashboard")
    item = get_object_or_404(MealItem.objects.select_related("meal", "meal__client"), pk=pk)
    client = item.meal.client
    if not can_manage_or_own_client(request.user, client):
        raise PermissionDenied("Недостаточно прав для удаления записи питания.")
    selected_date = item.meal.meal_date
    meal = item.meal
    item.delete()
    if not meal.items.exists():
        meal.delete()
    messages.success(request, "Запись питания удалена.")
    return redirect(f"{reverse('nutrition_client', args=[client.pk])}?date={selected_date}")


@login_required
def nutrition_target_set(request, client_id):
    client = get_client_for_user(request.user, client_id)
    if not can_manage_client(request.user, client):
        raise PermissionDenied("Задавать нормы может администратор или закрепленный тренер.")
    form = NutritionTargetForm(request.POST or None, initial={"start_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        target = form.save(commit=False)
        target.client = client
        target.trainer = trainer_profile_for(request.user) or client.trainer
        target.save()
        messages.success(request, "Норма питания сохранена.")
        return redirect(f"{reverse('client_detail', args=[client.pk])}?tab=nutrition")
    return render_app(request, "club/form_page.html", {"form": form, "title": f"Норма питания: {client.full_name}", "back_url": reverse("client_detail", args=[client.pk])})


@login_required
def progress_page(request, client_id=None):
    client = resolve_client_context(request, client_id)
    chart_period = client_chart_period_from_request(request)
    if request.method == "POST":
        if not can_manage_or_own_client(request.user, client):
            raise PermissionDenied("Недостаточно прав для добавления прогресса.")
        if request.POST.get("form_type") == "workout_load":
            save_workout_load(client, request.POST)
            messages.success(request, "Нагрузка тренировки сохранена.")
            return redirect(f"{reverse('progress_client', args=[client.pk])}#training-load")
        else:
            form = ProgressRecordForm(request.POST)
            if form.is_valid():
                record = form.save(commit=False)
                record.client = client
                record.save()
                messages.success(request, "Запись прогресса добавлена.")
                return redirect("progress_client", client_id=client.pk)
    else:
        form = ProgressRecordForm(initial={"record_date": timezone.localdate()})

    context = {
        "client": client,
        "form": form,
        "records": progress_records_for_period(client, chart_period),
        "progress": progress_chart_data(client, chart_period),
        "progress_charts": client_dashboard_charts(client, chart_period),
        "client_chart_period": chart_period,
        "client_chart_period_label": client_chart_period_label(chart_period),
        "client_chart_period_options": client_chart_period_options(),
        "workout_load_rows": workout_load_rows(client, chart_period),
    }
    context.update(weight_forecast_context(request, client))
    return render_app(request, "club/progress_page.html", context)


@login_required
def progress_record_delete(request, pk):
    if request.method != "POST":
        return redirect("dashboard")
    record = get_object_or_404(ProgressRecord.objects.select_related("client"), pk=pk)
    client = record.client
    if not can_manage_or_own_client(request.user, client):
        raise PermissionDenied("Недостаточно прав для удаления записи прогресса.")
    record.delete()
    messages.success(request, "Запись прогресса удалена.")
    return redirect("progress_client", client_id=client.pk)


@login_required
def completed_workout_delete(request, pk):
    if request.method != "POST":
        return redirect("dashboard")
    workout = get_object_or_404(
        CompletedWorkout.objects.select_related("client", "appointment"),
        pk=pk,
    )
    client = workout.client
    if not can_manage_or_own_client(request.user, client):
        raise PermissionDenied("Недостаточно прав для удаления тренировки.")
    appointment_id = workout.appointment_id
    workout.delete()
    restore_membership_visit(client)
    if appointment_id:
        TrainingAppointment.objects.filter(pk=appointment_id, status=AppointmentStatus.COMPLETED).update(status=AppointmentStatus.BOOKED)
    messages.success(request, "Выполненная тренировка удалена.")
    return redirect(f"{reverse('progress_client', args=[client.pk])}#training-load")


@login_required
def comment_create(request, client_id):
    client = get_client_for_user(request.user, client_id)
    if not can_manage_client(request.user, client):
        raise PermissionDenied("Комментарии может оставлять администратор или закрепленный тренер.")
    form = TrainerCommentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        comment = form.save(commit=False)
        comment.client = client
        comment.trainer = trainer_profile_for(request.user) or client.trainer
        comment.save()
        messages.success(request, "Комментарий сохранен.")
        return redirect(f"{reverse('client_detail', args=[client.pk])}?tab=comments")
    return render_app(request, "club/form_page.html", {"form": form, "title": f"Комментарий: {client.full_name}", "back_url": reverse("client_detail", args=[client.pk])})


@login_required
def recommendations_page(request, client_id=None):
    client = resolve_client_context(request, client_id)
    recommendations = build_client_recommendations(client)
    context = {"client": client, "recommendations": recommendations}
    if is_client(request.user) and client_profile_for(request.user) == client:
        context.update(client_recommendations_ai_context(request, client))
    return render_app(
        request,
        "club/recommendations.html",
        context,
    )


@role_required(Role.ADMIN, Role.TRAINER)
def reports_index(request):
    clients = clients_available_for(request.user)
    context = {
        "metrics": dashboard_metrics(request.user),
        "goal_stats": goal_stats(clients),
        "trainer_load": trainer_load_report(clients),
        "risk_clients": risk_clients(clients),
        "active_clients": active_clients_report(clients),
    }
    return render_app(request, "club/reports.html", context)


@role_required(Role.ADMIN)
def admin_dashboards(request):
    clients = ClientProfile.objects.select_related("trainer", "user").all()
    context = admin_dashboard_context(clients)
    analysis = management_ai_analysis_for_dashboard(request)
    context["ai_analysis"] = analysis
    if analysis:
        context["management_ai_provider"] = ai_provider_label(analysis.provider)
        context["management_ai_model"] = analysis.model
        context["management_ai_selected_provider"] = normalize_ai_provider(analysis.provider)
        context["management_ai_selected_model_tier"] = management_ai_model_tier_for_model(analysis.provider, analysis.model)
        context["ai_analysis_status_url"] = reverse("management_ai_analysis_status", args=[analysis.pk])
        if analysis.status == ManagementAIAnalysis.Status.DONE:
            context["ai_management"] = ai_result_for_display(analysis, build_local_management_recommendations)
        elif analysis.status == ManagementAIAnalysis.Status.FAILED:
            context["ai_management_error"] = analysis.error
    return render_app(request, "club/admin_dashboards.html", context)


@role_required(Role.ADMIN)
def management_ai_analysis_start(request):
    if request.method != "POST":
        return redirect("admin_dashboards")

    clients = ClientProfile.objects.select_related("trainer", "user").all()
    context = admin_dashboard_context(clients)
    provider = management_ai_requested_provider(request)
    model_tier = management_ai_requested_model_tier(request)
    analysis = ManagementAIAnalysis.objects.create(
        requested_by=request.user,
        provider=provider,
        model=management_ai_model_for_provider(provider, model_tier),
        payload=context["management_ai_payload"],
    )
    queue_management_analysis(analysis.pk)
    messages.info(request, "ИИ-анализ запущен. Можно перейти в другой раздел и вернуться позже.")
    return redirect(f"{reverse('admin_dashboards')}?analysis={analysis.pk}#management-ai")


@role_required(Role.ADMIN)
def management_ai_analysis_status(request, pk):
    analysis = get_object_or_404(ManagementAIAnalysis, pk=pk, requested_by=request.user)
    mark_stale_analysis_failed(analysis, "ИИ-анализ управления")
    return no_store_json_response(
        {
            "id": analysis.pk,
            "status": analysis.status,
            "status_label": analysis.get_status_display(),
            "dashboard_url": f"{reverse('admin_dashboards')}?analysis={analysis.pk}#management-ai",
            "finished": analysis.status in {ManagementAIAnalysis.Status.DONE, ManagementAIAnalysis.Status.FAILED},
        }
    )


@role_required(Role.TRAINER)
def trainer_ai_analysis_start(request):
    if request.method != "POST":
        return redirect("dashboard")

    trainer = trainer_profile_for(request.user)
    if trainer is None:
        raise PermissionDenied("К пользователю не привязан профиль тренера.")

    provider = management_ai_requested_provider(request)
    model_tier = management_ai_requested_model_tier(request)
    analysis = TrainerAIAnalysis.objects.create(
        requested_by=request.user,
        trainer=trainer,
        provider=provider,
        model=management_ai_model_for_provider(provider, model_tier),
        payload=build_trainer_ai_payload(trainer),
    )
    queue_trainer_analysis(analysis.pk)
    messages.info(request, "ИИ-ассистент тренера запущен. Можно перейти в другой раздел и вернуться позже.")
    return redirect(f"{reverse('dashboard')}?trainer_analysis={analysis.pk}#trainer-ai")


@role_required(Role.TRAINER)
def trainer_ai_analysis_status(request, pk):
    trainer = trainer_profile_for(request.user)
    analysis = get_object_or_404(TrainerAIAnalysis, pk=pk, requested_by=request.user, trainer=trainer)
    mark_stale_analysis_failed(analysis, "ИИ-ассистент тренера")
    return no_store_json_response(
        {
            "id": analysis.pk,
            "status": analysis.status,
            "status_label": analysis.get_status_display(),
            "dashboard_url": f"{reverse('dashboard')}?trainer_analysis={analysis.pk}#trainer-ai",
            "finished": analysis.status in {TrainerAIAnalysis.Status.DONE, TrainerAIAnalysis.Status.FAILED},
        }
    )


@role_required(Role.CLIENT)
def client_ai_analysis_start(request):
    if request.method != "POST":
        return redirect("recommendations")

    client = client_profile_for(request.user)
    if client is None:
        raise PermissionDenied("К пользователю не привязан профиль клиента.")

    provider = management_ai_requested_provider(request)
    model_tier = normalize_ai_model_tier(request.POST.get("model_tier") or CLIENT_AI_DEFAULT_MODEL_TIER)
    analysis = ClientAIAnalysis.objects.create(
        requested_by=request.user,
        client=client,
        provider=provider,
        model=management_ai_model_for_provider(provider, model_tier),
        payload=build_client_ai_payload(client),
    )
    queue_client_analysis(analysis.pk)
    messages.info(request, "ИИ-коуч клиента запущен. Можно перейти в другой раздел и вернуться позже.")
    return redirect(f"{reverse('recommendations')}?client_analysis={analysis.pk}#client-ai")


@role_required(Role.CLIENT)
def client_ai_analysis_status(request, pk):
    client = client_profile_for(request.user)
    analysis = get_object_or_404(ClientAIAnalysis, pk=pk, requested_by=request.user, client=client)
    mark_stale_analysis_failed(analysis, "ИИ-коуч клиента")
    return no_store_json_response(
        {
            "id": analysis.pk,
            "status": analysis.status,
            "status_label": analysis.get_status_display(),
            "dashboard_url": f"{reverse('recommendations')}?client_analysis={analysis.pk}#client-ai",
            "finished": analysis.status in {ClientAIAnalysis.Status.DONE, ClientAIAnalysis.Status.FAILED},
        }
    )


@login_required
def weight_forecast_analysis_start(request, client_id):
    if request.method != "POST":
        return redirect("progress_client", client_id=client_id)

    client = resolve_client_context(request, client_id)
    if not can_manage_or_own_client(request.user, client):
        raise PermissionDenied("Недостаточно прав для прогноза веса.")

    readiness = weight_forecast_readiness(client)
    if not readiness["can_run"]:
        messages.warning(request, "Для ИИ-прогноза веса пока недостаточно данных: " + "; ".join(readiness["reasons"]) + ".")
        return redirect(f"{reverse('progress_client', args=[client.pk])}#weight-forecast")

    provider = management_ai_requested_provider(request)
    model_tier = management_ai_requested_model_tier(request)
    analysis = WeightForecastAnalysis.objects.create(
        requested_by=request.user,
        client=client,
        provider=provider,
        model=management_ai_model_for_provider(provider, model_tier),
        payload=build_weight_forecast_payload(client),
    )
    queue_weight_forecast_analysis(analysis.pk)
    messages.info(request, "ИИ-прогноз веса запущен. Можно перейти в другой раздел и вернуться позже.")
    return redirect(f"{reverse('progress_client', args=[client.pk])}?forecast={analysis.pk}#weight-forecast")


@login_required
def weight_forecast_analysis_status(request, pk):
    analysis = get_object_or_404(WeightForecastAnalysis.objects.select_related("client"), pk=pk, requested_by=request.user)
    if not can_manage_or_own_client(request.user, analysis.client):
        raise PermissionDenied("Недостаточно прав для просмотра прогноза веса.")
    mark_stale_analysis_failed(analysis, "ИИ-прогноз веса")
    return no_store_json_response(
        {
            "id": analysis.pk,
            "status": analysis.status,
            "status_label": analysis.get_status_display(),
            "dashboard_url": f"{reverse('progress_client', args=[analysis.client_id])}?forecast={analysis.pk}#weight-forecast",
            "finished": analysis.status in {WeightForecastAnalysis.Status.DONE, WeightForecastAnalysis.Status.FAILED},
        }
    )


def no_store_json_response(payload):
    response = JsonResponse(payload)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def management_ai_analysis_for_dashboard(request):
    analyses = ManagementAIAnalysis.objects.filter(requested_by=request.user)
    analysis_id = request.GET.get("analysis")
    if analysis_id:
        return mark_stale_analysis_failed(analyses.filter(pk=analysis_id).first(), "ИИ-анализ управления")
    return mark_stale_analysis_failed(analyses.first(), "ИИ-анализ управления")


def trainer_ai_analysis_for_dashboard(request, trainer):
    analyses = TrainerAIAnalysis.objects.filter(requested_by=request.user, trainer=trainer)
    analysis_id = request.GET.get("trainer_analysis")
    if analysis_id:
        return mark_stale_analysis_failed(analyses.filter(pk=analysis_id).first(), "ИИ-ассистент тренера")
    return mark_stale_analysis_failed(analyses.first(), "ИИ-ассистент тренера")


def trainer_dashboard_ai_context(request, trainer):
    payload = build_trainer_ai_payload(trainer)
    selected_provider = default_management_ai_provider()
    selected_model_tier = default_management_ai_model_tier()
    provider_label, model = management_ai_provider_context(selected_provider, selected_model_tier)
    context = {
        "trainer_ai_payload": payload,
        "trainer_ai_providers": management_ai_provider_options(),
        "trainer_ai_model_tiers": management_ai_model_tier_options(),
        "trainer_ai_selected_provider": selected_provider,
        "trainer_ai_selected_model_tier": selected_model_tier,
        "trainer_ai_provider": provider_label,
        "trainer_ai_model": model,
    }
    analysis = trainer_ai_analysis_for_dashboard(request, trainer)
    context["trainer_ai_analysis"] = analysis
    if analysis:
        context["trainer_ai_provider"] = ai_provider_label(analysis.provider)
        context["trainer_ai_model"] = analysis.model
        context["trainer_ai_selected_provider"] = normalize_ai_provider(analysis.provider)
        context["trainer_ai_selected_model_tier"] = management_ai_model_tier_for_model(analysis.provider, analysis.model)
        context["trainer_ai_analysis_status_url"] = reverse("trainer_ai_analysis_status", args=[analysis.pk])
        if analysis.status == TrainerAIAnalysis.Status.DONE:
            context["trainer_ai"] = ai_result_for_display(analysis, build_local_trainer_recommendations)
        elif analysis.status == TrainerAIAnalysis.Status.FAILED:
            context["trainer_ai_error"] = analysis.error
    return context


def client_ai_analysis_for_recommendations(request, client):
    analyses = ClientAIAnalysis.objects.filter(requested_by=request.user, client=client)
    analysis_id = request.GET.get("client_analysis")
    if analysis_id:
        return mark_stale_analysis_failed(analyses.filter(pk=analysis_id).first(), "ИИ-коуч клиента")
    return mark_stale_analysis_failed(analyses.first(), "ИИ-коуч клиента")


def client_recommendations_ai_context(request, client):
    payload = build_client_ai_payload(client)
    selected_provider = default_management_ai_provider()
    selected_model_tier = CLIENT_AI_DEFAULT_MODEL_TIER
    provider_label, model = management_ai_provider_context(selected_provider, selected_model_tier)
    context = {
        "client_ai_payload": payload,
        "client_ai_providers": management_ai_provider_options(),
        "client_ai_model_tiers": management_ai_model_tier_options(),
        "client_ai_selected_provider": selected_provider,
        "client_ai_selected_model_tier": selected_model_tier,
        "client_ai_provider": provider_label,
        "client_ai_model": model,
    }
    analysis = client_ai_analysis_for_recommendations(request, client)
    context["client_ai_analysis"] = analysis
    if analysis:
        context["client_ai_provider"] = ai_provider_label(analysis.provider)
        context["client_ai_model"] = analysis.model
        context["client_ai_selected_provider"] = normalize_ai_provider(analysis.provider)
        context["client_ai_selected_model_tier"] = management_ai_model_tier_for_model(analysis.provider, analysis.model)
        context["client_ai_analysis_status_url"] = reverse("client_ai_analysis_status", args=[analysis.pk])
        if analysis.status == ClientAIAnalysis.Status.DONE:
            context["client_ai"] = ai_result_for_display(analysis, build_local_client_recommendations)
        elif analysis.status == ClientAIAnalysis.Status.FAILED:
            context["client_ai_error"] = analysis.error
    return context


def weight_forecast_analysis_for_progress(request, client):
    analyses = WeightForecastAnalysis.objects.filter(requested_by=request.user, client=client)
    analysis_id = request.GET.get("forecast")
    if analysis_id:
        return mark_stale_analysis_failed(analyses.filter(pk=analysis_id).first(), "ИИ-прогноз веса")
    return mark_stale_analysis_failed(analyses.first(), "ИИ-прогноз веса")


def weight_forecast_context(request, client):
    readiness = weight_forecast_readiness(client)
    selected_provider = default_management_ai_provider()
    selected_model_tier = default_management_ai_model_tier()
    provider_label, model = management_ai_provider_context(selected_provider, selected_model_tier)
    context = {
        "weight_forecast_readiness": readiness,
        "weight_forecast_providers": management_ai_provider_options(),
        "weight_forecast_model_tiers": management_ai_model_tier_options(),
        "weight_forecast_selected_provider": selected_provider,
        "weight_forecast_selected_model_tier": selected_model_tier,
        "weight_forecast_provider": provider_label,
        "weight_forecast_model": model,
    }
    analysis = weight_forecast_analysis_for_progress(request, client)
    context["weight_forecast_analysis"] = analysis
    if analysis:
        context["weight_forecast_provider"] = ai_provider_label(analysis.provider)
        context["weight_forecast_model"] = analysis.model
        context["weight_forecast_selected_provider"] = normalize_ai_provider(analysis.provider)
        context["weight_forecast_selected_model_tier"] = management_ai_model_tier_for_model(analysis.provider, analysis.model)
        context["weight_forecast_status_url"] = reverse("weight_forecast_analysis_status", args=[analysis.pk])
        if analysis.status == WeightForecastAnalysis.Status.DONE:
            forecast = analysis.result or build_local_weight_forecast(analysis.payload, analysis.model)
            if forecast.get("raw"):
                forecast = build_local_weight_forecast(analysis.payload, analysis.model)
            context["weight_forecast"] = forecast
            context["weight_forecast_chart"] = json.dumps(weight_forecast_chart(analysis.payload, forecast), ensure_ascii=False)
        elif analysis.status == WeightForecastAnalysis.Status.FAILED:
            if should_repair_failed_weight_forecast(analysis):
                forecast = build_local_weight_forecast(analysis.payload, analysis.model)
                forecast["summary"] = (
                    "Агрегатор не вернул пригодный ИИ-прогноз в старом запросе, поэтому показан осторожный локальный расчет "
                    "по сохраненной истории веса. Для новой ИИ-версии повторите прогноз позже или выберите другой агрегатор."
                )
                context["weight_forecast"] = forecast
                context["weight_forecast_chart"] = json.dumps(weight_forecast_chart(analysis.payload, forecast), ensure_ascii=False)
                context["weight_forecast_error"] = f"Исходный ответ агрегатора: {analysis.error}"
            else:
                context["weight_forecast_error"] = analysis.error
    return context


def should_repair_failed_weight_forecast(analysis):
    error = (analysis.error or "").lower()
    if not (analysis.payload or {}).get("history"):
        return False
    repairable_fragments = (
        "не найден текст",
        "пустое поле content",
        "нет массива choices",
        "пустой ответ",
        "не удалось связаться",
        "ssl",
        "unexpected_eof",
        "urlopen error",
        "eof occurred",
        "timed out",
        "timeout",
    )
    return any(fragment in error for fragment in repairable_fragments)


def nutrition_summary(client, selected_date):
    meals = Meal.objects.filter(client=client, meal_date=selected_date).prefetch_related("items__product")
    meals_by_type = {key: None for key, _ in MealType.choices}
    totals = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    for meal in meals:
        meals_by_type[meal.meal_type] = meal
        meal_totals = meal.totals()
        for key in totals:
            totals[key] += meal_totals[key]

    target = NutritionTarget.objects.filter(client=client, start_date__lte=selected_date).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=selected_date)
    ).first()
    deviation = None
    if target:
        deviation = {
            "calories": round(totals["calories"] - target.calories_target, 1),
            "protein": round(totals["protein"] - target.protein_target, 1),
            "fat": round(totals["fat"] - target.fat_target, 1),
            "carbs": round(totals["carbs"] - target.carbs_target, 1),
        }

    return {
        "meals": meals_by_type,
        "meal_types": MealType.choices,
        "totals": {key: round(value, 1) for key, value in totals.items()},
        "target": target,
        "deviation": deviation,
    }


def client_chart_period_from_request(request):
    value = (request.GET.get("chart_period") or DEFAULT_CLIENT_CHART_PERIOD).strip()
    return value if value in CLIENT_CHART_PERIODS else DEFAULT_CLIENT_CHART_PERIOD


def client_chart_period_options():
    return [{"value": value, "label": data["label"]} for value, data in CLIENT_CHART_PERIODS.items()]


def client_chart_period_label(period):
    return CLIENT_CHART_PERIODS.get(period, CLIENT_CHART_PERIODS[DEFAULT_CLIENT_CHART_PERIOD])["label"]


def client_chart_period_days(period):
    return CLIENT_CHART_PERIODS.get(period, CLIENT_CHART_PERIODS[DEFAULT_CLIENT_CHART_PERIOD])["days"]


def client_chart_start_date(period, today=None):
    today = today or timezone.localdate()
    return today - timedelta(days=client_chart_period_days(period) - 1)


def client_chart_date_bounds(period, today=None):
    end_date = today or timezone.localdate()
    return client_chart_start_date(period, end_date), end_date


def client_chart_dates(period, today=None):
    start_date, end_date = client_chart_date_bounds(period, today)
    return [start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)]


def progress_records_for_period(client, period):
    start_date, end_date = client_chart_date_bounds(period)
    return client.progress_records.filter(record_date__range=(start_date, end_date))


def chart_progress_records_by_date(client, period, today=None):
    start_date, end_date = client_chart_date_bounds(period, today)
    records_by_date = {}
    records = client.progress_records.filter(record_date__range=(start_date, end_date)).order_by("record_date", "id")
    for record in records:
        records_by_date[record.record_date] = record
    return records_by_date


def progress_chart_data(client, period=DEFAULT_CLIENT_CHART_PERIOD):
    dates = client_chart_dates(period)
    records_by_date = chart_progress_records_by_date(client, period)
    return {
        "labels": json.dumps([date.strftime("%d.%m") for date in dates]),
        "weights": json.dumps([float(records_by_date[date].weight) if date in records_by_date else None for date in dates]),
        "waist": json.dumps(
            [float(records_by_date[date].waist) if date in records_by_date and records_by_date[date].waist else None for date in dates]
        ),
        "chest": json.dumps(
            [float(records_by_date[date].chest) if date in records_by_date and records_by_date[date].chest else None for date in dates]
        ),
        "hips": json.dumps(
            [float(records_by_date[date].hips) if date in records_by_date and records_by_date[date].hips else None for date in dates]
        ),
    }


def client_physical_summary(client):
    records = list(client.progress_records.all())
    latest = records[-1] if records else None
    first = records[0] if records else None
    previous = records[-2] if len(records) > 1 else first

    def value(record, attr):
        item = getattr(record, attr, None) if record else None
        return float(item) if item is not None else None

    def change(current, base, suffix):
        if current is None or base is None or current == base:
            return "без изменений" if current is not None else "нет данных"
        diff = current - base
        sign = "+" if diff > 0 else ""
        return f"{sign}{diff:.1f} {suffix}"

    weight = value(latest, "weight")
    waist = value(latest, "waist")
    chest = value(latest, "chest")
    hips = value(latest, "hips")
    bmi = None
    if weight and client.height:
        bmi = round(weight / ((client.height / 100) ** 2), 1)

    return {
        "latest_date": latest.record_date if latest else None,
        "weight": weight,
        "weight_change": change(weight, value(previous, "weight"), "кг"),
        "weight_total_change": change(weight, value(first, "weight"), "кг"),
        "bmi": bmi,
        "waist": waist,
        "waist_change": change(waist, value(previous, "waist"), "см"),
        "chest": chest,
        "chest_change": change(chest, value(previous, "chest"), "см"),
        "hips": hips,
        "hips_change": change(hips, value(previous, "hips"), "см"),
    }


def save_workout_load(client, post_data):
    workout_id = positive_int(post_data.get("workout_id"))
    workout = get_object_or_404(
        CompletedWorkout.objects.select_related("client", "workout_day", "workout_day__workout_plan").prefetch_related(
            "workout_day__exercises__exercise"
        ),
        pk=workout_id,
        client=client,
    )
    for plan_exercise in workout.workout_day.exercises.all():
        prefix = f"exercise_{plan_exercise.id}"
        entry = CompletedExercise.objects.filter(completed_workout=workout, workout_exercise=plan_exercise).first()
        if entry is None:
            entry = CompletedExercise(completed_workout=workout, workout_exercise=plan_exercise)
        entry.is_completed = post_data.get(f"{prefix}_done") == "on"
        entry.actual_sets = positive_int(post_data.get(f"{prefix}_sets"), plan_exercise.sets_count)
        entry.actual_reps = positive_int(post_data.get(f"{prefix}_reps"), plan_exercise.reps_count)
        entry.actual_weight = decimal_or_zero(post_data.get(f"{prefix}_weight"))
        entry.comment = post_data.get(f"{prefix}_comment", "").strip()
        entry.save()


def workout_load_rows(client, period=DEFAULT_CLIENT_CHART_PERIOD):
    start_date, end_date = client_chart_date_bounds(period)
    workouts = (
        CompletedWorkout.objects.filter(client=client, completed_at__date__range=(start_date, end_date))
        .select_related("workout_day", "workout_day__workout_plan")
        .prefetch_related("workout_day__exercises__exercise", "exercises__workout_exercise")
        .order_by("-completed_at")[:8]
    )
    rows = []
    for workout in workouts:
        entries_by_exercise = {entry.workout_exercise_id: entry for entry in workout.exercises.all()}
        entry_rows = []
        total_load = 0
        for plan_exercise in workout.workout_day.exercises.all():
            entry = entries_by_exercise.get(plan_exercise.id)
            is_completed_value = entry.is_completed if entry else True
            sets = entry.actual_sets if entry else plan_exercise.sets_count
            reps = entry.actual_reps if entry else plan_exercise.reps_count
            weight = float(entry.actual_weight) if entry else float(plan_exercise.recommended_weight)
            if is_completed_value:
                total_load += sets * reps * weight
            entry_rows.append(
                {
                    "plan_exercise": plan_exercise,
                    "is_completed": is_completed_value,
                    "sets": sets,
                    "reps": reps,
                    "weight": weight,
                    "comment": entry.comment if entry else "",
                }
            )
        rows.append(
            {
                "workout": workout,
                "entries": entry_rows,
                "total_load": round(total_load, 1),
            }
        )
    return rows


def client_dashboard_charts(client, period=DEFAULT_CLIENT_CHART_PERIOD):
    today = timezone.localdate()
    dates = client_chart_dates(period, today)
    records_by_date = chart_progress_records_by_date(client, period, today)
    labels = [date.strftime("%d.%m") for date in dates]
    data = {
        "clientWeight": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Вес",
                    "values": [float(records_by_date[date].weight) if date in records_by_date else None for date in dates],
                    "color": "#0f766e",
                }
            ],
            "suffix": " кг",
            "emptyText": "Нет замеров за период",
        },
        "clientMeasurements": {
            "labels": labels,
            "datasets": [
                {
                    "label": "Талия",
                    "values": [
                        float(records_by_date[date].waist) if date in records_by_date and records_by_date[date].waist else None
                        for date in dates
                    ],
                    "color": "#c75f28",
                },
                {
                    "label": "Грудь",
                    "values": [
                        float(records_by_date[date].chest) if date in records_by_date and records_by_date[date].chest else None
                        for date in dates
                    ],
                    "color": "#2563eb",
                },
                {
                    "label": "Бедра",
                    "values": [
                        float(records_by_date[date].hips) if date in records_by_date and records_by_date[date].hips else None
                        for date in dates
                    ],
                    "color": "#7c3aed",
                },
            ],
            "suffix": " см",
            "emptyText": "Нет замеров за период",
        },
        "workoutRegularity": workout_regularity_chart(client, today, period),
        "activityCalendar": activity_calendar_chart(client, today, period),
        "planCompletion": plan_completion_chart(client),
        "trainingLoad": training_load_chart(client, period),
    }
    return json.dumps(data, ensure_ascii=False)


def workout_regularity_chart(client, today, period=DEFAULT_CLIENT_CHART_PERIOD):
    start_date, end_date = client_chart_date_bounds(period, today)
    labels = []
    values = []
    week_start = start_date
    while week_start <= end_date:
        week_end = min(week_start + timedelta(days=6), end_date)
        labels.append(f"{week_start:%d.%m}-{week_end:%d.%m}")
        values.append(
            CompletedWorkout.objects.filter(
                client=client,
                completed_at__date__range=(week_start, week_end),
            ).count()
        )
        week_start = week_end + timedelta(days=1)
    chart_values = values if any(values) else [None for _ in values]
    return {
        "labels": labels,
        "datasets": [{"label": "Тренировки", "values": chart_values, "color": "#2563eb"}],
        "emptyText": "Нет тренировок за период",
    }


def activity_calendar_chart(client, today, period=DEFAULT_CLIENT_CHART_PERIOD):
    start, end_date = client_chart_date_bounds(period, today)
    workouts_by_day = {}
    workout_dates = CompletedWorkout.objects.filter(
        client=client,
        completed_at__date__range=(start, end_date),
    ).values_list("completed_at", flat=True)
    for completed_at in workout_dates:
        day = timezone.localtime(completed_at).date()
        workouts_by_day[day] = workouts_by_day.get(day, 0) + 1

    days = []
    days_count = (end_date - start).days + 1
    for offset in range(days_count):
        day = start + timedelta(days=offset)
        value = workouts_by_day.get(day, 0)
        days.append(
            {
                "label": day.strftime("%d.%m"),
                "weekday": day.weekday(),
                "inPeriod": True,
                "value": value,
                "level": min(value, 4),
            }
        )
    return {
        "days": days,
        "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
        "periodLabel": client_chart_period_label(period),
        "hasData": bool(workouts_by_day),
        "emptyText": "Нет активности за период",
    }


def plan_completion_chart(client):
    plan = client.active_plan()
    if not plan:
        return {"labels": [], "values": [], "colors": ["#0f766e", "#dfe5dc"], "centerLabel": "-"}

    total_days = plan.days.count()
    completed_days = (
        CompletedWorkout.objects.filter(client=client, workout_day__workout_plan=plan).values("workout_day_id").distinct().count()
    )
    remaining_days = max(total_days - completed_days, 0)
    percent = round(completed_days / total_days * 100) if total_days else 0
    return {
        "labels": ["Выполнено", "Осталось"],
        "values": [completed_days, remaining_days],
        "colors": ["#0f766e", "#dfe5dc"],
        "centerLabel": f"{percent}%",
    }


def training_load_chart(client, period=DEFAULT_CLIENT_CHART_PERIOD):
    start, end_date = client_chart_date_bounds(period)
    dates = client_chart_dates(period)
    workouts = (
        CompletedWorkout.objects.filter(client=client, completed_at__date__range=(start, end_date))
        .prefetch_related("exercises")
        .order_by("completed_at")
    )
    loads_by_day = {}
    for workout in workouts:
        day = timezone.localtime(workout.completed_at).date()
        load = 0
        for item in workout.exercises.all():
            if item.is_completed:
                load += item.actual_sets * item.actual_reps * float(item.actual_weight)
        loads_by_day[day] = loads_by_day.get(day, 0) + load

    labels = [day.strftime("%d.%m") for day in dates]
    values = [round(loads_by_day.get(day, 0), 1) for day in dates] if loads_by_day else [None for _ in dates]

    return {
        "labels": labels,
        "datasets": [{"label": "Тоннаж", "values": values, "color": "#c75f28"}],
        "emptyText": "Нет тоннажа за период",
    }


def admin_dashboard_context(clients):
    today = timezone.localdate()
    metrics = {
        "total_clients": clients.count(),
        "active_clients": clients.filter(status="active").count(),
        "active_period_clients": clients.filter(
            completed_workouts__completed_at__date__gte=today - timedelta(days=30)
        ).distinct().count(),
        "expiring_memberships": Membership.objects.filter(
            client__in=clients,
            status=MembershipStatus.ACTIVE,
            end_date__range=(today, today + timedelta(days=7)),
        ).select_related("client"),
    }
    risk_rows = risk_clients(clients)
    last_30_days = today - timedelta(days=30)
    workouts_30 = CompletedWorkout.objects.filter(completed_at__date__gte=last_30_days)
    active_plans = WorkoutPlan.objects.filter(is_template=False, status="active").prefetch_related("days")
    completion_values = [plan.completion_percent() for plan in active_plans]
    average_completion = round(sum(completion_values) / len(completion_values), 1) if completion_values else 0
    total_clients = clients.count()
    avg_workouts = round(workouts_30.count() / total_clients, 1) if total_clients else 0

    membership_status_labels = dict(MembershipStatus.choices)
    membership_status_colors = {
        MembershipStatus.ACTIVE: "#0f766e",
        MembershipStatus.PLANNED: "#2563eb",
        MembershipStatus.EXPIRED: "#b42318",
        MembershipStatus.FROZEN: "#b7791f",
    }
    membership_rows = Membership.objects.values("status").annotate(count=Count("id")).order_by("status")
    goal_rows = goal_stats(clients)
    trainer_rows = trainer_load_report(clients)
    activity_chart = weekly_activity_chart(today)
    risk_chart = risk_reason_chart(risk_rows)
    trainer_review_stats = TrainerReview.objects.filter(is_published=True).aggregate(avg=Avg("rating"), count=Count("id"))
    business_kpis = {
        "workouts_30": workouts_30.count(),
        "avg_workouts": avg_workouts,
        "risk_count": len(risk_rows),
        "average_completion": average_completion,
        "expiring_count": metrics["expiring_memberships"].count(),
        "trainer_reviews_count": trainer_review_stats["count"] or 0,
        "trainer_average_rating": round(trainer_review_stats["avg"] or 0, 1),
    }

    charts = {
        "activity": activity_chart,
        "goals": {
            "labels": [row["goal"] for row in goal_rows],
            "values": [row["count"] for row in goal_rows],
            "colors": ["#0f766e", "#c75f28", "#2563eb", "#7c3aed", "#b7791f"],
        },
        "memberships": {
            "labels": [membership_status_labels.get(row["status"], row["status"]) for row in membership_rows],
            "values": [row["count"] for row in membership_rows],
            "colors": [membership_status_colors.get(row["status"], "#6b7280") for row in membership_rows],
        },
        "trainerLoad": {
            "labels": [row["trainer"].full_name for row in trainer_rows],
            "datasets": [
                {"label": "Всего клиентов", "values": [row["clients_count"] for row in trainer_rows], "color": "#0f766e"},
                {"label": "Активных", "values": [row["active_count"] for row in trainer_rows], "color": "#2563eb"},
                {"label": "Низкая активность", "values": [row["low_activity_count"] for row in trainer_rows], "color": "#c75f28"},
            ],
        },
        "risk": risk_chart,
    }

    management_ai_selected_provider = default_management_ai_provider()
    management_ai_selected_model_tier = default_management_ai_model_tier()
    management_ai_provider, management_ai_model = management_ai_provider_context(
        management_ai_selected_provider,
        management_ai_selected_model_tier,
    )
    return {
        "metrics": metrics,
        "business_kpis": business_kpis,
        "goal_stats": goal_rows,
        "trainer_load": trainer_rows,
        "risk_clients": risk_rows[:8],
        "dashboard_charts": json.dumps(charts, ensure_ascii=False),
        "management_ai_payload": management_ai_payload(metrics, business_kpis, goal_rows, trainer_rows, risk_rows, activity_chart),
        "management_ai_providers": management_ai_provider_options(),
        "management_ai_model_tiers": management_ai_model_tier_options(),
        "management_ai_selected_provider": management_ai_selected_provider,
        "management_ai_selected_model_tier": management_ai_selected_model_tier,
        "management_ai_provider": management_ai_provider,
        "management_ai_model": management_ai_model,
    }


def management_ai_provider_context(provider, model_tier=None):
    return ai_provider_label(provider), management_ai_model_for_provider(provider, model_tier)


def ai_result_for_display(analysis, fallback_builder):
    result = analysis.result if isinstance(analysis.result, dict) else {}
    if result.get("raw") and analysis.payload:
        return fallback_builder(analysis.payload, result.get("model") or analysis.model)
    return result


def management_ai_model_for_provider(provider, model_tier=None):
    provider = normalize_ai_provider(provider)
    model_tier = normalize_ai_model_tier(model_tier)
    if provider == "gptunnel":
        if model_tier == "fast":
            return getattr(settings, "GPTUNNEL_FAST_MODEL", "gemini-3.5-flash")
        if model_tier == "smart":
            return getattr(settings, "GPTUNNEL_SMART_MODEL", "gpt-5.5-pro")
        return getattr(
            settings,
            "GPTUNNEL_BALANCED_MODEL",
            getattr(settings, "GPTUNNEL_API_MODEL", "claude-4.6-sonnet"),
        )
    if provider == "openrouter":
        if model_tier == "fast":
            return getattr(settings, "OPENROUTER_FAST_MODEL", "google/gemini-3.5-flash")
        if model_tier == "smart":
            return getattr(settings, "OPENROUTER_SMART_MODEL", "openai/gpt-5.5-pro")
        return getattr(settings, "OPENROUTER_BALANCED_MODEL", getattr(settings, "OPENROUTER_API_MODEL", "anthropic/claude-sonnet-4.6"))
    if model_tier == "fast":
        return getattr(settings, "ZAI_FAST_MODEL", "glm-5-turbo")
    if model_tier == "smart":
        return getattr(settings, "ZAI_SMART_MODEL", "glm-5.1")
    return getattr(settings, "ZAI_BALANCED_MODEL", getattr(settings, "ZAI_API_MODEL", "glm-5"))


def default_management_ai_provider():
    provider = normalize_ai_provider(getattr(settings, "AI_PROVIDER", "gptunnel"))
    return provider if provider in AI_PROVIDER_LABELS else "gptunnel"


def default_management_ai_model_tier():
    return "balanced"


def management_ai_requested_provider(request):
    provider = normalize_ai_provider(request.POST.get("provider") or default_management_ai_provider())
    return provider if provider in AI_PROVIDER_LABELS else default_management_ai_provider()


def management_ai_requested_model_tier(request):
    return normalize_ai_model_tier(request.POST.get("model_tier"))


def normalize_ai_model_tier(value):
    value = (value or "").strip().lower()
    aliases = {
        "quick": "fast",
        "speed": "fast",
        "fast": "fast",
        "быстрая": "fast",
        "balanced": "balanced",
        "optimal": "balanced",
        "оптимальная": "balanced",
        "smart": "smart",
        "heavy": "smart",
        "умная": "smart",
    }
    return aliases.get(value, value) if aliases.get(value, value) in AI_MODEL_TIERS else default_management_ai_model_tier()


def management_ai_model_tier_options():
    return [
        {"value": value, "label": data["label"], "description": data["description"]}
        for value, data in AI_MODEL_TIERS.items()
    ]


def management_ai_model_tier_for_model(provider, model):
    provider = normalize_ai_provider(provider)
    model = (model or "").strip()
    for tier in AI_MODEL_TIERS:
        if management_ai_model_for_provider(provider, tier) == model:
            return tier
    return default_management_ai_model_tier()


def management_ai_provider_options():
    return [{"value": value, "label": label} for value, label in AI_PROVIDER_LABELS.items()]


def management_ai_payload(metrics, business_kpis, goal_rows, trainer_rows, risk_rows, activity_chart):
    kpis = {
        "total_clients": metrics["total_clients"],
        "active_clients": metrics["active_clients"],
        "active_period_clients_30_days": metrics["active_period_clients"],
        "expiring_memberships_7_days": business_kpis["expiring_count"],
        "workouts_30_days": business_kpis["workouts_30"],
        "average_workouts_per_client_30_days": business_kpis["avg_workouts"],
        "active_plan_average_completion_percent": business_kpis["average_completion"],
        "risk_clients_count": len(risk_rows),
        "trainer_reviews_count": business_kpis["trainer_reviews_count"],
        "trainer_average_rating": business_kpis["trainer_average_rating"],
    }
    weekly_activity = [
        {"week_start": label, "workouts": workouts, "active_clients": active_clients}
        for label, workouts, active_clients in zip(
            activity_chart["labels"],
            activity_chart["datasets"][0]["values"],
            activity_chart["datasets"][1]["values"],
        )
    ]
    return {
        "business_kpis": kpis,
        "goal_distribution": goal_rows,
        "weekly_activity": weekly_activity,
        "trainer_load": [
            {
                "trainer": row["trainer"].full_name,
                "specialization": row["trainer"].specialization,
                "clients_count": row["clients_count"],
                "active_count": row["active_count"],
                "low_activity_count": row["low_activity_count"],
                "average_rating": row["average_rating"],
                "reviews_count": row["reviews_count"],
            }
            for row in trainer_rows
        ],
        "risk_clients": [
            {
                "client": row["client"].full_name,
                "trainer": row["client"].trainer.full_name if row["client"].trainer else "не назначен",
                "last_workout_at": row["last"].strftime("%d.%m.%Y %H:%M") if row["last"] else "нет",
                "membership_end": row["membership"].end_date.strftime("%d.%m.%Y") if row["membership"] else "нет",
                "reason": row["reason"],
            }
            for row in risk_rows[:12]
        ],
    }


def weekly_activity_chart(today):
    labels = []
    workout_values = []
    client_values = []
    for offset in range(7, -1, -1):
        week_end = today - timedelta(days=offset * 7)
        week_start = week_end - timedelta(days=6)
        workouts = CompletedWorkout.objects.filter(completed_at__date__range=(week_start, week_end))
        labels.append(week_start.strftime("%d.%m"))
        workout_values.append(workouts.count())
        client_values.append(workouts.values("client_id").distinct().count())
    return {
        "labels": labels,
        "datasets": [
            {"label": "Тренировок", "values": workout_values, "color": "#0f766e"},
            {"label": "Активных клиентов", "values": client_values, "color": "#c75f28"},
        ],
    }


def risk_reason_chart(rows):
    counts = {"Нет тренировок": 0, "Истекает абонемент": 0, "Абонемент завершен": 0}
    for row in rows:
        reason = row["reason"]
        if "нет тренировок" in reason:
            counts["Нет тренировок"] += 1
        if "истекает" in reason:
            counts["Истекает абонемент"] += 1
        if "завершен" in reason:
            counts["Абонемент завершен"] += 1
    return {
        "labels": list(counts.keys()),
        "values": list(counts.values()),
        "colors": ["#c75f28", "#b7791f", "#b42318"],
    }


def goal_stats(clients):
    goal_labels = dict(Goal.choices)
    rows = clients.values("goal").annotate(count=Count("id")).order_by("-count")
    return [{"goal": goal_labels.get(row["goal"], row["goal"]), "count": row["count"]} for row in rows]


def active_clients_report(clients):
    border = timezone.localdate() - timedelta(days=30)
    return clients.filter(completed_workouts__completed_at__date__gte=border).annotate(
        workouts_count=Count("completed_workouts", filter=Q(completed_workouts__completed_at__date__gte=border)),
        last_activity_at=Max("completed_workouts__completed_at"),
    ).distinct()


def risk_clients(clients):
    inactive_border = timezone.localdate() - timedelta(days=14)
    today = timezone.localdate()
    result = []
    for client in clients.select_related("trainer").prefetch_related("memberships"):
        last = client.last_workout_at()
        membership = client.current_membership()
        reasons = []
        if last is None or last.date() < inactive_border:
            reasons.append("нет тренировок более 14 дней")
        if membership and membership.end_date < today:
            reasons.append("абонемент завершен")
        elif membership and membership.expires_soon:
            reasons.append("абонемент истекает")
        if reasons:
            result.append({"client": client, "last": last, "membership": membership, "reason": ", ".join(reasons)})
    return result


def trainer_load_report(clients):
    rows = []
    for trainer in trainer_load_queryset(clients):
        trainer_clients = clients.filter(trainer=trainer)
        review_stats = trainer.trainer_reviews.filter(is_published=True).aggregate(avg=Avg("rating"), count=Count("id"))
        rows.append(
            {
                "trainer": trainer,
                "clients_count": trainer_clients.count(),
                "active_count": trainer_clients.filter(status="active").count(),
                "low_activity_count": len([client for client in trainer_clients if client.is_low_activity()]),
                "average_rating": round(review_stats["avg"] or 0, 1),
                "reviews_count": review_stats["count"] or 0,
            }
        )
    return rows


def booked_slot_ids():
    return TrainingAppointment.objects.filter(status=AppointmentStatus.BOOKED).values_list("slot_id", flat=True)


def schedule_slot_rows(slots):
    rows = []
    for slot in slots:
        appointment = None
        appointments = getattr(slot, "_prefetched_objects_cache", {}).get("appointments")
        if appointments is None:
            appointment = slot.booked_appointment
        else:
            appointment = next((item for item in appointments if item.status == AppointmentStatus.BOOKED), None)
        rows.append(
            {
                "slot": slot,
                "appointment": appointment,
                "is_available": slot.is_active and slot.start_at > timezone.now() and appointment is None,
            }
        )
    return rows


def schedule_week_groups(slot_rows):
    today = timezone.localdate()
    rows = sorted(slot_rows, key=lambda row: row["slot"].start_at)
    if rows:
        first_date = timezone.localtime(rows[0]["slot"].start_at).date()
        last_date = timezone.localtime(rows[-1]["slot"].start_at).date()
    else:
        first_date = today
        last_date = today

    start = first_date - timedelta(days=first_date.weekday())
    end = last_date - timedelta(days=last_date.weekday())
    rows_by_date = {}
    for row in rows:
        slot_date = timezone.localtime(row["slot"].start_at).date()
        rows_by_date.setdefault(slot_date, []).append(row)

    weeks = []
    current = start
    weekday_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    while current <= end:
        days = []
        for offset, weekday in enumerate(weekday_labels):
            date = current + timedelta(days=offset)
            day_rows = rows_by_date.get(date, [])
            days.append(
                {
                    "date": date,
                    "weekday": weekday,
                    "rows": day_rows,
                    "is_today": date == today,
                    "available_count": len([row for row in day_rows if row["is_available"]]),
                    "booked_count": len([row for row in day_rows if row["appointment"]]),
                }
            )
        weeks.append(
            {
                "start": current,
                "end": current + timedelta(days=6),
                "days": days,
                "available_count": sum(day["available_count"] for day in days),
                "booked_count": sum(day["booked_count"] for day in days),
            }
        )
        current += timedelta(days=7)
    return weeks


def schedule_stats(slot_rows, appointments):
    appointment_count = len(appointments) if isinstance(appointments, list) else appointments.count()
    return {
        "available_slots": len([row for row in slot_rows if row["is_available"]]),
        "booked_sessions": appointment_count,
        "cancel_deadline_hours": 24,
    }


def attach_completion_days(appointments):
    for appointment in appointments:
        plan = appointment.client.active_plan()
        appointment.completion_day = next_uncompleted_workout_day(plan, appointment.client) if plan else None
    return appointments


def next_uncompleted_workout_day(plan, client):
    if not plan:
        return None
    completed_day_ids = set(
        CompletedWorkout.objects.filter(
            client=client,
            workout_day__workout_plan=plan,
            exercises__is_completed=True,
        ).values_list("workout_day_id", flat=True)
    )
    return plan.days.exclude(id__in=completed_day_ids).prefetch_related("exercises__exercise").first()


def completed_at_from_post(post_data, fallback=None):
    fallback = fallback or timezone.now()
    fallback_local = timezone.localtime(fallback)
    selected_date = parse_date(post_data.get("completed_date") or "") or fallback_local.date()
    selected_time = parse_time(post_data.get("completed_time") or "") or fallback_local.time().replace(second=0, microsecond=0)
    return timezone.make_aware(datetime.combine(selected_date, selected_time))


def workout_appointment_from_request(request, client, plan, completed_at=None):
    appointment_id = positive_int(request.POST.get("appointment_id") or request.GET.get("appointment"))
    if appointment_id:
        appointment = get_object_or_404(
            TrainingAppointment.objects.select_related("slot", "slot__trainer"),
            pk=appointment_id,
            client=client,
            status=AppointmentStatus.BOOKED,
        )
        if plan.trainer_id and appointment.slot.trainer_id != plan.trainer_id:
            raise PermissionDenied("Запись относится к другому тренеру.")
        return appointment

    if completed_at is None:
        return None

    appointments = TrainingAppointment.objects.filter(
        client=client,
        status=AppointmentStatus.BOOKED,
        slot__start_at__date=timezone.localtime(completed_at).date(),
        slot__start_at__lte=completed_at,
    ).select_related("slot", "slot__trainer")
    if plan.trainer_id:
        appointments = appointments.filter(slot__trainer_id=plan.trainer_id)
    appointments = list(appointments.order_by("slot__start_at")[:2])
    return appointments[0] if len(appointments) == 1 else None


def consume_membership_visit(membership):
    membership.visits_left = max(membership.visits_left - 1, 0)
    membership.save()


def restore_membership_visit(client):
    membership = client.current_membership()
    if membership and membership.visits_left < membership.visits_total:
        membership.visits_left += 1
        membership.save()


def resolve_client_context(request, client_id):
    if client_id:
        return get_client_for_user(request.user, client_id)
    if is_client(request.user):
        client = client_profile_for(request.user)
        if client:
            return client
    raise PermissionDenied("Выберите клиента для этого раздела.")


def can_manage_or_own_client(user, client):
    return can_manage_client(user, client) or (is_client(user) and client_profile_for(user) == client)


def clone_template_days(template, plan):
    for source_day in template.days.prefetch_related("exercises__exercise"):
        day = WorkoutDay.objects.create(
            workout_plan=plan,
            title=source_day.title,
            day_number=source_day.day_number,
            description=source_day.description,
        )
        for source_exercise in source_day.exercises.all():
            WorkoutExercise.objects.create(
                workout_day=day,
                exercise=source_exercise.exercise,
                sets_count=source_exercise.sets_count,
                reps_count=source_exercise.reps_count,
                recommended_weight=source_exercise.recommended_weight,
                rest_seconds=source_exercise.rest_seconds,
                comment=source_exercise.comment,
                order_number=source_exercise.order_number,
            )


def positive_int(value, default=0):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(number, 0)


def decimal_or_zero(value):
    try:
        return max(float(value), 0)
    except (TypeError, ValueError):
        return 0

# Create your views here.
