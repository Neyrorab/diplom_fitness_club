from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from club.models import (
    ClientProfile,
    CompletedExercise,
    CompletedWorkout,
    Goal,
    Meal,
    MealItem,
    MealType,
    Product,
    ProgressRecord,
)


MEAL_TEMPLATES = {
    MealType.BREAKFAST: (
        (("Овсяные хлопья", 70), ("Молоко 2.5%", 200), ("Банан", 100)),
        (("Творог 5%", 180), ("Черника", 80), ("Мед", 15)),
        (("Яйцо", 120), ("Хлеб цельнозерновой", 60), ("Помидор", 100)),
    ),
    MealType.LUNCH: (
        (("Куриная грудка", 170), ("Рис бурый", 90), ("Огурец", 120), ("Оливковое масло", 10)),
        (("Индейка", 170), ("Гречка", 90), ("Брокколи", 150)),
        (("Тунец", 150), ("Макароны твердых сортов", 90), ("Шпинат", 80)),
    ),
    MealType.DINNER: (
        (("Лосось", 160), ("Киноа", 80), ("Брокколи", 150)),
        (("Говядина постная", 160), ("Картофель", 180), ("Морковь", 120)),
        (("Чечевица", 180), ("Помидор", 120), ("Авокадо", 70)),
    ),
    MealType.SNACK: (
        (("Кефир 1%", 250), ("Яблоко", 140)),
        (("Йогурт натуральный", 200), ("Миндаль", 25)),
        (("Протеин сывороточный", 30), ("Банан", 100)),
    ),
}

MEAL_COMMENTS = {
    MealType.BREAKFAST: "Плановый завтрак перед рабочим днем.",
    MealType.LUNCH: "Основной прием пищи с белком и гарниром.",
    MealType.DINNER: "Легкий ужин после дневной активности.",
    MealType.SNACK: "Перекус для поддержания нормы БЖУ.",
}

MOODS = ("Бодрое", "Спокойное", "Нормальное", "Хорошее", "Умеренная усталость")


class Command(BaseCommand):
    help = "Extend demo training, nutrition and progress data up to the selected date."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            default=None,
            help="Target date in YYYY-MM-DD format. Defaults to the current local date.",
        )

    def handle(self, *args, **options):
        target_date = self._target_date(options["date"])
        product_by_name = {product.name: product for product in Product.objects.filter(is_active=True)}
        missing_products = sorted(
            {
                name
                for template_group in MEAL_TEMPLATES.values()
                for template in template_group
                for name, _weight in template
                if name not in product_by_name
            }
        )
        if missing_products:
            raise CommandError(f"Missing products: {', '.join(missing_products)}")

        clients = list(
            ClientProfile.objects.select_related("user", "trainer")
            .prefetch_related("workout_plans__days__exercises__exercise")
            .order_by("id")
        )
        if not clients:
            raise CommandError("No clients found.")

        totals = {
            "meals": 0,
            "meal_items": 0,
            "progress_records": 0,
            "workouts": 0,
            "completed_exercises": 0,
        }

        with transaction.atomic():
            for client_index, client in enumerate(clients, start=1):
                self._extend_meals(client, client_index, target_date, product_by_name, totals)
                self._extend_progress(client, client_index, target_date, totals)
                self._extend_workouts(client, client_index, target_date, totals)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data extended to {date}: {meals} meals, {meal_items} meal items, "
                "{progress_records} progress records, {workouts} workouts, "
                "{completed_exercises} completed exercises.".format(date=target_date, **totals)
            )
        )

    def _target_date(self, raw_date):
        if not raw_date:
            return timezone.localdate()
        target_date = parse_date(raw_date)
        if target_date is None:
            raise CommandError("Use --date in YYYY-MM-DD format.")
        return target_date

    def _extend_meals(self, client, client_index, target_date, product_by_name, totals):
        last_meal_date = client.meals.order_by("-meal_date").values_list("meal_date", flat=True).first()
        start_date = last_meal_date + timedelta(days=1) if last_meal_date else target_date - timedelta(days=6)
        if start_date > target_date:
            return

        current_date = start_date
        while current_date <= target_date:
            for meal_type in (MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER, MealType.SNACK):
                meal, created = Meal.objects.get_or_create(
                    client=client,
                    meal_date=current_date,
                    meal_type=meal_type,
                    defaults={"comment": MEAL_COMMENTS[meal_type]},
                )
                if created:
                    totals["meals"] += 1
                if meal.items.exists():
                    continue

                templates = MEAL_TEMPLATES[meal_type]
                template = templates[(current_date.toordinal() + client_index) % len(templates)]
                weight_factor = Decimal("1") + Decimal((client_index % 5) - 2) / Decimal("20")
                for product_name, base_weight in template:
                    MealItem.objects.create(
                        meal=meal,
                        product=product_by_name[product_name],
                        weight_grams=self._decimal(base_weight) * weight_factor,
                    )
                    totals["meal_items"] += 1
            current_date += timedelta(days=1)

    def _extend_progress(self, client, client_index, target_date, totals):
        if client.progress_records.filter(record_date=target_date).exists():
            return

        latest = client.progress_records.order_by("-record_date", "-id").first()
        if latest is None:
            base_weight = Decimal("68.0") + Decimal(client_index % 12)
            ProgressRecord.objects.create(
                client=client,
                record_date=target_date,
                weight=base_weight,
                waist=Decimal("78.0") + Decimal(client_index % 8),
                chest=Decimal("92.0") + Decimal(client_index % 9),
                hips=Decimal("94.0") + Decimal(client_index % 7),
                comment="Контрольный замер на текущую дату.",
            )
            totals["progress_records"] += 1
            return

        days_passed = max((target_date - latest.record_date).days, 1)
        weeks = Decimal(days_passed) / Decimal("7")
        weight_delta = self._weekly_weight_delta(client, client_index) * weeks
        size_delta = self._weekly_size_delta(client) * weeks

        ProgressRecord.objects.create(
            client=client,
            record_date=target_date,
            weight=self._positive_decimal(latest.weight + weight_delta),
            waist=self._optional_measure(latest.waist, size_delta),
            chest=self._optional_measure(latest.chest, self._chest_delta(client) * weeks),
            hips=self._optional_measure(latest.hips, size_delta),
            comment="Актуальный контрольный замер после последних тренировок.",
        )
        totals["progress_records"] += 1

    def _extend_workouts(self, client, client_index, target_date, totals):
        plan = client.active_plan()
        if not plan:
            return

        plan_days = list(plan.days.all())
        if not plan_days:
            return

        last_workout_at = client.completed_workouts.order_by("-completed_at").values_list("completed_at", flat=True).first()
        start_date = last_workout_at.date() + timedelta(days=1) if last_workout_at else target_date - timedelta(days=6)
        if start_date > target_date:
            return

        current_date = start_date
        while current_date <= target_date:
            if self._should_add_workout(current_date, target_date, client_index):
                already_exists = client.completed_workouts.filter(completed_at__date=current_date).exists()
                if not already_exists:
                    workout_day = plan_days[(current_date.toordinal() + client_index) % len(plan_days)]
                    completed_at = self._local_datetime(current_date, 17 + client_index % 4, (client_index % 2) * 30)
                    workout = CompletedWorkout.objects.create(
                        client=client,
                        workout_day=workout_day,
                        completed_at=completed_at,
                        mood=MOODS[(current_date.toordinal() + client_index) % len(MOODS)],
                        comment="Тренировка выполнена по плану, нагрузка без резких изменений.",
                    )
                    totals["workouts"] += 1
                    for plan_exercise in workout_day.exercises.all():
                        CompletedExercise.objects.create(
                            completed_workout=workout,
                            workout_exercise=plan_exercise,
                            actual_sets=plan_exercise.sets_count,
                            actual_reps=max(plan_exercise.reps_count - (client_index % 2), 1),
                            actual_weight=self._positive_decimal(
                                plan_exercise.recommended_weight + Decimal(client_index % 3) * Decimal("0.5")
                            ),
                            is_completed=True,
                            comment="Выполнено в рабочем темпе.",
                        )
                        totals["completed_exercises"] += 1
            current_date += timedelta(days=1)

    def _should_add_workout(self, current_date, target_date, client_index):
        if current_date == target_date:
            return client_index == 1 or client_index % 3 == 0
        return (current_date.toordinal() + client_index) % 3 == 0

    def _weekly_weight_delta(self, client, client_index):
        variation = Decimal(client_index % 3) * Decimal("0.05")
        if client.goal == Goal.WEIGHT_LOSS:
            return Decimal("-0.30") - variation
        if client.goal == Goal.MUSCLE_GAIN:
            return Decimal("0.20") + variation
        if client.goal == Goal.ENDURANCE:
            return Decimal("-0.10")
        if client.goal == Goal.RECOVERY:
            return Decimal("0.05")
        return Decimal("0.00")

    def _weekly_size_delta(self, client):
        if client.goal == Goal.WEIGHT_LOSS:
            return Decimal("-0.30")
        if client.goal == Goal.MUSCLE_GAIN:
            return Decimal("0.10")
        if client.goal == Goal.ENDURANCE:
            return Decimal("-0.10")
        return Decimal("0.00")

    def _chest_delta(self, client):
        if client.goal == Goal.MUSCLE_GAIN:
            return Decimal("0.15")
        if client.goal == Goal.WEIGHT_LOSS:
            return Decimal("-0.05")
        return Decimal("0.00")

    def _optional_measure(self, value, delta):
        if value is None:
            return None
        return self._positive_decimal(value + delta)

    def _positive_decimal(self, value):
        return max(self._decimal(value), Decimal("1.0"))

    def _decimal(self, value):
        return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)

    def _local_datetime(self, date_value, hour, minute):
        local_time = datetime.combine(date_value, time(hour=hour, minute=minute))
        return timezone.make_aware(local_time, timezone.get_current_timezone())
