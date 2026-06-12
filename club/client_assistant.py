from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import AppointmentStatus, CompletedWorkout, Meal, MembershipStatus, NutritionTarget
from .recommendations import build_client_recommendations


CLIENT_AI_RECENT_WORKOUTS_LIMIT = 3
CLIENT_AI_NEXT_WORKOUT_EXERCISES_LIMIT = 4
CLIENT_AI_RECENT_PROGRESS_LIMIT = 3
CLIENT_AI_RECOMMENDATIONS_LIMIT = 3
CLIENT_AI_SIGNALS_LIMIT = 4


def build_client_ai_payload(client, recommendations=None):
    today = timezone.localdate()
    recommendations = recommendations or build_client_recommendations(client)
    membership = client.current_membership()
    plan = client.active_plan()
    target = current_nutrition_target(client, today)
    nutrition = nutrition_summary(client, today, target)
    progress = progress_summary(client, today)
    workouts = workout_summary(client, today)

    return {
        "today": today.strftime("%d.%m.%Y"),
        "scope": "Только данные текущего клиента",
        "client": {
            "id": client.id,
            "name": client.full_name,
            "goal": client.get_goal_display(),
            "training_level": client.training_level or "не указан",
            "health_limitations": client.health_limitations or "",
            "height_cm": client.height,
            "status": client.get_status_display(),
            "trainer": client.trainer.full_name if client.trainer else "не назначен",
        },
        "membership": membership_summary(membership, today),
        "active_plan": plan_summary(plan, client, today),
        "workouts": workouts,
        "nutrition": nutrition,
        "progress": progress,
        "schedule": {"next_appointment": next_appointment_summary(client)},
        "local_recommendations": {
            "score": recommendations["score"],
            "signals": recommendations["signals"][:CLIENT_AI_SIGNALS_LIMIT],
            "items": [
                compact_recommendation_item(item)
                for item in recommendations["items"][:CLIENT_AI_RECOMMENDATIONS_LIMIT]
            ],
        },
    }


def compact_text(value, limit=140):
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def compact_recommendation_item(item):
    return {
        "category": item.get("category", ""),
        "priority": item.get("priority", ""),
        "title": compact_text(item.get("title", ""), 70),
        "reason": compact_text(item.get("reason", ""), 90),
        "action": compact_text(item.get("action", ""), 110),
    }


def membership_summary(membership, today):
    if not membership:
        return {"status": "нет", "type": "", "end_date": "нет", "days_left": None, "visits_left": 0, "expires_soon": False}
    return {
        "status": membership.get_status_display(),
        "type": membership.type,
        "end_date": membership.end_date.strftime("%d.%m.%Y"),
        "days_left": (membership.end_date - today).days,
        "visits_left": membership.visits_left,
        "expires_soon": membership.expires_soon,
        "is_expired": membership.status == MembershipStatus.EXPIRED,
    }


def plan_summary(plan, client, today):
    if not plan:
        return None

    completed_day_ids = set(
        CompletedWorkout.objects.filter(client=client, workout_day__workout_plan=plan).values_list("workout_day_id", flat=True)
    )
    next_day = plan.days.exclude(id__in=completed_day_ids).prefetch_related("exercises__exercise").first()
    return {
        "title": plan.title,
        "goal": plan.get_goal_display(),
        "status": plan.get_status_display(),
        "age_days": (today - plan.start_date).days if plan.start_date else 0,
        "days_count": plan.days.count(),
        "completion_percent": plan.completion_percent(),
        "next_workout": next_workout_summary(next_day),
    }


def next_workout_summary(day):
    if not day:
        return None
    exercises = []
    for item in day.exercises.select_related("exercise")[:CLIENT_AI_NEXT_WORKOUT_EXERCISES_LIMIT]:
        exercises.append(
            {
                "name": item.exercise.name,
                "sets": item.sets_count,
                "reps": item.reps_count,
                "weight": float(item.recommended_weight),
            }
        )
    return {
        "title": day.title,
        "day_number": day.day_number,
        "description": compact_text(day.description, 180),
        "exercises": exercises,
    }


def workout_summary(client, today):
    last_workout = client.last_workout()
    last_workout_at = timezone.localtime(last_workout.completed_at) if last_workout else None
    recent = (
        CompletedWorkout.objects.filter(client=client)
        .select_related("workout_day")
        .order_by("-completed_at")[:CLIENT_AI_RECENT_WORKOUTS_LIMIT]
    )
    return {
        "workouts_7_days": completed_workouts_count(client, today - timedelta(days=7)),
        "workouts_14_days": completed_workouts_count(client, today - timedelta(days=14)),
        "workouts_30_days": completed_workouts_count(client, today - timedelta(days=30)),
        "last_workout_at": last_workout_at.strftime("%d.%m.%Y %H:%M") if last_workout_at else "нет",
        "days_since_last_workout": (today - last_workout_at.date()).days if last_workout_at else None,
        "last_workout_mood": last_workout.mood if last_workout else "",
        "last_workout_comment": compact_text(last_workout.comment, 160) if last_workout else "",
        "recent_workouts": [
            {
                "date": timezone.localtime(item.completed_at).strftime("%d.%m.%Y"),
                "day": item.workout_day.title,
                "mood": item.mood,
            }
            for item in recent
        ],
    }


def completed_workouts_count(client, border_date):
    return CompletedWorkout.objects.filter(client=client, completed_at__date__gte=border_date).count()


def current_nutrition_target(client, today):
    return (
        NutritionTarget.objects.filter(client=client, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .first()
    )


def nutrition_summary(client, today, target):
    start = today - timedelta(days=6)
    meals = Meal.objects.filter(client=client, meal_date__range=(start, today)).prefetch_related("items")
    daily = {}
    for meal in meals:
        totals = meal.totals()
        day = daily.setdefault(meal.meal_date, {"calories": 0, "protein": 0, "fat": 0, "carbs": 0})
        for key, value in totals.items():
            day[key] += value

    days_with_food = len(daily)
    if days_with_food:
        averages = {
            key: round(sum(day[key] for day in daily.values()) / days_with_food, 1)
            for key in ("calories", "protein", "fat", "carbs")
        }
    else:
        averages = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}

    target_payload = None
    deviation = None
    if target:
        target_payload = {
            "calories": target.calories_target,
            "protein": target.protein_target,
            "fat": target.fat_target,
            "carbs": target.carbs_target,
        }
        deviation = {
            "calories": round(averages["calories"] - target.calories_target, 1),
            "protein": round(averages["protein"] - target.protein_target, 1),
            "fat": round(averages["fat"] - target.fat_target, 1),
            "carbs": round(averages["carbs"] - target.carbs_target, 1),
        }

    return {
        "period": f"{start:%d.%m.%Y}-{today:%d.%m.%Y}",
        "days_with_food": days_with_food,
        "averages": averages,
        "target": target_payload,
        "deviation": deviation,
    }


def progress_summary(client, today):
    records_count = client.progress_records.count()
    first = client.progress_records.order_by("record_date", "id").first()
    records = list(client.progress_records.order_by("-record_date", "-id")[:CLIENT_AI_RECENT_PROGRESS_LIMIT])
    records.reverse()
    latest = records[-1] if records else None
    previous = records[-2] if len(records) > 1 else None
    weight = float(latest.weight) if latest else None
    bmi = round(weight / ((client.height / 100) ** 2), 1) if weight and client.height else None
    return {
        "records_count": records_count,
        "history_period": progress_history_period(first, latest),
        "latest_date": latest.record_date.strftime("%d.%m.%Y") if latest else "нет",
        "days_since_update": (today - latest.record_date).days if latest else None,
        "weight": weight,
        "bmi": bmi,
        "weight_delta_last": numeric_delta(latest.weight if latest else None, previous.weight if previous else None),
        "weight_delta_total": numeric_delta(latest.weight if latest else None, first.weight if first else None),
        "waist": float(latest.waist) if latest and latest.waist else None,
        "chest": float(latest.chest) if latest and latest.chest else None,
        "hips": float(latest.hips) if latest and latest.hips else None,
        "recent_records": [
            {
                "date": record.record_date.strftime("%d.%m.%Y"),
                "weight": float(record.weight),
                "waist": float(record.waist) if record.waist else None,
                "chest": float(record.chest) if record.chest else None,
                "hips": float(record.hips) if record.hips else None,
            }
            for record in records
        ],
    }


def progress_history_period(first, latest):
    if not first or not latest:
        return None
    return f"{first.record_date:%d.%m.%Y}-{latest.record_date:%d.%m.%Y}"


def numeric_delta(current, previous):
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 1)


def next_appointment_summary(client):
    appointment = (
        client.training_appointments.filter(status=AppointmentStatus.BOOKED, slot__start_at__gte=timezone.now())
        .select_related("slot", "slot__trainer")
        .order_by("slot__start_at")
        .first()
    )
    if not appointment:
        return {"start_at": "нет", "trainer": client.trainer.full_name if client.trainer else "не назначен"}
    return {
        "start_at": timezone.localtime(appointment.slot.start_at).strftime("%d.%m.%Y %H:%M"),
        "end_at": timezone.localtime(appointment.slot.end_at).strftime("%d.%m.%Y %H:%M"),
        "trainer": appointment.slot.trainer.full_name,
    }
