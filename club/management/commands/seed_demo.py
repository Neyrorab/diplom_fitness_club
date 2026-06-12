from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from club.models import (
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
    NutritionTarget,
    Product,
    ProgressRecord,
    Role,
    ScheduleSlot,
    TrainerComment,
    TrainerProfile,
    TrainingAppointment,
    WorkoutDay,
    WorkoutExercise,
    WorkoutPlan,
)
from club.utils import bootstrap_roles


class Command(BaseCommand):
    help = "Создает демонстрационные данные для защиты дипломного проекта."

    @transaction.atomic
    def handle(self, *args, **options):
        bootstrap_roles()
        today = timezone.localdate()

        admin = self.user("admin", "admin12345", Role.ADMIN, "Администратор клуба", "admin@fitness.local", is_staff=True, is_superuser=True)

        trainer_specs = [
            ("trainer1", "trainer12345", "Марина Волкова", "marina@fitness.local", "+7 900 100-10-01", "Силовой тренинг", 8),
            ("trainer2", "trainer12345", "Алексей Орлов", "alexey@fitness.local", "+7 900 100-10-02", "Функциональная подготовка", 6),
        ]
        trainers = []
        for username, password, name, email, phone, specialization, experience in trainer_specs:
            user = self.user(username, password, Role.TRAINER, name, email)
            trainer, _ = TrainerProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": name,
                    "phone": phone,
                    "specialization": specialization,
                    "experience_years": experience,
                    "status": "active",
                },
            )
            trainers.append(trainer)

        self.seed_exercises()
        self.seed_products()
        TrainingAppointment.objects.all().delete()
        ScheduleSlot.objects.all().delete()

        client_specs = [
            ("client1", "client12345", "Ирина Соколова", "+7 900 200-10-01", Goal.WEIGHT_LOSS, trainers[0], 168, "Начальный"),
            ("client2", "client12345", "Дмитрий Павлов", "+7 900 200-10-02", Goal.MUSCLE_GAIN, trainers[0], 182, "Средний"),
            ("client3", "client12345", "Ольга Ким", "+7 900 200-10-03", Goal.MAINTENANCE, trainers[0], 164, "Начальный"),
            ("client4", "client12345", "Никита Федоров", "+7 900 200-10-04", Goal.ENDURANCE, trainers[0], 178, "Средний"),
            ("client5", "client12345", "Анна Морозова", "+7 900 200-10-05", Goal.RECOVERY, trainers[1], 170, "После перерыва"),
            ("client6", "client12345", "Сергей Лебедев", "+7 900 200-10-06", Goal.WEIGHT_LOSS, trainers[1], 176, "Начальный"),
            ("client7", "client12345", "Елена Романова", "+7 900 200-10-07", Goal.MUSCLE_GAIN, trainers[1], 166, "Средний"),
            ("client8", "client12345", "Павел Андреев", "+7 900 200-10-08", Goal.MAINTENANCE, trainers[1], 184, "Продвинутый"),
        ]
        clients = []
        for index, spec in enumerate(client_specs, start=1):
            username, password, name, phone, goal, trainer, height, level = spec
            user = self.user(username, password, Role.CLIENT, name, f"{username}@fitness.local")
            client, _ = ClientProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": name,
                    "phone": phone,
                    "birth_date": today.replace(year=today.year - 25 - index),
                    "height": height,
                    "goal": goal,
                    "training_level": level,
                    "health_limitations": "Без ограничений" if index % 3 else "Беречь колени при прыжковых упражнениях",
                    "trainer": trainer,
                    "status": "active",
                },
            )
            clients.append(client)

        membership_types = ["Безлимит 1 месяц", "12 занятий", "Персональный пакет"]
        for index, client in enumerate(clients):
            end_offset = [30, 5, -2, 18, 7, 45, 3, 21][index]
            Membership.objects.update_or_create(
                client=client,
                type=membership_types[index % len(membership_types)],
                defaults={
                    "start_date": today - timedelta(days=30 - index),
                    "end_date": today + timedelta(days=end_offset),
                    "visits_total": 12 if index % 2 else 30,
                    "visits_left": max(0, 10 - index),
                    "status": "active",
                },
            )

        self.seed_templates(clients[0], trainers[0])
        templates = list(WorkoutPlan.objects.filter(is_template=True).prefetch_related("days__exercises__exercise"))

        for index, client in enumerate(clients):
            template = templates[index % len(templates)]
            plan, _ = WorkoutPlan.objects.update_or_create(
                client=client,
                title=f"{template.title} для {client.full_name.split()[0]}",
                defaults={
                    "trainer": client.trainer,
                    "goal": client.goal,
                    "description": template.description,
                    "start_date": today - timedelta(days=21),
                    "end_date": today + timedelta(days=35),
                    "status": "active",
                    "is_template": False,
                },
            )
            plan.days.all().delete()
            self.copy_template(template, plan)

        products = list(Product.objects.all())
        review_titles = [
            "Комфортный зал и понятный план",
            "Тренеры держат фокус",
            "Удобно следить за прогрессом",
            "Хорошая атмосфера",
        ]
        review_texts = [
            "Нравится, что тренировки идут по плану, а тренер быстро отвечает на вопросы.",
            "После месяца занятий стало проще держать режим и не пропускать тренировки.",
            "Удобно видеть питание, замеры и рекомендации в одном кабинете.",
            "В клубе спокойно, чисто, тренеры внимательные и помогают с техникой.",
        ]
        for index, client in enumerate(clients):
            NutritionTarget.objects.update_or_create(
                client=client,
                start_date=today - timedelta(days=30),
                defaults={
                    "trainer": client.trainer,
                    "calories_target": 1900 + index * 90,
                    "protein_target": 110 + index * 5,
                    "fat_target": 60 + index * 2,
                    "carbs_target": 210 + index * 8,
                    "end_date": None,
                },
            )
            self.seed_progress(client, today, index)
            self.seed_meals(client, today, products, index)
            self.seed_workout_history(client, today, index)
            TrainerComment.objects.update_or_create(
                client=client,
                related_type="profile",
                text="Продолжать текущий режим и обновить замеры в конце недели.",
                defaults={"trainer": client.trainer},
            )
            ClubReview.objects.update_or_create(
                client=client,
                title=review_titles[index % len(review_titles)],
                defaults={
                    "rating": 5 if index % 4 != 2 else 4,
                    "text": review_texts[index % len(review_texts)],
                    "is_published": True,
                },
            )

        self.seed_schedule(trainers, clients, today, admin)

        self.stdout.write(self.style.SUCCESS("Демо-данные созданы. Логины: admin/admin12345, trainer1/trainer12345, client1/client12345."))

    def user(self, username, password, role, full_name, email, is_staff=False, is_superuser=False):
        user, _ = User.objects.update_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": full_name,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
                "is_active": True,
            },
        )
        user.set_password(password)
        user.save()
        user.groups.clear()
        user.groups.add(*[group for group in user.groups.model.objects.filter(name=role)])
        return user

    def seed_exercises(self):
        exercises = [
            ("Приседания со штангой", "Ноги", "Силовое", "Спина нейтральна, колени направлены по линии стоп."),
            ("Жим ногами", "Ноги", "Силовое", "Не отрывать таз от сиденья."),
            ("Выпады с гантелями", "Ноги", "Функциональное", "Шаг средней длины, корпус стабилен."),
            ("Румынская тяга", "Задняя цепь", "Силовое", "Движение от таза, спина ровная."),
            ("Жим лежа", "Грудь", "Силовое", "Лопатки сведены, стопы устойчиво на полу."),
            ("Разводка гантелей", "Грудь", "Изолирующее", "Локти слегка согнуты."),
            ("Тяга верхнего блока", "Спина", "Силовое", "Тянуть локти вниз, не заваливать корпус."),
            ("Горизонтальная тяга", "Спина", "Силовое", "Сводить лопатки в конце движения."),
            ("Подтягивания", "Спина", "Силовое", "Контролировать опускание."),
            ("Жим гантелей сидя", "Плечи", "Силовое", "Не прогибаться в пояснице."),
            ("Махи гантелями в стороны", "Плечи", "Изолирующее", "Поднимать до уровня плеч."),
            ("Сгибание рук со штангой", "Бицепс", "Изолирующее", "Локти фиксированы."),
            ("Французский жим", "Трицепс", "Изолирующее", "Плечи неподвижны."),
            ("Планка", "Кор", "Статическое", "Корпус в прямой линии."),
            ("Скручивания", "Пресс", "Изолирующее", "Без рывков шеей."),
            ("Гиперэкстензия", "Поясница", "Функциональное", "Поднимать корпус до нейтрали."),
            ("Берпи", "Все тело", "Кардио", "Работать в контролируемом темпе."),
            ("Гребной тренажер", "Кардио", "Кардио", "Сначала ноги, затем корпус и руки."),
            ("Велотренажер", "Кардио", "Кардио", "Держать ровный каденс."),
            ("Эллипсоид", "Кардио", "Кардио", "Не переносить весь вес на поручни."),
            ("Становая тяга", "Задняя цепь", "Силовое", "Гриф близко к ногам."),
            ("Тяга гантели в наклоне", "Спина", "Силовое", "Не скручивать корпус."),
            ("Ягодичный мост", "Ягодицы", "Силовое", "Пауза в верхней точке."),
            ("Подъемы на носки", "Икры", "Изолирующее", "Полная амплитуда."),
            ("Отжимания", "Грудь", "Функциональное", "Корпус стабилен."),
            ("Фермерская прогулка", "Кор", "Функциональное", "Плечи опущены, шаг ровный."),
        ]
        for name, muscle_group, exercise_type, description in exercises:
            Exercise.objects.update_or_create(
                name=name,
                defaults={
                    "muscle_group": muscle_group,
                    "exercise_type": exercise_type,
                    "technique_description": description,
                    "is_active": True,
                },
            )

    def seed_products(self):
        products = [
            ("Овсяные хлопья", 366, 11.9, 7.2, 69.3),
            ("Гречка", 343, 13.2, 3.4, 71.5),
            ("Рис бурый", 337, 7.4, 1.8, 72.9),
            ("Куриная грудка", 165, 31.0, 3.6, 0.0),
            ("Индейка", 145, 29.0, 2.5, 0.0),
            ("Лосось", 208, 20.0, 13.0, 0.0),
            ("Тунец", 132, 28.0, 1.0, 0.0),
            ("Яйцо", 157, 12.7, 10.9, 0.7),
            ("Творог 5%", 121, 17.0, 5.0, 1.8),
            ("Йогурт натуральный", 66, 5.0, 3.2, 4.0),
            ("Молоко 2.5%", 52, 2.8, 2.5, 4.7),
            ("Сыр твердый", 356, 24.0, 28.0, 2.0),
            ("Банан", 89, 1.1, 0.3, 22.8),
            ("Яблоко", 47, 0.4, 0.4, 9.8),
            ("Апельсин", 43, 0.9, 0.2, 8.1),
            ("Черника", 57, 0.7, 0.3, 14.5),
            ("Брокколи", 34, 2.8, 0.4, 6.6),
            ("Огурец", 15, 0.7, 0.1, 3.6),
            ("Помидор", 20, 1.1, 0.2, 3.7),
            ("Шпинат", 23, 2.9, 0.4, 3.6),
            ("Картофель", 77, 2.0, 0.4, 16.3),
            ("Авокадо", 160, 2.0, 14.7, 8.5),
            ("Оливковое масло", 884, 0.0, 100.0, 0.0),
            ("Миндаль", 579, 21.2, 49.9, 21.6),
            ("Грецкий орех", 654, 15.2, 65.2, 13.7),
            ("Протеин сывороточный", 390, 78.0, 6.0, 8.0),
            ("Хлеб цельнозерновой", 247, 13.0, 4.2, 41.0),
            ("Макароны твердых сортов", 350, 12.0, 1.5, 72.0),
            ("Фасоль", 123, 7.6, 0.5, 21.5),
            ("Чечевица", 116, 9.0, 0.4, 20.1),
            ("Киноа", 368, 14.1, 6.1, 64.2),
            ("Мед", 304, 0.3, 0.0, 82.4),
            ("Говядина постная", 187, 29.0, 7.0, 0.0),
            ("Кефир 1%", 40, 3.0, 1.0, 4.0),
            ("Морковь", 41, 0.9, 0.2, 9.6),
            ("Творог обезжиренный", 86, 18.0, 0.6, 1.8),
        ]
        for name, calories, protein, fat, carbs in products:
            Product.objects.update_or_create(
                name=name,
                defaults={
                    "calories_per_100g": Decimal(str(calories)),
                    "protein_per_100g": Decimal(str(protein)),
                    "fat_per_100g": Decimal(str(fat)),
                    "carbs_per_100g": Decimal(str(carbs)),
                    "is_active": True,
                },
            )

    def seed_templates(self, client, trainer):
        definitions = [
            (
                "Новичок: 3 тренировки в неделю",
                Goal.MAINTENANCE,
                "Базовая адаптация к силовым тренировкам.",
                [
                    ("День А", [("Приседания со штангой", 3, 10), ("Жим лежа", 3, 10), ("Тяга верхнего блока", 3, 12), ("Планка", 3, 45)]),
                    ("День B", [("Жим ногами", 3, 12), ("Горизонтальная тяга", 3, 12), ("Жим гантелей сидя", 3, 10), ("Скручивания", 3, 15)]),
                    ("День C", [("Румынская тяга", 3, 10), ("Отжимания", 3, 12), ("Махи гантелями в стороны", 3, 15), ("Гиперэкстензия", 3, 12)]),
                ],
            ),
            (
                "Похудение: круговая тренировка",
                Goal.WEIGHT_LOSS,
                "Повышенный расход энергии и развитие общей выносливости.",
                [
                    ("Круг 1", [("Берпи", 4, 12), ("Гребной тренажер", 4, 1), ("Выпады с гантелями", 4, 12), ("Планка", 4, 40)]),
                    ("Круг 2", [("Эллипсоид", 4, 1), ("Отжимания", 4, 12), ("Ягодичный мост", 4, 15), ("Скручивания", 4, 18)]),
                ],
            ),
            (
                "Набор мышечной массы: базовый сплит",
                Goal.MUSCLE_GAIN,
                "Силовой сплит с акцентом на базовые движения.",
                [
                    ("Грудь и трицепс", [("Жим лежа", 4, 8), ("Разводка гантелей", 3, 12), ("Французский жим", 3, 10)]),
                    ("Спина и бицепс", [("Становая тяга", 4, 6), ("Подтягивания", 4, 8), ("Сгибание рук со штангой", 3, 10)]),
                    ("Ноги и плечи", [("Приседания со штангой", 4, 8), ("Жим ногами", 3, 10), ("Жим гантелей сидя", 4, 8)]),
                ],
            ),
        ]
        for title, goal, description, days in definitions:
            plan, _ = WorkoutPlan.objects.update_or_create(
                title=title,
                is_template=True,
                defaults={
                    "client": client,
                    "trainer": trainer,
                    "goal": goal,
                    "description": description,
                    "start_date": timezone.localdate(),
                    "status": "active",
                },
            )
            plan.days.all().delete()
            for day_number, (day_title, exercises) in enumerate(days, start=1):
                day = WorkoutDay.objects.create(workout_plan=plan, title=day_title, day_number=day_number)
                for order, (exercise_name, sets, reps) in enumerate(exercises, start=1):
                    WorkoutExercise.objects.create(
                        workout_day=day,
                        exercise=Exercise.objects.get(name=exercise_name),
                        sets_count=sets,
                        reps_count=reps,
                        recommended_weight=self.default_weight_for_exercise(exercise_name),
                        rest_seconds=60,
                        comment="Подбирать нагрузку по самочувствию.",
                        order_number=order,
                    )

    def copy_template(self, template, plan):
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

    def seed_progress(self, client, today, index):
        start_weight = Decimal(str(82 - index * 1.8))
        for week in range(6):
            ProgressRecord.objects.update_or_create(
                client=client,
                record_date=today - timedelta(days=(5 - week) * 7),
                defaults={
                    "weight": start_weight - Decimal(str(week * 0.35)) + Decimal(str(index * 0.05)),
                    "waist": Decimal("84") - Decimal(str(week * 0.4)) + Decimal(str(index)),
                    "chest": Decimal("96") + Decimal(str(index * 0.7)),
                    "hips": Decimal("100") - Decimal(str(week * 0.2)) + Decimal(str(index * 0.5)),
                    "comment": "Плановая запись",
                },
            )

    def seed_meals(self, client, today, products, index):
        product_by_name = {product.name: product for product in products}
        if index == 0:
            meal_plan = [
                (
                    MealType.BREAKFAST,
                    [("Овсяные хлопья", "55"), ("Творог 5%", "150"), ("Банан", "90")],
                    "Стабильный завтрак для контроля аппетита",
                ),
                (
                    MealType.LUNCH,
                    [("Куриная грудка", "160"), ("Рис бурый", "70"), ("Брокколи", "180"), ("Оливковое масло", "8")],
                    "Белок и сложные углеводы после дневной активности",
                ),
                (
                    MealType.DINNER,
                    [("Индейка", "140"), ("Картофель", "180"), ("Огурец", "120"), ("Помидор", "120")],
                    "Легкий ужин без сильного дефицита",
                ),
                (
                    MealType.SNACK,
                    [("Кефир 1%", "250"), ("Миндаль", "15"), ("Протеин сывороточный", "25")],
                    "Перекус для добора белка",
                ),
            ]
            day_count = 14
        else:
            selected = products[index : index + 8] or products[:8]
            meal_plan = [
                (meal_type, [(product.name, "150")], "Демо-прием пищи")
                for meal_type, product in zip([MealType.BREAKFAST, MealType.LUNCH, MealType.DINNER], selected[:3])
            ]
            day_count = 3

        for day_offset in range(day_count):
            date = today - timedelta(days=day_offset)
            for meal_type, items, comment in meal_plan:
                meal, _ = Meal.objects.get_or_create(client=client, meal_date=date, meal_type=meal_type)
                meal.comment = comment
                meal.save(update_fields=["comment"])
                if not meal.items.exists():
                    for product_name, weight in items:
                        product = product_by_name.get(product_name)
                        if product:
                            MealItem.objects.create(meal=meal, product=product, weight_grams=Decimal(weight))

    def seed_workout_history(self, client, today, index):
        plan = client.workout_plans.filter(is_template=False).prefetch_related("days__exercises").first()
        if not plan:
            return
        days = list(plan.days.all())
        if not days:
            return
        offsets = [2, 6, 11] if index not in (2, 6) else [18, 24]
        for offset in offsets:
            day = days[offset % len(days)]
            workout, created = CompletedWorkout.objects.get_or_create(
                client=client,
                workout_day=day,
                completed_at=timezone.make_aware(timezone.datetime.combine(today - timedelta(days=offset), timezone.datetime.min.time())),
                defaults={"mood": "Хорошо", "comment": "Демо-выполнение"},
            )
            if created:
                for item in day.exercises.all():
                    CompletedExercise.objects.create(
                        completed_workout=workout,
                        workout_exercise=item,
                        actual_sets=item.sets_count,
                        actual_reps=item.reps_count,
                        actual_weight=self.demo_actual_weight(item, index),
                        is_completed=True,
                    )

    def default_weight_for_exercise(self, exercise_name):
        weights = {
            "Приседания со штангой": "35",
            "Жим лежа": "32.5",
            "Тяга верхнего блока": "35",
            "Жим ногами": "80",
            "Горизонтальная тяга": "32.5",
            "Жим гантелей сидя": "16",
            "Румынская тяга": "37.5",
            "Махи гантелями в стороны": "6",
            "Выпады с гантелями": "14",
            "Ягодичный мост": "35",
            "Становая тяга": "55",
            "Сгибание рук со штангой": "18",
            "Разводка гантелей": "10",
            "Французский жим": "16",
        }
        return Decimal(weights.get(exercise_name, "0"))

    def demo_actual_weight(self, plan_exercise, client_index):
        base = plan_exercise.recommended_weight or self.default_weight_for_exercise(plan_exercise.exercise.name)
        if base <= 0:
            return Decimal("0")
        return base + Decimal(str((client_index % 4) * 1.25))

    def seed_schedule(self, trainers, clients, today, admin):
        slot_specs = [
            (1, time(10, 0), time(11, 0)),
            (1, time(12, 0), time(13, 0)),
            (2, time(18, 0), time(19, 0)),
            (3, time(9, 30), time(10, 30)),
            (4, time(17, 0), time(18, 0)),
            (6, time(11, 0), time(12, 0)),
            (8, time(19, 0), time(20, 0)),
        ]
        slots_by_trainer = {trainer.id: [] for trainer in trainers}
        for trainer in trainers:
            for day_offset, start_time, end_time in slot_specs:
                start_at = timezone.make_aware(timezone.datetime.combine(today + timedelta(days=day_offset), start_time))
                end_at = timezone.make_aware(timezone.datetime.combine(today + timedelta(days=day_offset), end_time))
                slot = ScheduleSlot.objects.create(
                    trainer=trainer,
                    start_at=start_at,
                    end_at=end_at,
                    note="Персональная тренировка",
                    created_by=admin,
                )
                slots_by_trainer[trainer.id].append(slot)

        for client in clients[:4]:
            slot = slots_by_trainer[client.trainer_id].pop(0)
            TrainingAppointment.objects.create(slot=slot, client=client)
