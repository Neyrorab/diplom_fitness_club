from datetime import timedelta

from django.utils import timezone

from .models import AppointmentStatus, CompletedWorkout, MembershipStatus, ProgressRecord, ScheduleSlot, TrainingAppointment, WorkoutPlan


def build_trainer_ai_payload(trainer):
    today = timezone.localdate()
    now = timezone.now()
    week_end = today + timedelta(days=7)
    clients = trainer.clients.select_related("user", "trainer").prefetch_related(
        "memberships",
        "progress_records",
        "nutrition_targets",
        "workout_plans__days__exercises__exercise",
    )
    client_rows = [build_client_row(client, today) for client in clients]
    high_count = sum(1 for row in client_rows for signal in row["signals"] if signal["severity"] == "high")
    medium_count = sum(1 for row in client_rows for signal in row["signals"] if signal["severity"] == "medium")
    expiring_count = sum(1 for row in client_rows if row["membership"].get("expires_soon"))
    no_plan_count = sum(1 for row in client_rows if not row["active_plan"])
    low_activity_count = sum(1 for row in client_rows if row["days_since_last_workout"] is None or row["days_since_last_workout"] > 14)
    workouts_30 = sum(row["workouts_30_days"] for row in client_rows)
    active_30 = sum(1 for row in client_rows if row["workouts_30_days"] > 0)
    booked_next_7 = TrainingAppointment.objects.filter(
        slot__trainer=trainer,
        status=AppointmentStatus.BOOKED,
        slot__start_at__gte=now,
        slot__start_at__date__lte=week_end,
    ).count()
    slot_ids_booked = TrainingAppointment.objects.filter(status=AppointmentStatus.BOOKED).values_list("slot_id", flat=True)
    free_next_7 = (
        ScheduleSlot.objects.filter(trainer=trainer, is_active=True, start_at__gte=now, start_at__date__lte=week_end)
        .exclude(id__in=slot_ids_booked)
        .count()
    )

    return {
        "today": today.strftime("%d.%m.%Y"),
        "scope": "Только клиенты, закрепленные за тренером",
        "trainer": {
            "name": trainer.full_name,
            "specialization": trainer.specialization,
            "clients_count": len(client_rows),
            "active_clients_30_days": active_30,
            "low_activity_clients": low_activity_count,
            "expiring_memberships_7_days": expiring_count,
            "clients_without_active_plan": no_plan_count,
            "workouts_30_days": workouts_30,
            "average_workouts_per_client_30_days": round(workouts_30 / len(client_rows), 1) if client_rows else 0,
            "booked_sessions_next_7_days": booked_next_7,
            "free_slots_next_7_days": free_next_7,
            "high_signals": high_count,
            "medium_signals": medium_count,
        },
        "scenario_counts": {
            "contact_today": count_clients_with_scenario(client_rows, "contact_today"),
            "plan_adjustment": count_clients_with_scenario(client_rows, "plan_adjustment"),
            "activity_drop": count_clients_with_scenario(client_rows, "activity_drop"),
            "motivational_message": count_clients_with_scenario(client_rows, "motivational_message"),
            "workout_preparation": count_clients_with_scenario(client_rows, "workout_preparation"),
            "renewal_support": count_clients_with_scenario(client_rows, "renewal_support"),
        },
        "clients": client_rows,
    }


def build_client_row(client, today):
    membership = client.current_membership()
    active_plan = client.active_plan()
    last_workout = client.last_workout()
    last_workout_at = timezone.localtime(last_workout.completed_at) if last_workout else None
    days_since_last = (today - last_workout_at.date()).days if last_workout_at else None
    workouts_7 = completed_workouts_count(client, today - timedelta(days=7))
    workouts_30 = completed_workouts_count(client, today - timedelta(days=30))
    previous_30 = CompletedWorkout.objects.filter(
        client=client,
        completed_at__date__gte=today - timedelta(days=60),
        completed_at__date__lt=today - timedelta(days=30),
    ).count()
    progress = progress_summary(client, today)
    plan = plan_summary(active_plan, client, today)
    next_appointment = next_client_appointment(client)
    row = {
        "id": client.id,
        "name": client.full_name,
        "goal": client.get_goal_display(),
        "training_level": client.training_level or "не указан",
        "health_limitations": client.health_limitations or "",
        "status": client.get_status_display(),
        "workouts_7_days": workouts_7,
        "workouts_30_days": workouts_30,
        "workouts_previous_30_days": previous_30,
        "last_workout_at": last_workout_at.strftime("%d.%m.%Y %H:%M") if last_workout_at else "нет",
        "last_workout_mood": last_workout.mood if last_workout else "",
        "last_workout_comment": last_workout.comment if last_workout else "",
        "days_since_last_workout": days_since_last,
        "membership": membership_summary(membership, today),
        "active_plan": plan,
        "next_appointment": appointment_summary(next_appointment),
        "progress": progress,
    }
    row["signals"] = client_signals(row, today)
    row["recommended_focus"] = recommended_focus(row["signals"])
    return row


def completed_workouts_count(client, border_date):
    return CompletedWorkout.objects.filter(client=client, completed_at__date__gte=border_date).count()


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
    completion = plan.completion_percent()
    plan_age_days = (today - plan.start_date).days if plan.start_date else 0
    return {
        "title": plan.title,
        "goal": plan.get_goal_display(),
        "status": plan.get_status_display(),
        "start_date": plan.start_date.strftime("%d.%m.%Y") if plan.start_date else "",
        "end_date": plan.end_date.strftime("%d.%m.%Y") if plan.end_date else "",
        "age_days": plan_age_days,
        "days_count": plan.days.count(),
        "completion_percent": completion,
        "next_workout": next_workout_summary(next_day),
    }


def next_workout_summary(day):
    if not day:
        return None
    exercises = []
    for item in day.exercises.select_related("exercise")[:6]:
        weight = f", {item.recommended_weight:g} кг" if item.recommended_weight else ""
        exercises.append(
            {
                "name": item.exercise.name,
                "muscle_group": item.exercise.muscle_group,
                "sets": item.sets_count,
                "reps": item.reps_count,
                "weight": float(item.recommended_weight),
                "label": f"{item.exercise.name}: {item.sets_count}x{item.reps_count}{weight}",
            }
        )
    return {
        "title": day.title,
        "day_number": day.day_number,
        "description": day.description,
        "exercises": exercises,
    }


def progress_summary(client, today):
    records = list(ProgressRecord.objects.filter(client=client).order_by("-record_date")[:2])
    latest = records[0] if records else None
    previous = records[1] if len(records) > 1 else None
    if not latest:
        return {"latest_date": "нет", "days_since_update": None, "weight": None, "weight_delta": None, "waist_delta": None}
    return {
        "latest_date": latest.record_date.strftime("%d.%m.%Y"),
        "days_since_update": (today - latest.record_date).days,
        "weight": float(latest.weight),
        "weight_delta": numeric_delta(latest.weight, getattr(previous, "weight", None)),
        "waist_delta": numeric_delta(latest.waist, getattr(previous, "waist", None)),
    }


def next_client_appointment(client):
    return (
        TrainingAppointment.objects.filter(
            client=client,
            status=AppointmentStatus.BOOKED,
            slot__start_at__gte=timezone.now(),
        )
        .select_related("slot", "slot__trainer")
        .order_by("slot__start_at")
        .first()
    )


def appointment_summary(appointment):
    if not appointment:
        return {"start_at": "нет", "end_at": "нет"}
    return {
        "start_at": timezone.localtime(appointment.slot.start_at).strftime("%d.%m.%Y %H:%M"),
        "end_at": timezone.localtime(appointment.slot.end_at).strftime("%d.%m.%Y %H:%M"),
        "trainer": appointment.slot.trainer.full_name,
    }


def numeric_delta(current, previous):
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), 1)


def client_signals(row, today):
    signals = []
    days_since_last = row["days_since_last_workout"]
    membership = row["membership"]
    plan = row["active_plan"]
    workouts_30 = row["workouts_30_days"]
    previous_30 = row["workouts_previous_30_days"]

    if days_since_last is None:
        add_signal(
            signals,
            "no_workouts_yet",
            "high",
            "Нет выполненных тренировок",
            "Клиент закреплен за тренером, но не имеет отметок о выполненных тренировках.",
            "contact_today",
        )
    elif days_since_last > 14:
        add_signal(
            signals,
            "no_recent_activity",
            "high",
            f"Нет тренировок {days_since_last} дней",
            "Пауза более 14 дней повышает риск ухода и снижает шанс продления.",
            "contact_today",
        )
    elif row["workouts_7_days"] == 0 and workouts_30 >= 2:
        add_signal(
            signals,
            "activity_drop",
            "medium",
            "Пауза на текущей неделе",
            "В течение 30 дней тренировки были, но за последние 7 дней активности нет.",
            "activity_drop",
        )

    if workouts_30 < 4:
        add_signal(
            signals,
            "low_regular_activity",
            "medium",
            "Низкая регулярность",
            f"За 30 дней зафиксировано {workouts_30} тренировок; стоит вывести клиента хотя бы на 1-2 занятия в неделю.",
            "motivational_message",
        )
    if previous_30 >= 4 and workouts_30 < previous_30 / 2:
        add_signal(
            signals,
            "activity_decline",
            "high",
            "Снижение активности",
            f"В прошлом 30-дневном периоде было {previous_30} тренировок, сейчас {workouts_30}.",
            "activity_drop",
        )

    if not plan:
        add_signal(
            signals,
            "no_active_plan",
            "high",
            "Нет активного плана",
            "Клиенту сложно удерживать регулярность без понятного ближайшего плана.",
            "plan_adjustment",
        )
    elif plan["days_count"] and plan["completion_percent"] < 50 and plan["age_days"] >= 7:
        add_signal(
            signals,
            "low_plan_completion",
            "medium",
            "План выполняется слабо",
            f"Выполнение активного плана: {plan['completion_percent']}%. Нужна корректировка сложности или расписания.",
            "plan_adjustment",
        )

    if membership.get("expires_soon"):
        add_signal(
            signals,
            "membership_expires_soon",
            "high",
            "Абонемент скоро закончится",
            f"До окончания {membership['days_left']} дней; тренеру стоит подготовить итог прогресса и следующий тренировочный цикл.",
            "renewal_support",
        )
    elif membership.get("is_expired"):
        add_signal(
            signals,
            "membership_expired",
            "high",
            "Абонемент завершен",
            "Без контакта после завершения абонемента клиент может не вернуться к тренировкам.",
            "renewal_support",
        )

    if plan and plan.get("next_workout"):
        add_signal(
            signals,
            "next_workout_ready",
            "low",
            "Есть ближайшая тренировка",
            f"Следующий блок: {plan['next_workout']['title']}. Тренер может заранее проверить нагрузку и ограничения.",
            "workout_preparation",
        )

    if row["health_limitations"]:
        add_signal(
            signals,
            "health_limitations",
            "medium",
            "Есть ограничения по здоровью",
            "Рекомендации и нагрузку нужно сверять с указанными ограничениями клиента.",
            "plan_adjustment",
        )

    if row["next_appointment"]["start_at"] == "нет" and (workouts_30 < 4 or (days_since_last is not None and days_since_last > 7)):
        add_signal(
            signals,
            "no_upcoming_appointment",
            "medium",
            "Нет будущей записи",
            "У клиента нет запланированного занятия, поэтому тренеру стоит предложить конкретное свободное время.",
            "workout_preparation",
        )

    progress = row["progress"]
    if progress["days_since_update"] is None or progress["days_since_update"] > 30:
        add_signal(
            signals,
            "progress_check_needed",
            "low",
            "Нужен контроль прогресса",
            "Давно не обновлялись замеры; тренеру стоит назначить контрольную точку.",
            "motivational_message",
        )

    return signals


def add_signal(signals, signal_id, severity, title, detail, scenario):
    signals.append(
        {
            "id": signal_id,
            "severity": severity,
            "title": title,
            "detail": detail,
            "scenario": scenario,
        }
    )


def recommended_focus(signals):
    for severity in ("high", "medium", "low"):
        for signal in signals:
            if signal["severity"] == severity:
                return signal["title"]
    return "Поддержать текущий темп"


def count_clients_with_scenario(rows, scenario):
    return sum(1 for row in rows if any(signal["scenario"] == scenario for signal in row["signals"]))
