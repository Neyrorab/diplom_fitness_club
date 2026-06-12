from collections import defaultdict
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import CompletedWorkout, Goal, Meal, MembershipStatus, NutritionTarget


def build_client_recommendations(client):
    today = timezone.localdate()
    progress_records_count = client.progress_records.count()
    records = progress_record_sample(client)
    workouts_30 = CompletedWorkout.objects.filter(client=client, completed_at__date__gte=today - timedelta(days=30)).count()
    workouts_14 = CompletedWorkout.objects.filter(client=client, completed_at__date__gte=today - timedelta(days=14)).count()
    last_workout = client.last_workout_at()
    membership = client.current_membership()
    plan = client.active_plan()
    target = current_nutrition_target(client, today)
    nutrition = nutrition_average(client, today, target)
    progress = progress_summary(records)

    items = []

    if progress_records_count == 0:
        add_item(
            items,
            "Прогресс",
            "high",
            "Добавить стартовые замеры",
            "Сейчас системе не хватает записей веса и объемов, поэтому рекомендации по динамике ограничены.",
            "Добавьте вес, талию, грудь и бедра сегодня, затем повторяйте замер раз в неделю.",
        )
    elif progress_records_count < 3:
        add_item(
            items,
            "Прогресс",
            "medium",
            "Накопить историю замеров",
            "Есть первые данные, но для устойчивого вывода по тренду нужно минимум 3-4 точки.",
            "Запланируйте следующий замер через 7 дней в одинаковых условиях.",
        )

    if last_workout is None:
        add_item(
            items,
            "Тренировки",
            "high",
            "Начать фиксацию тренировок",
            "В системе пока нет выполненных тренировок, поэтому тренер не видит фактическую регулярность.",
            "Выполните ближайший тренировочный день и отметьте упражнения в личном кабинете.",
        )
    elif last_workout.date() < today - timedelta(days=14):
        add_item(
            items,
            "Тренировки",
            "high",
            "Вернуть регулярность занятий",
            f"Последняя тренировка была {last_workout.date():%d.%m.%Y}; это повышает риск потери результата.",
            "Поставьте 2 короткие тренировки на ближайшие 7 дней и начните с умеренной нагрузки.",
        )
    elif workouts_14 < 2:
        add_item(
            items,
            "Тренировки",
            "medium",
            "Увеличить частоту тренировок",
            "За последние 14 дней отмечено меньше двух тренировок.",
            "Доведите регулярность до 2-3 тренировок в неделю, чтобы прогресс стал стабильнее.",
        )

    if plan:
        completion = plan.completion_percent()
        if completion < 35 and workouts_30 > 0:
            add_item(
                items,
                "Тренировочный план",
                "medium",
                "Проверить реалистичность плана",
                f"Текущий план выполнен примерно на {completion}%, хотя тренировки уже фиксируются.",
                "Тренеру стоит убрать лишние дни или заменить сложные упражнения на более доступные.",
            )
        elif completion >= 70:
            add_item(
                items,
                "Тренировочный план",
                "low",
                "Подготовить обновление программы",
                f"План выполнен примерно на {completion}%, клиент близок к следующему этапу.",
                "Через 1-2 недели можно повысить нагрузку или обновить тренировочные дни.",
            )

    add_goal_recommendations(items, client, progress, nutrition)
    add_nutrition_recommendations(items, client, target, nutrition)

    if membership:
        if membership.status == MembershipStatus.EXPIRED:
            add_item(
                items,
                "Абонемент",
                "high",
                "Продлить абонемент",
                "Абонемент завершен, клиент может выпасть из регулярных посещений.",
                "Администратору стоит связаться с клиентом и предложить удобный формат продления.",
            )
        elif membership.status == MembershipStatus.FROZEN:
            add_item(
                items,
                "Абонемент",
                "medium",
                "Уточнить дату возвращения",
                "Абонемент заморожен, поэтому тренировки не стоит учитывать как обычный пропуск активности.",
                "Зафиксируйте планируемую дату разморозки и подготовьте мягкий возврат к тренировкам.",
            )
        elif membership.expires_soon:
            add_item(
                items,
                "Абонемент",
                "medium",
                "Напомнить о продлении",
                f"Абонемент заканчивается {membership.end_date:%d.%m.%Y}.",
                "Предложите продление до окончания срока, пока клиент вовлечен в процесс.",
            )

    if workouts_30 >= 8 and progress["weight_delta_total"] is not None:
        add_item(
            items,
            "Мотивация",
            "low",
            "Поддержать текущий режим",
            "Регулярность за последние 30 дней хорошая, данных достаточно для контроля динамики.",
            "Сохраните текущий режим и обсудите с тренером небольшой рост нагрузки.",
        )

    if not items:
        add_item(
            items,
            "Общее",
            "low",
            "Продолжать текущий план",
            "Критичных отклонений по тренировкам, питанию и прогрессу не обнаружено.",
            "Сохраняйте регулярность и обновляйте дневник питания/замеры каждую неделю.",
        )

    priority_order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda item: priority_order[item["priority"]])

    return {
        "score": adherence_score(workouts_30, workouts_14, progress, nutrition, membership),
        "items": items[:8],
        "signals": recommendation_signals(client, workouts_30, workouts_14, progress, nutrition, membership, plan),
        "generated_at": timezone.now(),
    }


def add_item(items, category, priority, title, reason, action):
    items.append(
        {
            "category": category,
            "priority": priority,
            "title": title,
            "reason": reason,
            "action": action,
        }
    )


def progress_record_sample(client):
    first = client.progress_records.order_by("record_date", "id").first()
    latest_records = list(client.progress_records.order_by("-record_date", "-id")[:2])
    latest_records.reverse()

    records = []
    known_ids = set()
    if first:
        records.append(first)
        known_ids.add(first.id)
    for record in latest_records:
        if record.id not in known_ids:
            records.append(record)
            known_ids.add(record.id)
    return records


def progress_summary(records):
    if not records:
        return {
            "latest_weight": None,
            "weight_delta_total": None,
            "weight_delta_last": None,
            "weekly_weight_delta": None,
        }

    first = records[0]
    latest = records[-1]
    previous = records[-2] if len(records) > 1 else first
    days = max((latest.record_date - first.record_date).days, 1)
    total_delta = float(latest.weight - first.weight)
    return {
        "latest_weight": float(latest.weight),
        "weight_delta_total": total_delta,
        "weight_delta_last": float(latest.weight - previous.weight),
        "weekly_weight_delta": round(total_delta / days * 7, 2),
    }


def current_nutrition_target(client, today):
    return (
        NutritionTarget.objects.filter(client=client, start_date__lte=today)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .first()
    )


def nutrition_average(client, today, target):
    start = today - timedelta(days=6)
    meals = Meal.objects.filter(client=client, meal_date__range=(start, today)).prefetch_related("items")
    daily = defaultdict(lambda: {"calories": 0, "protein": 0, "fat": 0, "carbs": 0})
    for meal in meals:
        totals = meal.totals()
        for key, value in totals.items():
            daily[meal.meal_date][key] += value

    days_with_food = len(daily)
    if days_with_food == 0:
        averages = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    else:
        averages = {
            key: round(sum(day[key] for day in daily.values()) / days_with_food, 1)
            for key in ("calories", "protein", "fat", "carbs")
        }

    deviation = None
    if target:
        deviation = {
            "calories": round(averages["calories"] - target.calories_target, 1),
            "protein": round(averages["protein"] - target.protein_target, 1),
            "fat": round(averages["fat"] - target.fat_target, 1),
            "carbs": round(averages["carbs"] - target.carbs_target, 1),
        }

    return {
        "days_with_food": days_with_food,
        "averages": averages,
        "deviation": deviation,
        "target": target,
    }


def add_goal_recommendations(items, client, progress, nutrition):
    weekly_delta = progress["weekly_weight_delta"]
    if weekly_delta is None:
        return

    if client.goal == Goal.WEIGHT_LOSS:
        if weekly_delta >= -0.1:
            add_item(
                items,
                "Цель",
                "medium",
                "Скорректировать темп снижения веса",
                "Вес снижается слишком медленно или стоит на месте относительно цели похудения.",
                "Проверьте дневник питания за неделю и добавьте 1 кардио-сессию низкой интенсивности.",
            )
        elif weekly_delta < -1.2:
            add_item(
                items,
                "Цель",
                "medium",
                "Не снижать вес слишком резко",
                "Темп снижения веса выше комфортного диапазона, это может ухудшить восстановление.",
                "Обсудите с тренером повышение калорийности или снижение объема кардио.",
            )
    elif client.goal == Goal.MUSCLE_GAIN:
        if weekly_delta <= 0.1:
            add_item(
                items,
                "Цель",
                "medium",
                "Добавить ресурс для набора массы",
                "Вес почти не растет, для набора мышечной массы может не хватать питания или прогрессии нагрузок.",
                "Увеличьте среднюю калорийность на 150-250 ккал и отслеживайте рабочие веса.",
            )
    elif client.goal == Goal.ENDURANCE:
        add_item(
            items,
            "Цель",
            "low",
            "Добавить контроль выносливости",
            "Для цели по выносливости полезно отслеживать не только вес, но и регулярность кардио.",
            "Раз в неделю фиксируйте длительность кардио и субъективную сложность тренировки.",
        )


def add_nutrition_recommendations(items, client, target, nutrition):
    days = nutrition["days_with_food"]
    averages = nutrition["averages"]
    if days < 3:
        add_item(
            items,
            "Питание",
            "medium",
            "Заполнять дневник питания чаще",
            f"За последние 7 дней заполнено только {days} дней питания.",
            "Вносите хотя бы основные приемы пищи 5-7 дней подряд, чтобы рекомендации стали точнее.",
        )
        return

    if not target:
        add_item(
            items,
            "Питание",
            "medium",
            "Задать норму калорий и БЖУ",
            "У клиента нет активной нормы питания, поэтому система не может оценить отклонения.",
            "Тренеру стоит задать цель по калориям, белкам, жирам и углеводам.",
        )
        return

    if averages["protein"] < target.protein_target * 0.8:
        add_item(
            items,
            "Питание",
            "medium",
            "Поднять белок до нормы",
            f"Средний белок за заполненные дни: {averages['protein']:.0f} г при норме {target.protein_target} г.",
            "Добавьте белковый продукт в завтрак или ужин: творог, рыбу, птицу или бобовые.",
        )

    if client.goal == Goal.WEIGHT_LOSS and averages["calories"] > target.calories_target * 1.1:
        add_item(
            items,
            "Питание",
            "high",
            "Снизить среднее превышение калорий",
            f"Средняя калорийность выше нормы примерно на {averages['calories'] - target.calories_target:.0f} ккал.",
            "Уберите один калорийный перекус или уменьшите порцию углеводов в вечернем приеме пищи.",
        )
    elif client.goal == Goal.MUSCLE_GAIN and averages["calories"] < target.calories_target * 0.9:
        add_item(
            items,
            "Питание",
            "medium",
            "Добавить калории для роста",
            f"Средняя калорийность ниже нормы примерно на {target.calories_target - averages['calories']:.0f} ккал.",
            "Добавьте плотный перекус после тренировки или увеличьте порцию крупы/картофеля.",
        )


def adherence_score(workouts_30, workouts_14, progress, nutrition, membership):
    score = 55
    score += min(workouts_30, 12) * 2
    score += min(workouts_14, 6) * 2
    if progress["weight_delta_total"] is not None:
        score += 8
    if nutrition["days_with_food"] >= 5:
        score += 10
    elif nutrition["days_with_food"] >= 3:
        score += 5
    if not membership:
        score -= 10
    elif membership.status == MembershipStatus.ACTIVE:
        score += 5
        if membership.expires_soon:
            score -= 7
    elif membership.status == MembershipStatus.EXPIRED:
        score -= 15
    else:
        score -= 10
    return max(0, min(100, score))


def recommendation_signals(client, workouts_30, workouts_14, progress, nutrition, membership, plan):
    if not membership or membership.status == MembershipStatus.EXPIRED:
        membership_state = "bad"
    elif membership.status == MembershipStatus.ACTIVE and not membership.expires_soon:
        membership_state = "good"
    else:
        membership_state = "warn"

    return [
        {"label": "Тренировок за 30 дней", "value": workouts_30, "state": "good" if workouts_30 >= 8 else "warn"},
        {"label": "Тренировок за 14 дней", "value": workouts_14, "state": "good" if workouts_14 >= 2 else "bad"},
        {
            "label": "Дней питания за неделю",
            "value": nutrition["days_with_food"],
            "state": "good" if nutrition["days_with_food"] >= 5 else "warn",
        },
        {
            "label": "Изменение веса",
            "value": f"{progress['weight_delta_total']:+.1f} кг" if progress["weight_delta_total"] is not None else "-",
            "state": "good" if progress["weight_delta_total"] is not None else "warn",
        },
        {
            "label": "Выполнение плана",
            "value": f"{plan.completion_percent()}%" if plan else "-",
            "state": "good" if plan and plan.completion_percent() >= 60 else "warn",
        },
        {
            "label": "Абонемент",
            "value": membership.get_status_display() if membership else "-",
            "state": membership_state,
        },
    ]
