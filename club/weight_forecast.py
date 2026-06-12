from datetime import datetime, timedelta

from django.utils import timezone

from .models import CompletedWorkout, Meal, NutritionTarget


MIN_WEIGHT_FORECAST_RECORDS = 4
MIN_WEIGHT_FORECAST_SPAN_DAYS = 21
WEIGHT_FORECAST_HORIZON_DAYS = 56
WEIGHT_FORECAST_STEP_DAYS = 7


def weight_forecast_readiness(client):
    records = list(client.progress_records.order_by("record_date", "id").only("id", "client_id", "record_date", "weight"))
    count = len(records)
    span_days = (records[-1].record_date - records[0].record_date).days if count >= 2 else 0
    reasons = []
    if count < MIN_WEIGHT_FORECAST_RECORDS:
        reasons.append(f"нужно минимум {MIN_WEIGHT_FORECAST_RECORDS} записи веса")
    if span_days < MIN_WEIGHT_FORECAST_SPAN_DAYS:
        reasons.append(f"период наблюдений должен быть не короче {MIN_WEIGHT_FORECAST_SPAN_DAYS} дней")

    return {
        "can_run": not reasons,
        "records_count": count,
        "span_days": span_days,
        "min_records": MIN_WEIGHT_FORECAST_RECORDS,
        "min_span_days": MIN_WEIGHT_FORECAST_SPAN_DAYS,
        "reasons": reasons,
    }


def build_weight_forecast_payload(client):
    today = timezone.localdate()
    records = list(client.progress_records.order_by("record_date", "id"))
    first = records[0]
    latest = records[-1]
    span_days = max((latest.record_date - first.record_date).days, 1)
    total_delta = float(latest.weight - first.weight)
    weekly_delta = round(total_delta / span_days * 7, 2)

    recent_start = today - timedelta(days=29)
    workouts_30 = CompletedWorkout.objects.filter(client=client, completed_at__date__gte=recent_start)
    meals = Meal.objects.filter(client=client, meal_date__gte=today - timedelta(days=13)).prefetch_related("items")
    nutrition = nutrition_period_summary(meals, 14)
    nutrition_target = (
        NutritionTarget.objects.filter(client=client, start_date__lte=today)
        .filter(end_date__isnull=True)
        .first()
        or NutritionTarget.objects.filter(client=client, start_date__lte=today, end_date__gte=today).first()
    )
    plan = client.active_plan()

    return {
        "client": {
            "name": client.full_name,
            "goal": client.get_goal_display(),
            "training_level": client.training_level or "не указан",
            "height_cm": client.height,
            "trainer": client.trainer.full_name if client.trainer else "не назначен",
        },
        "history": [
            {
                "date": record.record_date.isoformat(),
                "weight": float(record.weight),
                "waist": float(record.waist) if record.waist else None,
                "chest": float(record.chest) if record.chest else None,
                "hips": float(record.hips) if record.hips else None,
                "comment": record.comment,
            }
            for record in records[-16:]
        ],
        "history_stats": {
            "records_count": len(records),
            "span_days": span_days,
            "first_weight": float(first.weight),
            "latest_weight": float(latest.weight),
            "total_delta_kg": round(total_delta, 1),
            "weekly_delta_kg": weekly_delta,
        },
        "context": {
            "today": today.isoformat(),
            "workouts_30_days": workouts_30.count(),
            "workout_days_30": workouts_30.values("completed_at__date").distinct().count(),
            "nutrition_days_14": nutrition["days_with_food"],
            "nutrition_averages_14": nutrition["averages"],
            "nutrition_target": nutrition_target_payload(nutrition_target),
            "nutrition_deviation_14": nutrition_deviation(nutrition["averages"], nutrition_target),
            "active_plan": {
                "title": plan.title,
                "completion_percent": plan.completion_percent(),
                "goal": plan.get_goal_display(),
            }
            if plan
            else None,
        },
        "forecast_request": {
            "horizon_days": WEIGHT_FORECAST_HORIZON_DAYS,
            "step_days": WEIGHT_FORECAST_STEP_DAYS,
            "expected_points": WEIGHT_FORECAST_HORIZON_DAYS // WEIGHT_FORECAST_STEP_DAYS,
            "required_series": ["probable", "lower", "upper"],
        },
    }


def nutrition_period_summary(meals, period_days):
    daily = {}
    for meal in meals:
        totals = meal.totals()
        day = meal.meal_date.isoformat()
        daily.setdefault(day, {"calories": 0, "protein": 0, "fat": 0, "carbs": 0})
        for key, value in totals.items():
            daily[day][key] += value

    days_with_food = len(daily)
    divisor = days_with_food or period_days
    averages = {
        key: round(sum(day[key] for day in daily.values()) / divisor, 1) if divisor else 0
        for key in ("calories", "protein", "fat", "carbs")
    }
    return {"days_with_food": days_with_food, "averages": averages}


def nutrition_target_payload(target):
    if not target:
        return None
    return {
        "calories": target.calories_target,
        "protein": target.protein_target,
        "fat": target.fat_target,
        "carbs": target.carbs_target,
    }


def nutrition_deviation(averages, target):
    if not target:
        return None
    return {
        "calories": round(averages["calories"] - target.calories_target, 1),
        "protein": round(averages["protein"] - target.protein_target, 1),
        "fat": round(averages["fat"] - target.fat_target, 1),
        "carbs": round(averages["carbs"] - target.carbs_target, 1),
    }


def weight_forecast_chart(payload, forecast):
    history = payload.get("history") or []
    points = forecast.get("points") or []
    labels = []
    fact_values = []
    probable_values = []
    lower_values = []
    upper_values = []

    for item in history:
        labels.append(format_chart_date(item.get("date")))
        fact_values.append(item.get("weight"))
        probable_values.append(None)
        lower_values.append(None)
        upper_values.append(None)

    if history and points:
        last_weight = history[-1].get("weight")
        probable_values[-1] = last_weight
        lower_values[-1] = last_weight
        upper_values[-1] = last_weight

    for point in points:
        labels.append(format_chart_date(point.get("date")))
        fact_values.append(None)
        probable_values.append(point.get("probable"))
        lower_values.append(point.get("lower"))
        upper_values.append(point.get("upper"))

    return {
        "labels": labels,
        "datasets": [
            {"label": "Фактический вес", "values": fact_values, "color": "#0f766e"},
            {"label": "Вероятный вариант", "values": probable_values, "color": "#2563eb"},
            {"label": "Нижняя граница", "values": lower_values, "color": "#65a30d", "dash": [7, 5]},
            {"label": "Верхняя граница", "values": upper_values, "color": "#c75f28", "dash": [7, 5]},
        ],
        "suffix": " кг",
        "ySteps": 8,
        "emptyText": "Недостаточно данных для прогноза",
    }


def format_chart_date(value):
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m")
    except ValueError:
        return str(value or "")
