from datetime import time, timedelta
from decimal import Decimal
from io import StringIO
import json
import urllib.error
import urllib.request
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .ai_jobs import (
    mark_stale_analysis_failed,
    run_client_analysis,
    run_management_analysis,
    run_trainer_analysis,
    run_weight_forecast_analysis,
)
from .ai_management import (
    ManagementAIError,
    get_gptunnel_recommendations,
    get_openrouter_weight_forecast,
    get_zai_recommendations,
    parse_ai_recommendation,
    parse_client_ai_recommendation,
    parse_trainer_ai_recommendation,
    parse_weight_forecast,
    send_chat_request,
)
from .client_assistant import build_client_ai_payload
from .forms import MembershipForm, ProductForm, ProgressRecordForm, WorkoutPlanForm
from .models import (
    ClientAIAnalysis,
    ClubReview,
    ClientProfile,
    Exercise,
    Meal,
    MealItem,
    MealType,
    Membership,
    MembershipStatus,
    ManagementAIAnalysis,
    AppointmentStatus,
    Product,
    ProgressRecord,
    Role,
    ScheduleSlot,
    TrainerAIAnalysis,
    TrainerProfile,
    TrainingAppointment,
    WeightForecastAnalysis,
    CompletedExercise,
    CompletedWorkout,
    WorkoutDay,
    WorkoutExercise,
    WorkoutPlan,
)
from .recommendations import build_client_recommendations
from .trainer_assistant import build_trainer_ai_payload
from .templatetags.club_extras import exercise_type_class, product_macro_class
from .utils import bootstrap_roles
from .weight_forecast import build_weight_forecast_payload, weight_forecast_readiness


class AccessAndCalculationTests(TestCase):
    def setUp(self):
        bootstrap_roles()
        self.admin_user = self.create_user("admin", "admin", Role.ADMIN)
        self.trainer_user = self.create_user("trainer", "trainer", Role.TRAINER)
        self.other_trainer_user = self.create_user("trainer2", "trainer", Role.TRAINER)
        self.client_user = self.create_user("client", "client", Role.CLIENT)
        self.other_client_user = self.create_user("client2", "client", Role.CLIENT)

        self.trainer = TrainerProfile.objects.create(
            user=self.trainer_user,
            full_name="Тренер Первый",
            phone="+1",
            specialization="Силовой тренинг",
        )
        self.other_trainer = TrainerProfile.objects.create(
            user=self.other_trainer_user,
            full_name="Тренер Второй",
            phone="+2",
            specialization="Кардио",
        )
        self.client_profile = ClientProfile.objects.create(
            user=self.client_user,
            full_name="Клиент Один",
            phone="+3",
            goal="weight_loss",
            trainer=self.trainer,
        )
        self.other_client_profile = ClientProfile.objects.create(
            user=self.other_client_user,
            full_name="Клиент Два",
            phone="+4",
            goal="maintenance",
            trainer=self.other_trainer,
        )

    def create_user(self, username, password, role):
        user = User.objects.create_user(username=username, password=password)
        user.groups.add(Group.objects.get(name=role))
        return user

    def test_client_cannot_open_another_client_card(self):
        self.client.login(username="client", password="client")
        response = self.client.get(reverse("client_detail", args=[self.other_client_profile.id]))
        self.assertEqual(response.status_code, 403)

    def test_trainer_cannot_open_unassigned_client_card(self):
        self.client.login(username="trainer", password="trainer")
        response = self.client.get(reverse("client_detail", args=[self.other_client_profile.id]))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_filter_clients_by_low_activity(self):
        self.client.login(username="admin", password="admin")
        response = self.client.get(
            reverse("clients_list"),
            {"q": "", "status": "", "trainer": self.trainer.id, "activity": "low"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.client_profile.full_name)

    def test_admin_dashboards_are_admin_only(self):
        self.client.login(username="admin", password="admin")
        response = self.client.get(reverse("admin_dashboards"))
        self.assertEqual(response.status_code, 200)

        self.client.logout()
        self.client.login(username="client", password="client")
        response = self.client.get(reverse("admin_dashboards"))
        self.assertEqual(response.status_code, 403)

    @override_settings(AI_PROVIDER="gptunnel", GPTUNNEL_API_MODEL="gpt-4o-mini", GPTUNNEL_BALANCED_MODEL="gpt-4o-mini")
    def test_admin_dashboard_shows_gptunnel_provider(self):
        self.client.login(username="admin", password="admin")
        response = self.client.get(reverse("admin_dashboards"))

        self.assertContains(response, "GPTunnel")
        self.assertContains(response, "gpt-4o-mini")

    def test_ai_model_picker_renders_for_all_roles(self):
        self.client.login(username="admin", password="admin")
        response = self.client.get(reverse("admin_dashboards"))
        self.assertContains(response, "Модель")
        self.assertContains(response, "Быстрая")
        self.assertContains(response, "Оптимальная")
        self.assertContains(response, "Умная")

        self.client.logout()
        self.client.login(username="trainer", password="trainer")
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Модель")
        self.assertContains(response, "Быстрая")
        self.assertContains(response, "Оптимальная")
        self.assertContains(response, "Умная")

        self.client.logout()
        self.client.login(username="client", password="client")
        response = self.client.get(reverse("recommendations"))
        self.assertContains(response, "Модель")
        self.assertContains(response, "Быстрая")
        self.assertContains(response, "Оптимальная")
        self.assertContains(response, "Умная")

    def test_client_dashboard_renders_training_charts(self):
        today = timezone.localdate()
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="Силовой план",
            goal=self.client_profile.goal,
            start_date=today - timedelta(days=10),
        )
        completed_day = WorkoutDay.objects.create(workout_plan=plan, title="День силы", day_number=1)
        WorkoutDay.objects.create(workout_plan=plan, title="День техники", day_number=2)
        exercise = Exercise.objects.create(name="Жим", muscle_group="Грудь", exercise_type="Силовое")
        plan_exercise = WorkoutExercise.objects.create(
            workout_day=completed_day,
            exercise=exercise,
            sets_count=3,
            reps_count=10,
            recommended_weight=Decimal("40.0"),
        )
        workout = CompletedWorkout.objects.create(
            client=self.client_profile,
            workout_day=completed_day,
            completed_at=timezone.now(),
            mood="Хорошо",
        )
        CompletedExercise.objects.create(
            completed_workout=workout,
            workout_exercise=plan_exercise,
            actual_sets=3,
            actual_reps=10,
            actual_weight=Decimal("40.0"),
            is_completed=True,
        )

        self.client.login(username="client", password="client")
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Регулярность тренировок")
        self.assertContains(response, "Календарь активности")
        self.assertContains(response, "Выполнение плана")
        self.assertContains(response, "Объем нагрузки")
        self.assertContains(response, '"workoutRegularity"')
        self.assertContains(response, '"activityCalendar"')
        self.assertContains(response, '"planCompletion"')
        self.assertContains(response, '"trainingLoad"')
        self.assertContains(response, "50%")
        self.assertContains(response, "1200.0")

    def test_client_chart_period_filters_large_history(self):
        today = timezone.localdate()
        for offset in range(220):
            ProgressRecord.objects.create(
                client=self.client_profile,
                record_date=today - timedelta(days=offset),
                weight=Decimal("82.0") - Decimal(offset) / Decimal("20"),
                waist=Decimal("84.0"),
                chest=Decimal("96.0"),
                hips=Decimal("100.0"),
            )
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="Длинная история",
            goal=self.client_profile.goal,
            start_date=today - timedelta(days=140),
        )
        day = WorkoutDay.objects.create(workout_plan=plan, title="День", day_number=1)
        exercise = Exercise.objects.create(name="Тяга", muscle_group="Спина", exercise_type="Силовое")
        plan_exercise = WorkoutExercise.objects.create(
            workout_day=day,
            exercise=exercise,
            sets_count=3,
            reps_count=10,
            recommended_weight=Decimal("30.0"),
        )
        for offset in range(120):
            workout = CompletedWorkout.objects.create(
                client=self.client_profile,
                workout_day=day,
                completed_at=timezone.make_aware(
                    timezone.datetime.combine(today - timedelta(days=offset), time(hour=12))
                ),
            )
            CompletedExercise.objects.create(
                completed_workout=workout,
                workout_exercise=plan_exercise,
                actual_sets=3,
                actual_reps=10,
                actual_weight=Decimal("30.0"),
                is_completed=True,
            )

        self.client.login(username="client", password="client")
        response = self.client.get(reverse("dashboard"), {"chart_period": "30"})
        charts = json.loads(response.context["client_dashboard_charts"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "30 дней")
        self.assertEqual(len(charts["clientWeight"]["labels"]), 30)
        self.assertEqual(len(charts["trainingLoad"]["labels"]), 30)
        self.assertEqual(len(charts["workoutRegularity"]["labels"]), 5)
        self.assertEqual(len(charts["activityCalendar"]["days"]), 30)

        response = self.client.get(reverse("dashboard"), {"chart_period": "7"})
        charts = json.loads(response.context["client_dashboard_charts"])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="7" selected')
        self.assertEqual(len(charts["clientWeight"]["labels"]), 7)
        self.assertEqual(len(charts["trainingLoad"]["labels"]), 7)
        self.assertEqual(len(charts["workoutRegularity"]["labels"]), 1)
        self.assertEqual(len(charts["activityCalendar"]["days"]), 7)
        self.assertEqual(charts["clientWeight"]["labels"][0], (today - timedelta(days=6)).strftime("%d.%m"))
        self.assertEqual(charts["clientWeight"]["labels"][-1], today.strftime("%d.%m"))
        self.assertEqual(charts["activityCalendar"]["days"][0]["label"], (today - timedelta(days=6)).strftime("%d.%m"))
        self.assertEqual(charts["activityCalendar"]["days"][-1]["label"], today.strftime("%d.%m"))
        self.assertEqual(charts["workoutRegularity"]["labels"][0], f"{today - timedelta(days=6):%d.%m}-{today:%d.%m}")

        for period, days in {"90": 90, "180": 180, "365": 365}.items():
            response = self.client.get(reverse("dashboard"), {"chart_period": period})
            charts = json.loads(response.context["client_dashboard_charts"])
            self.assertEqual(len(charts["clientWeight"]["labels"]), days)
            self.assertEqual(len(charts["trainingLoad"]["labels"]), days)
            self.assertEqual(len(charts["activityCalendar"]["days"]), days)
            self.assertEqual(len(charts["workoutRegularity"]["labels"]), (days + 6) // 7)

        self.client.logout()
        self.client.login(username="client2", password="client")
        response = self.client.get(reverse("dashboard"), {"chart_period": "7"})
        charts = json.loads(response.context["client_dashboard_charts"])
        self.assertEqual(len(charts["clientWeight"]["labels"]), 7)
        self.assertTrue(all(value is None for value in charts["clientWeight"]["datasets"][0]["values"]))
        self.assertTrue(all(value is None for value in charts["trainingLoad"]["datasets"][0]["values"]))
        self.assertFalse(charts["activityCalendar"]["hasData"])

        self.client.logout()
        self.client.login(username="client", password="client")
        response = self.client.get(reverse("progress"), {"chart_period": "30"})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.context["records"]), 30)
        self.assertLessEqual(len(response.context["workout_load_rows"]), 8)
        self.assertTrue(
            all(
                row["workout"].completed_at.date() >= today - timedelta(days=29)
                for row in response.context["workout_load_rows"]
            )
        )
        self.assertContains(response, "Период графиков")

        response = self.client.get(reverse("client_detail", args=[self.client_profile.id]), {"chart_period": "7", "tab": "progress"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["progress_table_records"]), 7)
        self.assertEqual(len(json.loads(response.context["progress"]["labels"])), 7)
        self.assertContains(response, "waist:")
        self.assertContains(response, "chest:")
        self.assertContains(response, "hips:")

    def future_slot(self, trainer=None, client=None, days=2, start=time(10, 0)):
        trainer = trainer or self.trainer
        start_at = timezone.make_aware(timezone.datetime.combine(timezone.localdate() + timedelta(days=days), start))
        end_at = start_at + timedelta(hours=1)
        return ScheduleSlot.objects.create(trainer=trainer, start_at=start_at, end_at=end_at, created_by=self.trainer_user)

    def active_membership(self, client=None, visits_total=5, visits_left=5):
        client = client or self.client_profile
        today = timezone.localdate()
        return Membership.objects.create(
            client=client,
            type="Test membership",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=30),
            visits_total=visits_total,
            visits_left=visits_left,
            status=MembershipStatus.ACTIVE,
        )

    def workout_day_with_exercise(self, client=None, trainer=None, is_template=False, status="active"):
        client = client or self.client_profile
        trainer = trainer or client.trainer
        exercise = Exercise.objects.create(name=f"Exercise {Exercise.objects.count()}", muscle_group="Core", exercise_type="Strength")
        plan = WorkoutPlan.objects.create(
            client=client,
            trainer=trainer,
            title=f"Plan {WorkoutPlan.objects.count()}",
            goal=client.goal,
            start_date=timezone.localdate(),
            status=status,
            is_template=is_template,
        )
        day = WorkoutDay.objects.create(workout_plan=plan, title="Day 1", day_number=1)
        workout_exercise = WorkoutExercise.objects.create(workout_day=day, exercise=exercise)
        return plan, day, workout_exercise

    def started_appointment(self, client=None, trainer=None, minutes_ago=30):
        client = client or self.client_profile
        trainer = trainer or client.trainer
        slot = self.future_slot(trainer=trainer)
        appointment = TrainingAppointment.objects.create(slot=slot, client=client)
        start_at = timezone.now() - timedelta(minutes=minutes_ago)
        ScheduleSlot.objects.filter(pk=slot.pk).update(start_at=start_at, end_at=start_at + timedelta(hours=1))
        appointment.refresh_from_db()
        return appointment

    def test_trainer_can_create_schedule_slot(self):
        self.client.login(username="trainer", password="trainer")
        date = timezone.localdate() + timedelta(days=3)

        response = self.client.post(
            reverse("schedule"),
            {
                "date": date.isoformat(),
                "start_time": "11:00",
                "end_time": "12:00",
                "repeat_weeks": 1,
                "note": "Персональная тренировка",
            },
        )

        self.assertRedirects(response, reverse("schedule"))
        self.assertEqual(ScheduleSlot.objects.filter(trainer=self.trainer).count(), 2)

    def test_schedule_page_renders_for_trainer_and_client(self):
        self.future_slot()

        self.client.login(username="trainer", password="trainer")
        response = self.client.get(reverse("schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "График занятий тренера")
        self.assertContains(response, "Недельный календарь")

        self.client.logout()
        self.client.login(username="client", password="client")
        response = self.client.get(reverse("schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Запись на тренировку")
        self.assertContains(response, "Доступные окна")
        self.assertIn("schedule_weeks", response.context)

    def test_exercise_group_filter_shows_unique_groups(self):
        Exercise.objects.create(name="Back row", muscle_group="Back", exercise_type="Силовое")
        Exercise.objects.create(name="Back pull", muscle_group="Back", exercise_type="Силовое")
        Exercise.objects.create(name="Leg press", muscle_group="Legs", exercise_type="Силовое")
        self.client.login(username="trainer", password="trainer")

        response = self.client.get(reverse("exercises_list"))

        self.assertEqual(list(response.context["groups"]), ["Back", "Legs"])

        response = self.client.get(reverse("exercises_list"), {"group": "Back"})
        self.assertEqual(response.context["selected_group"], "Back")
        self.assertEqual(response.context["exercises"].count(), 2)
        self.assertEqual(set(response.context["exercises"].values_list("muscle_group", flat=True)), {"Back"})

    def test_client_can_book_free_slot_with_assigned_trainer(self):
        slot = self.future_slot()
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("schedule_slot_book", args=[slot.id]))

        self.assertRedirects(response, reverse("schedule"))
        appointment = TrainingAppointment.objects.get(slot=slot, client=self.client_profile)
        self.assertEqual(appointment.status, AppointmentStatus.BOOKED)

    def test_client_cannot_book_other_trainer_slot(self):
        slot = self.future_slot(trainer=self.other_trainer)
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("schedule_slot_book", args=[slot.id]))

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TrainingAppointment.objects.exists())

    def test_client_can_cancel_appointment_more_than_24_hours_before_start(self):
        slot = self.future_slot(days=3)
        appointment = TrainingAppointment.objects.create(slot=slot, client=self.client_profile)
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("schedule_appointment_cancel", args=[appointment.id]), {"reason": "Командировка"})

        self.assertRedirects(response, reverse("schedule"))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.CANCELLED)
        self.assertIsNone(slot.booked_appointment)

    def test_client_cannot_cancel_appointment_less_than_24_hours_before_start(self):
        start_at = timezone.now() + timedelta(hours=12)
        slot = ScheduleSlot.objects.create(trainer=self.trainer, start_at=start_at, end_at=start_at + timedelta(hours=1))
        appointment = TrainingAppointment.objects.create(slot=slot, client=self.client_profile)
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("schedule_appointment_cancel", args=[appointment.id]), {"reason": "Поздно"})

        self.assertRedirects(response, reverse("schedule"))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.BOOKED)

    def test_trainer_cannot_remove_booked_slot(self):
        slot = self.future_slot()
        TrainingAppointment.objects.create(slot=slot, client=self.client_profile)
        self.client.login(username="trainer", password="trainer")

        response = self.client.post(reverse("schedule_slot_deactivate", args=[slot.id]))

        self.assertRedirects(response, reverse("schedule"))
        slot.refresh_from_db()
        self.assertTrue(slot.is_active)

    def test_future_membership_is_planned_and_not_current(self):
        today = timezone.localdate()
        future = Membership.objects.create(
            client=self.client_profile,
            type="Future",
            start_date=today + timedelta(days=10),
            end_date=today + timedelta(days=40),
            visits_total=10,
            visits_left=10,
            status=MembershipStatus.ACTIVE,
        )

        self.assertEqual(future.status, MembershipStatus.PLANNED)
        self.assertIsNone(self.client_profile.current_membership())

        active = self.active_membership(visits_total=8, visits_left=8)
        self.assertEqual(self.client_profile.current_membership(), active)

    def test_complete_workout_consumes_visit_and_marks_appointment_completed(self):
        membership = self.active_membership(visits_total=2, visits_left=2)
        plan, day, workout_exercise = self.workout_day_with_exercise()
        appointment = self.started_appointment()
        self.client.login(username="client", password="client")

        response = self.client.post(
            reverse("complete_workout", args=[day.id]),
            {
                f"exercise_{workout_exercise.id}_done": "on",
                f"exercise_{workout_exercise.id}_sets": "3",
                f"exercise_{workout_exercise.id}_reps": "12",
                f"exercise_{workout_exercise.id}_weight": "10",
            },
        )

        self.assertRedirects(response, f"{reverse('client_detail', args=[self.client_profile.pk])}?tab=workouts")
        membership.refresh_from_db()
        appointment.refresh_from_db()
        self.assertEqual(membership.visits_left, 1)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)
        self.assertEqual(appointment.status, AppointmentStatus.COMPLETED)
        self.assertEqual(CompletedWorkout.objects.get(client=self.client_profile, workout_day=day).appointment, appointment)

    def test_trainer_can_complete_started_appointment(self):
        membership = self.active_membership(visits_total=2, visits_left=2)
        plan, day, workout_exercise = self.workout_day_with_exercise()
        appointment = self.started_appointment()
        self.client.login(username="trainer", password="trainer")

        response = self.client.post(reverse("schedule_appointment_complete", args=[appointment.id]))

        self.assertRedirects(response, reverse("schedule"))
        appointment.refresh_from_db()
        membership.refresh_from_db()
        workout = CompletedWorkout.objects.get(client=self.client_profile, workout_day=day)
        self.assertEqual(appointment.status, AppointmentStatus.COMPLETED)
        self.assertEqual(workout.appointment, appointment)
        self.assertEqual(workout.exercises.filter(is_completed=True).count(), 1)
        self.assertEqual(membership.visits_left, 1)

    def test_client_can_delete_meal_item_and_progress_record(self):
        product = Product.objects.create(
            name="Йогурт",
            calories_per_100g=Decimal("80"),
            protein_per_100g=Decimal("5"),
            fat_per_100g=Decimal("3"),
            carbs_per_100g=Decimal("8"),
        )
        selected_date = timezone.localdate()
        meal = Meal.objects.create(client=self.client_profile, meal_date=selected_date, meal_type=MealType.BREAKFAST)
        item = MealItem.objects.create(meal=meal, product=product, weight_grams=Decimal("150"))
        record = ProgressRecord.objects.create(
            client=self.client_profile,
            record_date=selected_date,
            weight=Decimal("80"),
            waist=Decimal("82"),
            chest=Decimal("96"),
            hips=Decimal("100"),
        )
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("meal_item_delete", args=[item.id]))

        self.assertRedirects(response, f"{reverse('nutrition_client', args=[self.client_profile.pk])}?date={selected_date}")
        self.assertFalse(MealItem.objects.filter(pk=item.pk).exists())
        self.assertFalse(Meal.objects.filter(pk=meal.pk).exists())

        response = self.client.post(reverse("progress_record_delete", args=[record.id]))

        self.assertRedirects(response, reverse("progress_client", args=[self.client_profile.pk]))
        self.assertFalse(ProgressRecord.objects.filter(pk=record.pk).exists())

    def test_delete_completed_workout_restores_visit_and_reopens_appointment(self):
        membership = self.active_membership(visits_total=2, visits_left=1)
        plan, day, _ = self.workout_day_with_exercise()
        appointment = self.started_appointment()
        workout = CompletedWorkout.objects.create(
            client=self.client_profile,
            workout_day=day,
            appointment=appointment,
            completed_at=timezone.now(),
        )
        TrainingAppointment.objects.filter(pk=appointment.pk).update(status=AppointmentStatus.COMPLETED)
        self.client.login(username="client", password="client")

        response = self.client.post(reverse("completed_workout_delete", args=[workout.id]))

        self.assertRedirects(response, f"{reverse('progress_client', args=[self.client_profile.pk])}#training-load")
        membership.refresh_from_db()
        appointment.refresh_from_db()
        self.assertFalse(CompletedWorkout.objects.filter(pk=workout.pk).exists())
        self.assertEqual(membership.visits_left, 2)
        self.assertEqual(appointment.status, AppointmentStatus.BOOKED)

    def test_client_cannot_complete_workout_without_active_membership(self):
        _, day, workout_exercise = self.workout_day_with_exercise()
        self.client.login(username="client", password="client")

        response = self.client.post(
            reverse("complete_workout", args=[day.id]),
            {
                f"exercise_{workout_exercise.id}_done": "on",
                f"exercise_{workout_exercise.id}_sets": "3",
                f"exercise_{workout_exercise.id}_reps": "12",
                f"exercise_{workout_exercise.id}_weight": "10",
            },
        )

        self.assertRedirects(response, reverse("workout_plan_detail", args=[day.workout_plan_id]))
        self.assertFalse(CompletedWorkout.objects.filter(client=self.client_profile, workout_day=day).exists())

    def test_template_plan_is_hidden_and_cannot_be_completed(self):
        plan, day, _ = self.workout_day_with_exercise(is_template=True)
        self.active_membership()
        self.client.login(username="client", password="client")

        response = self.client.get(reverse("client_detail", args=[self.client_profile.id]))
        self.assertNotContains(response, plan.title)

        response = self.client.get(reverse("complete_workout", args=[day.id]))
        self.assertEqual(response.status_code, 403)

    def test_business_forms_reject_invalid_values(self):
        today = timezone.localdate()

        self.assertFalse(
            MembershipForm(
                {
                    "type": "Invalid",
                    "start_date": today.isoformat(),
                    "end_date": (today + timedelta(days=30)).isoformat(),
                    "visits_total": "5",
                    "visits_left": "10",
                    "status": MembershipStatus.ACTIVE,
                }
            ).is_valid()
        )
        self.assertFalse(
            WorkoutPlanForm(
                {
                    "title": "Invalid plan",
                    "goal": self.client_profile.goal,
                    "description": "",
                    "start_date": today.isoformat(),
                    "end_date": (today - timedelta(days=1)).isoformat(),
                    "status": "active",
                    "template": "",
                }
            ).is_valid()
        )
        self.assertFalse(
            ProductForm(
                {
                    "name": "Invalid product",
                    "calories_per_100g": "-1",
                    "protein_per_100g": "1",
                    "fat_per_100g": "1",
                    "carbs_per_100g": "1",
                    "is_active": "on",
                }
            ).is_valid()
        )
        self.assertFalse(
            ProgressRecordForm(
                {
                    "record_date": today.isoformat(),
                    "weight": "-70",
                    "waist": "80",
                    "chest": "90",
                    "hips": "100",
                }
            ).is_valid()
        )

    def test_seed_ai_retention_crisis_scenario_shapes_dashboard_metrics(self):
        output = StringIO()

        call_command("seed_ai_scenario", scenario="retention_crisis", stdout=output)
        self.client.login(username="admin", password="admin12345")
        response = self.client.get(reverse("admin_dashboards"))
        business_kpis = response.context["business_kpis"]
        metrics = response.context["metrics"]

        self.assertIn("AI scenario seeded", output.getvalue())
        self.assertEqual(ClientProfile.objects.filter(user__username__startswith="client").count(), 8)
        self.assertEqual(metrics["active_period_clients"], 4)
        self.assertEqual(business_kpis["workouts_30"], 4)
        self.assertEqual(business_kpis["risk_count"], 7)
        self.assertEqual(business_kpis["expiring_count"], 3)

    def test_seed_ai_healthy_growth_has_full_weekly_activity(self):
        call_command("seed_ai_scenario", scenario="healthy_growth", stdout=StringIO())
        self.client.login(username="admin", password="admin12345")
        response = self.client.get(reverse("admin_dashboards"))
        activity = response.context["management_ai_payload"]["weekly_activity"]
        business_kpis = response.context["business_kpis"]

        self.assertEqual(business_kpis["risk_count"], 0)
        self.assertEqual(business_kpis["expiring_count"], 0)
        self.assertEqual(business_kpis["workouts_30"], 72)
        self.assertEqual([week["workouts"] for week in activity], [16] * 8)
        self.assertEqual([week["active_clients"] for week in activity], [8] * 8)

    def test_weekly_activity_ends_on_today(self):
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="Неделя",
            goal=self.client_profile.goal,
            start_date=timezone.localdate(),
            status="active",
        )
        workout_day = WorkoutDay.objects.create(workout_plan=plan, title="День", day_number=1)
        CompletedWorkout.objects.create(
            client=self.client_profile,
            workout_day=workout_day,
            completed_at=timezone.now() - timedelta(days=1),
        )
        other_plan = WorkoutPlan.objects.create(
            client=self.other_client_profile,
            trainer=self.other_trainer,
            title="РќРµРґРµР»СЏ 2",
            goal=self.other_client_profile.goal,
            start_date=timezone.localdate(),
            status="active",
        )
        other_workout_day = WorkoutDay.objects.create(workout_plan=other_plan, title="Р”РµРЅСЊ", day_number=1)
        CompletedWorkout.objects.create(
            client=self.other_client_profile,
            workout_day=other_workout_day,
            completed_at=timezone.now() + timedelta(days=1),
        )

        self.client.login(username="admin", password="admin")
        response = self.client.get(reverse("admin_dashboards"))
        activity = response.context["management_ai_payload"]["weekly_activity"]

        self.assertEqual(activity[-1]["workouts"], 1)
        self.assertEqual(activity[-1]["active_clients"], 1)

    @override_settings(AI_PROVIDER="openrouter", OPENROUTER_API_KEY="")
    def test_admin_ai_analysis_requires_api_key(self):
        analysis = ManagementAIAnalysis.objects.create(
            requested_by=self.admin_user,
            provider="OpenRouter",
            model="openai/gpt-4.1-mini",
            payload={},
        )

        run_management_analysis(analysis.pk)
        analysis.refresh_from_db()

        self.assertEqual(analysis.status, ManagementAIAnalysis.Status.FAILED)
        self.assertIn("OPENROUTER_API_KEY", analysis.error)

    @override_settings(AI_PROVIDER="gptunnel", GPTUNNEL_API_KEY="")
    def test_admin_gptunnel_analysis_requires_api_key(self):
        analysis = ManagementAIAnalysis.objects.create(
            requested_by=self.admin_user,
            provider="GPTunnel",
            model="gpt-4o-mini",
            payload={},
        )

        run_management_analysis(analysis.pk)
        analysis.refresh_from_db()

        self.assertEqual(analysis.status, ManagementAIAnalysis.Status.FAILED)
        self.assertIn("GPTUNNEL_API_KEY", analysis.error)

    @override_settings(GPTUNNEL_API_KEY="test-key", GPTUNNEL_API_MODEL="gpt-4o-mini", GPTUNNEL_FALLBACK_MODELS="gpt-4o")
    def test_gptunnel_tries_fallback_model(self):
        response = {
            "model": "gpt-4o",
            "choices": [{"message": {"content": '{"summary": "Резервная модель ответила."}'}}],
        }
        with patch("club.ai_management.send_chat_request", side_effect=[ManagementAIError("Сбой модели"), response]) as mocked_send:
            result = get_gptunnel_recommendations({})

        self.assertEqual(result["model"], "gpt-4o")
        self.assertEqual(result["summary"], "Резервная модель ответила.")
        self.assertEqual(mocked_send.call_count, 2)

    @override_settings(ZAI_API_KEY="test-key", ZAI_ALLOWED_MODELS={"glm-5.1"})
    def test_zai_paid_model_is_allowed(self):
        response = {
            "model": "glm-5.1",
            "choices": [{"message": {"content": '{"summary": "Платная модель ответила."}'}}],
        }

        with patch("club.ai_management.send_chat_request", return_value=response):
            result = get_zai_recommendations({}, model="glm-5.1")

        self.assertEqual(result["model"], "glm-5.1")
        self.assertEqual(result["summary"], "Платная модель ответила.")

    def test_socket_permission_error_gets_clear_message(self):
        request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions")
        socket_error = OSError(10013, "Сделана попытка доступа к сокету методом, запрещенным правами доступа")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError(socket_error)):
            with self.assertRaises(ManagementAIError) as context:
                send_chat_request(request, "OpenRouter", timeout=1)

        self.assertIn("WinError 10013", str(context.exception))
        self.assertIn("Перезапустите Django-сервер", str(context.exception))

    def test_chat_response_top_level_error_is_reported(self):
        request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions")

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"error": {"message": "No endpoints found for model"}}).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaises(ManagementAIError) as context:
                send_chat_request(request, "OpenRouter", timeout=1)

        self.assertIn("No endpoints found", str(context.exception))

    @override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_FALLBACK_MODELS="")
    def test_openrouter_weight_forecast_retries_without_response_format_when_content_is_empty(self):
        payload = {
            "client": {"name": self.client_profile.full_name},
            "history": [{"date": "2026-05-01", "weight": 82.0}],
            "forecast_request": {"expected_points": 1, "step_days": 7, "horizon_days": 7},
        }
        empty_response = {"model": "google/gemini-3.5-flash", "choices": [{"message": {}, "finish_reason": "error"}]}
        good_response = {
            "model": "google/gemini-3.5-flash",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Повторный запрос вернул прогноз.",
                                "confidence": "medium",
                                "trend_label": "снижение",
                                "points": [{"date": "2026-05-08", "probable": 81.6, "lower": 81.0, "upper": 82.0}],
                            }
                        )
                    }
                }
            ],
        }

        with patch("club.ai_management.send_chat_request", side_effect=[empty_response, good_response]) as mocked_send:
            result = get_openrouter_weight_forecast(payload, model="google/gemini-3.5-flash")

        first_request_body = json.loads(mocked_send.call_args_list[0].args[0].data.decode("utf-8"))
        second_request_body = json.loads(mocked_send.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("response_format", first_request_body)
        self.assertNotIn("response_format", second_request_body)
        self.assertEqual(result["summary"], "Повторный запрос вернул прогноз.")

    @override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_FALLBACK_MODELS="")
    def test_openrouter_weight_forecast_falls_back_when_retry_is_empty(self):
        payload = {
            "client": {"name": self.client_profile.full_name},
            "history": [{"date": "2026-05-01", "weight": 82.0}],
            "history_stats": {"latest_weight": 82.0, "weekly_delta_kg": -0.4},
            "forecast_request": {"expected_points": 1, "step_days": 7, "horizon_days": 7},
        }
        empty_response = {"model": "google/gemini-3.5-flash", "choices": [{"message": {}, "finish_reason": "error"}]}

        with patch("club.ai_management.send_chat_request", side_effect=[empty_response, empty_response]):
            result = get_openrouter_weight_forecast(payload, model="google/gemini-3.5-flash")

        self.assertIn("OpenRouter вернул пустой ответ", result["summary"])
        self.assertEqual(result["source"], "local_fallback")
        self.assertEqual(len(result["points"]), 1)

    @override_settings(OPENROUTER_API_KEY="test-key", OPENROUTER_FALLBACK_MODELS="")
    def test_openrouter_weight_forecast_falls_back_on_transient_ssl_error(self):
        payload = {
            "client": {"name": self.client_profile.full_name},
            "history": [{"date": "2026-05-01", "weight": 82.0}],
            "history_stats": {"latest_weight": 82.0, "weekly_delta_kg": -0.4},
            "forecast_request": {"expected_points": 1, "step_days": 7, "horizon_days": 7},
        }
        error = ManagementAIError(
            "Не удалось связаться с OpenRouter: <urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred>"
        )

        with patch("club.ai_management.send_chat_request", side_effect=[error, error]) as mocked_send:
            result = get_openrouter_weight_forecast(payload, model="anthropic/claude-sonnet-4.6")

        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(result["source"], "local_fallback")
        self.assertIn("OpenRouter временно не ответил", result["summary"])
        self.assertEqual(len(result["points"]), 1)

    def test_admin_ai_analysis_starts_background_job(self):
        self.client.login(username="admin", password="admin")

        with patch("club.views.queue_management_analysis") as mocked_queue:
            response = self.client.post(reverse("management_ai_analysis_start"), {"provider": "zai"})

        analysis = ManagementAIAnalysis.objects.get(requested_by=self.admin_user)
        self.assertRedirects(response, f"{reverse('admin_dashboards')}?analysis={analysis.pk}#management-ai")
        self.assertEqual(analysis.status, ManagementAIAnalysis.Status.QUEUED)
        self.assertEqual(analysis.provider, "zai")
        mocked_queue.assert_called_once_with(analysis.pk)

    @override_settings(OPENROUTER_SMART_MODEL="test/admin-smart-model")
    def test_admin_ai_analysis_saves_selected_model_tier(self):
        self.client.login(username="admin", password="admin")

        with patch("club.views.queue_management_analysis"):
            self.client.post(reverse("management_ai_analysis_start"), {"provider": "openrouter", "model_tier": "smart"})

        analysis = ManagementAIAnalysis.objects.get(requested_by=self.admin_user)
        self.assertEqual(analysis.provider, "openrouter")
        self.assertEqual(analysis.model, "test/admin-smart-model")

    def test_background_ai_analysis_uses_saved_provider(self):
        analysis = ManagementAIAnalysis.objects.create(
            requested_by=self.admin_user,
            provider="zai",
            model="glm-4.7-flash",
            payload={"business_kpis": {}},
        )
        result = {
            "model": "glm-4.7-flash",
            "summary": "Ответ сохранен.",
            "priority_actions": [],
            "risks": [],
            "trainer_actions": [],
            "growth_actions": [],
            "next_7_days": [],
        }

        with patch("club.ai_jobs.get_management_ai_recommendations", return_value=result) as mocked_ai:
            run_management_analysis(analysis.pk)

        mocked_ai.assert_called_once_with(analysis.payload, provider="zai", model=analysis.model)

    def test_trainer_ai_payload_detects_core_scenarios(self):
        today = timezone.localdate()
        Membership.objects.create(
            client=self.client_profile,
            type="Персональный",
            start_date=today - timedelta(days=20),
            end_date=today + timedelta(days=3),
            visits_total=12,
            visits_left=4,
        )
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="План на удержание",
            goal=self.client_profile.goal,
            start_date=today - timedelta(days=10),
            status="active",
        )
        WorkoutDay.objects.create(workout_plan=plan, title="Силовой день", day_number=1)

        payload = build_trainer_ai_payload(self.trainer)

        self.assertEqual(payload["trainer"]["clients_count"], 1)
        self.assertEqual(payload["scenario_counts"]["contact_today"], 1)
        self.assertEqual(payload["scenario_counts"]["renewal_support"], 1)
        self.assertEqual(payload["scenario_counts"]["workout_preparation"], 1)
        self.assertEqual(payload["clients"][0]["membership"]["expires_soon"], True)

    def test_trainer_ai_analysis_starts_background_job(self):
        self.client.login(username="trainer", password="trainer")

        with patch("club.views.queue_trainer_analysis") as mocked_queue:
            response = self.client.post(reverse("trainer_ai_analysis_start"), {"provider": "zai"})

        analysis = TrainerAIAnalysis.objects.get(requested_by=self.trainer_user)
        self.assertRedirects(response, f"{reverse('dashboard')}?trainer_analysis={analysis.pk}#trainer-ai")
        self.assertEqual(analysis.status, TrainerAIAnalysis.Status.QUEUED)
        self.assertEqual(analysis.provider, "zai")
        self.assertEqual(analysis.trainer, self.trainer)
        mocked_queue.assert_called_once_with(analysis.pk)

    @override_settings(OPENROUTER_FAST_MODEL="test/trainer-fast-model")
    def test_trainer_ai_analysis_saves_selected_model_tier(self):
        self.client.login(username="trainer", password="trainer")

        with patch("club.views.queue_trainer_analysis"):
            self.client.post(reverse("trainer_ai_analysis_start"), {"provider": "openrouter", "model_tier": "fast"})

        analysis = TrainerAIAnalysis.objects.get(requested_by=self.trainer_user)
        self.assertEqual(analysis.provider, "openrouter")
        self.assertEqual(analysis.model, "test/trainer-fast-model")

    def test_background_trainer_ai_analysis_uses_saved_provider(self):
        analysis = TrainerAIAnalysis.objects.create(
            requested_by=self.trainer_user,
            trainer=self.trainer,
            provider="zai",
            model="glm-4.7-flash",
            payload={"trainer": {"name": self.trainer.full_name}, "clients": []},
        )
        result = {
            "model": "glm-4.7-flash",
            "summary": "План тренера сохранен.",
            "priority_clients": [],
            "plan_adjustments": [],
            "communication_scripts": [],
            "upcoming_workouts": [],
            "renewal_support": [],
            "next_7_days": [],
        }

        with patch("club.ai_jobs.get_trainer_ai_recommendations", return_value=result) as mocked_ai:
            run_trainer_analysis(analysis.pk)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, TrainerAIAnalysis.Status.DONE)
        mocked_ai.assert_called_once_with(analysis.payload, provider="zai", model=analysis.model)

    def test_trainer_ai_parser_builds_structured_fallback_for_empty_response(self):
        payload = {
            "trainer": {"name": self.trainer.full_name, "clients_count": 1, "high_signals": 1, "medium_signals": 0},
            "clients": [
                {
                    "name": self.client_profile.full_name,
                    "workouts_30_days": 0,
                    "membership": {"status": "Активен", "end_date": "29.05.2026"},
                    "active_plan": None,
                    "signals": [
                        {
                            "id": "no_recent_activity",
                            "severity": "high",
                            "title": "Нет тренировок 20 дней",
                            "detail": "Пауза более 14 дней повышает риск ухода.",
                            "scenario": "contact_today",
                        }
                    ],
                }
            ],
        }

        result = parse_trainer_ai_recommendation('{"summary": ""}', "gpt-4o-mini", payload)

        self.assertIn("Тренер Первый", result["summary"])
        self.assertEqual(result["priority_clients"][0]["client"], self.client_profile.full_name)
        self.assertIn("Связаться", result["priority_clients"][0]["recommended_action"])

    def test_trainer_dashboard_renders_saved_ai_assistant_result(self):
        self.client.login(username="trainer", password="trainer")
        result = {
            "model": "gpt-4o-mini",
            "summary": "Сегодня тренеру стоит вернуть регулярность клиента.",
            "focus_score": 71,
            "priority_clients": [
                {
                    "priority": "high",
                    "client": self.client_profile.full_name,
                    "scenario": "контакт",
                    "title": "Вернуть регулярность",
                    "reason": "Нет тренировок более 14 дней.",
                    "recommended_action": "Связаться с клиентом и назначить ближайший слот.",
                    "message_draft": "Привет! Давай выберем удобное время на этой неделе.",
                    "business_effect": "Снижает риск ухода.",
                    "deadline": "29.05.2026",
                    "evidence": "Нет тренировок более 14 дней",
                }
            ],
            "plan_adjustments": [],
            "communication_scripts": [],
            "upcoming_workouts": [],
            "renewal_support": [],
            "next_7_days": ["Связаться с клиентом."],
        }
        analysis = TrainerAIAnalysis.objects.create(
            requested_by=self.trainer_user,
            trainer=self.trainer,
            provider="gptunnel",
            model="gpt-4o-mini",
            payload={},
            result=result,
            status=TrainerAIAnalysis.Status.DONE,
        )

        response = self.client.get(f"{reverse('dashboard')}?trainer_analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ИИ-ассистент тренера")
        self.assertContains(response, "Вернуть регулярность")

    def test_client_ai_payload_uses_only_current_client_data(self):
        today = timezone.localdate()
        Membership.objects.create(
            client=self.client_profile,
            type="Персональный",
            start_date=today - timedelta(days=20),
            end_date=today + timedelta(days=5),
            visits_total=12,
            visits_left=3,
        )

        payload = build_client_ai_payload(self.client_profile)

        self.assertEqual(payload["client"]["name"], self.client_profile.full_name)
        self.assertEqual(payload["scope"], "Только данные текущего клиента")
        self.assertEqual(payload["membership"]["expires_soon"], True)
        self.assertIn("local_recommendations", payload)

    def test_client_ai_analysis_starts_background_job(self):
        self.client.login(username="client", password="client")

        with patch("club.views.queue_client_analysis") as mocked_queue:
            response = self.client.post(reverse("client_ai_analysis_start"), {"provider": "zai"})

        analysis = ClientAIAnalysis.objects.get(requested_by=self.client_user)
        self.assertRedirects(response, f"{reverse('recommendations')}?client_analysis={analysis.pk}#client-ai")
        self.assertEqual(analysis.status, ClientAIAnalysis.Status.QUEUED)
        self.assertEqual(analysis.provider, "zai")
        self.assertEqual(analysis.client, self.client_profile)
        mocked_queue.assert_called_once_with(analysis.pk)

    @override_settings(OPENROUTER_BALANCED_MODEL="test/client-balanced-model")
    def test_client_ai_analysis_saves_selected_model_tier(self):
        self.client.login(username="client", password="client")

        with patch("club.views.queue_client_analysis"):
            self.client.post(reverse("client_ai_analysis_start"), {"provider": "openrouter", "model_tier": "balanced"})

        analysis = ClientAIAnalysis.objects.get(requested_by=self.client_user)
        self.assertEqual(analysis.provider, "openrouter")
        self.assertEqual(analysis.model, "test/client-balanced-model")

    def test_background_client_ai_analysis_uses_saved_provider(self):
        analysis = ClientAIAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="zai",
            model="glm-4.7-flash",
            payload={"client": {"name": self.client_profile.full_name}, "local_recommendations": {"items": []}},
        )
        result = {
            "model": "glm-4.7-flash",
            "summary": "Личный план клиента сохранен.",
            "priority_steps": [],
            "workout_focus": [],
            "nutrition_focus": [],
            "progress_focus": [],
            "questions_for_trainer": [],
            "next_7_days": [],
        }

        with patch("club.ai_jobs.get_client_ai_recommendations", return_value=result) as mocked_ai:
            run_client_analysis(analysis.pk)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, ClientAIAnalysis.Status.DONE)
        mocked_ai.assert_called_once_with(analysis.payload, provider="zai", model=analysis.model)

    def test_client_ai_parser_builds_structured_fallback_for_empty_response(self):
        payload = {
            "client": {"name": self.client_profile.full_name, "goal": "Похудение"},
            "workouts": {"workouts_30_days": 0, "days_since_last_workout": None},
            "nutrition": {"days_with_food": 1, "target": None},
            "progress": {"days_since_update": None},
            "membership": {"status": "Активен"},
            "active_plan": None,
            "schedule": {"next_appointment": {"start_at": "нет"}},
            "local_recommendations": {
                "score": 54,
                "items": [
                    {
                        "category": "Тренировки",
                        "priority": "high",
                        "title": "Начать фиксацию тренировок",
                        "reason": "Нет выполненных тренировок.",
                        "action": "Отметить ближайшую тренировку.",
                    }
                ],
            },
        }

        result = parse_client_ai_recommendation('{"summary": ""}', "gpt-4o-mini", payload)

        self.assertIn(self.client_profile.full_name, result["summary"])
        self.assertEqual(result["readiness_score"], 54)
        self.assertEqual(result["priority_steps"][0]["category"], "тренировки")
        self.assertGreaterEqual(len(result["next_7_days"]), 5)

    def test_client_ai_parser_falls_back_for_truncated_json_response(self):
        payload = {
            "client": {"name": self.client_profile.full_name, "goal": "Похудение"},
            "workouts": {"workouts_30_days": 3, "days_since_last_workout": 3},
            "nutrition": {"days_with_food": 0, "target": None},
            "progress": {"weight_delta_total": -1.8, "days_since_update": 2},
            "membership": {"status": "Активен"},
            "active_plan": {"title": "План на снижение веса", "completion_percent": 100},
            "schedule": {"next_appointment": {"start_at": "нет"}},
            "local_recommendations": {
                "score": 72,
                "items": [
                    {
                        "category": "Тренировки",
                        "priority": "medium",
                        "title": "Вернуть регулярность",
                        "reason": "За 30 дней только 3 тренировки.",
                        "action": "Записаться на ближайшую тренировку.",
                    }
                ],
            },
        }
        content = '{"summary": "Ирина, вы демонстрируете отличную динамику, но главный фокус - возоб'

        result = parse_client_ai_recommendation(content, "google/gemini-3.5-flash-20260519", payload)

        self.assertFalse(result["raw"])
        self.assertEqual(result["readiness_score"], 72)
        self.assertNotIn('{"summary"', result["summary"])
        self.assertGreaterEqual(len(result["priority_steps"]), 1)
        self.assertGreaterEqual(len(result["next_7_days"]), 5)

    def test_client_recommendations_page_renders_saved_ai_result(self):
        self.client.login(username="client", password="client")
        result = {
            "model": "gpt-4o-mini",
            "summary": "На этой неделе клиенту стоит вернуть регулярность.",
            "readiness_score": 68,
            "priority_steps": [
                {
                    "priority": "high",
                    "category": "тренировки",
                    "title": "Запланировать тренировку",
                    "reason": "Нет будущей записи.",
                    "action": "Открыть расписание и выбрать слот.",
                    "deadline": "29.05.2026",
                    "evidence": "Нет будущей записи",
                }
            ],
            "workout_focus": [],
            "nutrition_focus": [],
            "progress_focus": [],
            "questions_for_trainer": [],
            "next_7_days": ["Выбрать слот тренировки."],
        }
        analysis = ClientAIAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="gptunnel",
            model="gpt-4o-mini",
            payload={},
            result=result,
            status=ClientAIAnalysis.Status.DONE,
        )

        response = self.client.get(f"{reverse('recommendations')}?client_analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ИИ-коуч клиента")
        self.assertContains(response, "Запланировать тренировку")

    def test_client_recommendations_page_repairs_saved_raw_ai_result(self):
        self.client.login(username="client", password="client")
        payload = {
            "client": {"name": self.client_profile.full_name, "goal": "Похудение"},
            "workouts": {"workouts_30_days": 3, "days_since_last_workout": 3},
            "nutrition": {"days_with_food": 0, "target": None},
            "progress": {"weight_delta_total": -1.8, "days_since_update": 2},
            "membership": {"status": "Активен"},
            "active_plan": {"title": "План на снижение веса", "completion_percent": 100},
            "schedule": {"next_appointment": {"start_at": "нет"}},
            "local_recommendations": {
                "score": 72,
                "items": [
                    {
                        "category": "Тренировки",
                        "priority": "medium",
                        "title": "Вернуть регулярность",
                        "reason": "За 30 дней только 3 тренировки.",
                        "action": "Записаться на ближайшую тренировку.",
                    }
                ],
            },
        }
        analysis = ClientAIAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="openrouter",
            model="google/gemini-3.5-flash-20260519",
            payload=payload,
            result={
                "model": "google/gemini-3.5-flash-20260519",
                "summary": '{"summary": "Ирина, вы демонстрируете отличную динамику, но главный фокус - возоб',
                "priority_steps": [],
                "workout_focus": [],
                "nutrition_focus": [],
                "progress_focus": [],
                "questions_for_trainer": [],
                "next_7_days": [],
                "raw": True,
            },
            status=ClientAIAnalysis.Status.DONE,
        )

        response = self.client.get(f"{reverse('recommendations')}?client_analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '{"summary"')
        self.assertContains(response, "Вернуть регулярность")
        self.assertContains(response, "План на 7 дней")

    def test_client_ai_status_response_disables_cache(self):
        self.client.login(username="client", password="client")
        analysis = ClientAIAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="openrouter",
            model="google/gemini-3.5-flash",
            payload={},
        )

        response = self.client.get(reverse("client_ai_analysis_status", args=[analysis.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response.json()["finished"], False)

    def test_weight_forecast_payload_requires_enough_history(self):
        today = timezone.localdate()
        for index, weight in enumerate(["82.0", "81.4", "80.9", "80.5"]):
            ProgressRecord.objects.create(
                client=self.client_profile,
                record_date=today - timedelta(days=(3 - index) * 7),
                weight=Decimal(weight),
            )

        readiness = weight_forecast_readiness(self.client_profile)
        payload = build_weight_forecast_payload(self.client_profile)

        self.assertTrue(readiness["can_run"])
        self.assertEqual(payload["forecast_request"]["expected_points"], 8)
        self.assertEqual(payload["history_stats"]["records_count"], 4)
        self.assertEqual(payload["client"]["name"], self.client_profile.full_name)

    @override_settings(OPENROUTER_BALANCED_MODEL="test/forecast-balanced-model")
    def test_weight_forecast_starts_background_job(self):
        today = timezone.localdate()
        for index, weight in enumerate(["82.0", "81.5", "81.0", "80.4"]):
            ProgressRecord.objects.create(
                client=self.client_profile,
                record_date=today - timedelta(days=(3 - index) * 8),
                weight=Decimal(weight),
            )
        self.client.login(username="client", password="client")

        with patch("club.views.queue_weight_forecast_analysis") as mocked_queue:
            response = self.client.post(
                reverse("weight_forecast_analysis_start", args=[self.client_profile.pk]),
                {"provider": "openrouter", "model_tier": "balanced"},
            )

        analysis = WeightForecastAnalysis.objects.get(requested_by=self.client_user)
        self.assertRedirects(response, f"{reverse('progress_client', args=[self.client_profile.pk])}?forecast={analysis.pk}#weight-forecast")
        self.assertEqual(analysis.status, WeightForecastAnalysis.Status.QUEUED)
        self.assertEqual(analysis.provider, "openrouter")
        self.assertEqual(analysis.model, "test/forecast-balanced-model")
        mocked_queue.assert_called_once_with(analysis.pk)

    def test_weight_forecast_does_not_start_without_enough_data(self):
        self.client.login(username="client", password="client")

        with patch("club.views.queue_weight_forecast_analysis") as mocked_queue:
            response = self.client.post(reverse("weight_forecast_analysis_start", args=[self.client_profile.pk]))

        self.assertRedirects(response, f"{reverse('progress_client', args=[self.client_profile.pk])}#weight-forecast")
        self.assertEqual(WeightForecastAnalysis.objects.count(), 0)
        mocked_queue.assert_not_called()

    def test_background_weight_forecast_uses_saved_provider(self):
        analysis = WeightForecastAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="zai",
            model="glm-4.7-flash",
            payload={"history": [{"date": "2026-05-01", "weight": 82.0}], "forecast_request": {"expected_points": 1}},
        )
        result = {
            "model": "glm-4.7-flash",
            "summary": "Вес вероятно будет снижаться плавно.",
            "confidence": "medium",
            "trend_label": "снижение",
            "points": [{"date": "2026-05-08", "probable": 81.5, "lower": 81.0, "upper": 82.0}],
            "assumptions": [],
            "risks": [],
            "recommendations": [],
        }

        with patch("club.ai_jobs.get_weight_forecast", return_value=result) as mocked_ai:
            run_weight_forecast_analysis(analysis.pk)

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, WeightForecastAnalysis.Status.DONE)
        self.assertEqual(analysis.result["points"][0]["probable"], 81.5)
        mocked_ai.assert_called_once_with(analysis.payload, provider="zai", model=analysis.model)

    def test_weight_forecast_parser_normalizes_bounds(self):
        payload = {"forecast_request": {"expected_points": 2}}
        content = json.dumps(
            {
                "summary": "Тренд осторожно снижается.",
                "confidence": "high",
                "trend_label": "снижение",
                "points": [
                    {"date": "2026-06-12", "probable": 80.1, "lower": 79.6, "upper": 80.7},
                    {"date": "19.06.2026", "expected": "79,7", "low": 79.0, "high": 80.4},
                ],
            },
            ensure_ascii=False,
        )

        result = parse_weight_forecast(content, "gpt-4o-mini", payload)

        self.assertFalse(result["raw"])
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["points"][1]["date"], "2026-06-19")
        self.assertEqual(result["points"][1]["probable"], 79.7)

    def test_progress_page_renders_saved_weight_forecast(self):
        today = timezone.localdate()
        history = []
        for index, weight in enumerate(["82.0", "81.6", "81.0", "80.6"]):
            date = today - timedelta(days=(3 - index) * 7)
            ProgressRecord.objects.create(client=self.client_profile, record_date=date, weight=Decimal(weight))
            history.append({"date": date.isoformat(), "weight": float(weight)})
        analysis = WeightForecastAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="gptunnel",
            model="gpt-4o-mini",
            payload={"history": history, "forecast_request": {"expected_points": 1}},
            result={
                "model": "gpt-4o-mini",
                "summary": "Вероятный вариант показывает плавное снижение веса.",
                "confidence": "medium",
                "trend_label": "снижение",
                "points": [{"date": (today + timedelta(days=7)).isoformat(), "probable": 80.0, "lower": 79.4, "upper": 80.8}],
                "assumptions": ["Сохранится текущая регулярность."],
                "risks": [],
                "recommendations": [],
            },
            status=WeightForecastAnalysis.Status.DONE,
        )
        self.client.login(username="client", password="client")

        response = self.client.get(f"{reverse('progress')}?forecast={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ИИ-прогноз веса")
        self.assertContains(response, "Вероятный вариант")
        self.assertContains(response, "weightForecast")

    def test_failed_empty_openrouter_weight_forecast_renders_fallback_chart(self):
        today = timezone.localdate()
        history = []
        for index, weight in enumerate(["82.0", "81.6", "81.0", "80.6"]):
            date = today - timedelta(days=(3 - index) * 7)
            ProgressRecord.objects.create(client=self.client_profile, record_date=date, weight=Decimal(weight))
            history.append({"date": date.isoformat(), "weight": float(weight)})
        analysis = WeightForecastAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="openrouter",
            model="google/gemini-3.5-flash",
            payload={
                "client": {"name": self.client_profile.full_name},
                "history": history,
                "history_stats": {"latest_weight": 80.6, "weekly_delta_kg": -0.4},
                "forecast_request": {"expected_points": 2, "step_days": 7, "horizon_days": 14},
            },
            status=WeightForecastAnalysis.Status.FAILED,
            error="В ответе OpenRouter не найден текст рекомендации.",
        )
        self.client.login(username="client", password="client")

        response = self.client.get(f"{reverse('progress')}?forecast={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Исходный ответ агрегатора")
        self.assertContains(response, "Агрегатор не вернул пригодный ИИ-прогноз")
        self.assertContains(response, "weightForecast")

    def test_stale_ai_analysis_is_marked_failed(self):
        analysis = ClientAIAnalysis.objects.create(
            requested_by=self.client_user,
            client=self.client_profile,
            provider="openrouter",
            model="google/gemini-3.5-flash",
            payload={},
        )
        old_time = timezone.now() - timedelta(minutes=45)
        ClientAIAnalysis.objects.filter(pk=analysis.pk).update(created_at=old_time)
        analysis.refresh_from_db()

        mark_stale_analysis_failed(analysis, "ИИ-коуч клиента")

        analysis.refresh_from_db()
        self.assertEqual(analysis.status, ClientAIAnalysis.Status.FAILED)
        self.assertIn("перезапущен", analysis.error)

    def test_admin_ai_analysis_renders_recommendations(self):
        self.client.login(username="admin", password="admin")
        ai_result = {
            "model": "glm-4.7-flash",
            "summary": "Клубу нужно снизить риск ухода клиентов.",
            "priority_actions": [
                {
                    "priority": "high",
                    "title": "Снизить риск ухода",
                    "metric": "клиенты в зоне риска",
                    "action": "Связаться с клиентами без тренировок за 14 дней.",
                }
            ],
            "risks": [{"title": "Низкая активность", "detail": "Есть клиенты без недавних тренировок."}],
            "next_7_days": ["Передать список клиентов тренерам."],
        }
        analysis = ManagementAIAnalysis.objects.create(
            requested_by=self.admin_user,
            provider="OpenRouter",
            model="openai/gpt-4.1-mini",
            payload={},
            result=ai_result,
            status=ManagementAIAnalysis.Status.DONE,
        )
        response = self.client.get(f"{reverse('admin_dashboards')}?analysis={analysis.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Снизить риск ухода")

    def test_ai_parser_builds_structured_fallback_for_empty_response(self):
        payload = {
            "business_kpis": {
                "total_clients": 12,
                "risk_clients_count": 2,
                "expiring_memberships_7_days": 1,
                "workouts_30_days": 18,
                "average_workouts_per_client_30_days": 1.5,
                "active_plan_average_completion_percent": 42,
            },
            "trainer_load": [{"trainer": "Тренер Первый", "low_activity_count": 2}],
        }

        result = parse_ai_recommendation('{"summary": ""}', "glm-4.7-flash", payload)

        self.assertIn("2 клиентов", result["summary"])
        self.assertGreaterEqual(len(result["priority_actions"]), 3)
        self.assertGreaterEqual(len(result["next_7_days"]), 5)

    def test_ai_parser_accepts_markdown_json_and_extra_sections(self):
        content = """```json
        {
          "summary": "Нужно усилить удержание.",
          "health_score": 72,
          "priority_actions": [{"priority": "высокий", "title": "Позвонить клиентам", "action": "Связаться с группой риска"}],
          "trainer_actions": [{"trainer": "Марина", "focus": "Пропуски", "action": "Назначить слоты"}],
          "growth_actions": [{"title": "Продления", "action": "Предложить новый цикл", "metric": "продления"}],
          "next_7_days": ["Собрать список риска"]
        }
        ```"""

        result = parse_ai_recommendation(content, "glm-4.7-flash")

        self.assertEqual(result["health_score"], 72)
        self.assertEqual(result["priority_actions"][0]["priority"], "high")
        self.assertEqual(result["trainer_actions"][0]["trainer"], "Марина")
        self.assertEqual(result["growth_actions"][0]["metric"], "продления")

    def test_ai_parser_shortens_metrics_and_moves_past_deadlines(self):
        content = """{
          "summary": "Нужно усилить удержание.",
          "priority_actions": [
            {
              "priority": "high",
              "title": "Вернуть клиентов",
              "metric": "expiring_memberships_7_days",
              "action": "Позвонить клиентам",
              "deadline": "05.05.2026"
            }
          ]
        }"""

        result = parse_ai_recommendation(content, "openrouter/free")
        action = result["priority_actions"][0]

        self.assertEqual(action["metric"], "Истекают абонементы")
        self.assertNotEqual(action["deadline"], "05.05.2026")

    def test_recommendations_use_client_data_and_respect_access(self):
        recommendations = build_client_recommendations(self.client_profile)
        self.assertGreaterEqual(len(recommendations["items"]), 1)
        self.assertIn("score", recommendations)

        self.client.login(username="client", password="client")
        response = self.client.get(reverse("recommendations"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ИИ-рекомендации")

        response = self.client.get(reverse("recommendations_client", args=[self.other_client_profile.id]))
        self.assertEqual(response.status_code, 403)

    def test_client_can_leave_club_review(self):
        self.client.login(username="client", password="client")
        response = self.client.post(
            reverse("reviews"),
            {
                "rating": 5,
                "title": "Очень нравится формат",
                "text": "Тренеры внимательные, зал чистый, удобно следить за прогрессом.",
            },
        )

        self.assertEqual(response.status_code, 302)
        review = ClubReview.objects.get(client=self.client_profile)
        self.assertEqual(review.rating, 5)
        self.assertTrue(review.is_published)

        response = self.client.get(reverse("reviews"))
        self.assertContains(response, "Очень нравится формат")

    def test_trainer_cannot_leave_club_review(self):
        self.client.login(username="trainer", password="trainer")
        response = self.client.post(
            reverse("reviews"),
            {
                "rating": 4,
                "title": "Служебный отзыв",
                "text": "Такой отзыв не должен создаваться от имени тренера.",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ClubReview.objects.count(), 0)

    def test_product_macros_are_calculated_by_weight(self):
        product = Product.objects.create(
            name="Творог",
            calories_per_100g=Decimal("120"),
            protein_per_100g=Decimal("18"),
            fat_per_100g=Decimal("5"),
            carbs_per_100g=Decimal("2"),
        )
        meal = Meal.objects.create(client=self.client_profile, meal_date=timezone.localdate(), meal_type=MealType.BREAKFAST)
        item = MealItem.objects.create(meal=meal, product=product, weight_grams=Decimal("150"))

        self.assertEqual(item.calories, Decimal("180.0"))
        self.assertEqual(item.protein, Decimal("27.0"))
        self.assertEqual(item.fat, Decimal("7.5"))
        self.assertEqual(item.carbs, Decimal("3.0"))

    def test_type_markers_use_other_color_for_non_basic_values(self):
        self.assertEqual(exercise_type_class("Силовое"), "thumb-strength")
        self.assertEqual(exercise_type_class("Кардио"), "thumb-cardio")
        self.assertEqual(exercise_type_class("Функциональное"), "thumb-mobility")
        self.assertEqual(exercise_type_class("Статическое"), "thumb-other")

        protein_product = Product(
            name="Белковый продукт",
            calories_per_100g=Decimal("120"),
            protein_per_100g=Decimal("20"),
            fat_per_100g=Decimal("1"),
            carbs_per_100g=Decimal("2"),
        )
        mixed_product = Product(
            name="Смешанный продукт",
            calories_per_100g=Decimal("180"),
            protein_per_100g=Decimal("10"),
            fat_per_100g=Decimal("6"),
            carbs_per_100g=Decimal("12"),
        )
        self.assertEqual(product_macro_class(protein_product), "icon-protein")
        self.assertEqual(product_macro_class(mixed_product), "icon-other")

    def test_client_can_complete_own_workout(self):
        membership = self.active_membership(visits_total=3, visits_left=3)
        exercise = Exercise.objects.create(name="Планка", muscle_group="Кор", exercise_type="Статическое")
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="Тестовый план",
            goal="weight_loss",
            start_date=timezone.localdate(),
        )
        day = WorkoutDay.objects.create(workout_plan=plan, title="День 1", day_number=1)
        workout_exercise = WorkoutExercise.objects.create(workout_day=day, exercise=exercise)

        self.client.login(username="client", password="client")
        response = self.client.post(
            reverse("complete_workout", args=[day.id]),
            {
                "mood": "хорошо",
                f"exercise_{workout_exercise.id}_done": "on",
                f"exercise_{workout_exercise.id}_sets": "3",
                f"exercise_{workout_exercise.id}_reps": "12",
                f"exercise_{workout_exercise.id}_weight": "0",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client_profile.completed_workouts.count(), 1)
        self.assertIsNone(self.client_profile.completed_workouts.get().appointment)
        membership.refresh_from_db()
        self.assertEqual(membership.visits_left, 2)

    def test_client_can_update_workout_load_from_progress_page(self):
        exercise = Exercise.objects.create(name="Жим лежа", muscle_group="Грудь", exercise_type="Силовое")
        plan = WorkoutPlan.objects.create(
            client=self.client_profile,
            trainer=self.trainer,
            title="Силовой блок",
            goal="weight_loss",
            start_date=timezone.localdate(),
        )
        day = WorkoutDay.objects.create(workout_plan=plan, title="День 1", day_number=1)
        workout_exercise = WorkoutExercise.objects.create(
            workout_day=day,
            exercise=exercise,
            sets_count=3,
            reps_count=10,
            recommended_weight=Decimal("35.0"),
        )
        workout = CompletedWorkout.objects.create(client=self.client_profile, workout_day=day, completed_at=timezone.now())

        self.client.login(username="client", password="client")
        response = self.client.post(
            reverse("progress"),
            {
                "form_type": "workout_load",
                "workout_id": workout.id,
                f"exercise_{workout_exercise.id}_done": "on",
                f"exercise_{workout_exercise.id}_sets": "4",
                f"exercise_{workout_exercise.id}_reps": "8",
                f"exercise_{workout_exercise.id}_weight": "42.5",
            },
        )

        self.assertRedirects(response, f"{reverse('progress_client', args=[self.client_profile.pk])}#training-load")
        entry = CompletedExercise.objects.get(completed_workout=workout, workout_exercise=workout_exercise)
        self.assertEqual(entry.actual_sets, 4)
        self.assertEqual(entry.actual_reps, 8)
        self.assertEqual(entry.actual_weight, Decimal("42.5"))

        response = self.client.get(reverse("progress"))
        self.assertContains(response, "Тоннаж тренировок")
        self.assertContains(response, "1360.0")
