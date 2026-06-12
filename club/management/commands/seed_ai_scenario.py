from datetime import time, timedelta

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from club.models import (
    ClientProfile,
    CompletedExercise,
    CompletedWorkout,
    ManagementAIAnalysis,
    MembershipStatus,
    Role,
)
from club.utils import bootstrap_roles


SCENARIO_LABELS = {
    "retention_crisis": "retention crisis",
    "healthy_growth": "healthy growth",
    "renewal_wave": "renewal wave",
    "trainer_imbalance": "trainer imbalance",
}


class Command(BaseCommand):
    help = "Seeds one repeatable club state for testing management AI recommendations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenario",
            choices=SCENARIO_LABELS,
            default="retention_crisis",
            help="Scenario to place into the local database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        scenario = options["scenario"]
        today = timezone.localdate()

        self.reset_client_state()
        call_command("seed_demo", verbosity=0)

        clients = list(
            ClientProfile.objects.filter(user__username__startswith="client")
            .select_related("trainer", "user")
            .order_by("user__username")
        )
        self.clear_baseline_activity(clients)
        getattr(self, f"seed_{scenario}")(clients, today)

        self.stdout.write(self.style.SUCCESS(f"AI scenario seeded: {SCENARIO_LABELS[scenario]}."))
        self.stdout.write(self.summary(clients, today))

    def reset_client_state(self):
        bootstrap_roles()
        ManagementAIAnalysis.objects.all().delete()
        User.objects.filter(groups__name=Role.CLIENT).distinct().delete()

    def clear_baseline_activity(self, clients):
        CompletedWorkout.objects.filter(client__in=clients).delete()

    def seed_retention_crisis(self, clients, today):
        self.set_memberships(clients, today, [2, 5, -4, -8, 1, -2, 20, 25])
        self.set_workouts(
            clients,
            today,
            [
                [3],
                [20],
                [],
                [],
                [25],
                [],
                [],
                [4],
            ],
        )

    def seed_healthy_growth(self, clients, today):
        self.set_memberships(clients, today, [28, 34, 42, 55, 31, 46, 38, 60])
        stable_offsets = [1, 5, 8, 12, 15, 19, 22, 26, 29, 33, 36, 40, 43, 47, 50, 54]
        self.set_workouts(clients, today, [stable_offsets for _ in clients])

    def seed_renewal_wave(self, clients, today):
        self.set_memberships(clients, today, [1, 2, 3, 4, 5, 6, 7, 24])
        self.set_workouts(clients, today, [[2, 6, 10] for _ in clients])

    def seed_trainer_imbalance(self, clients, today):
        trainers = [client.trainer for client in clients if client.trainer]
        lead_trainer = trainers[0]
        support_trainer = next(trainer for trainer in trainers if trainer != lead_trainer)
        for index, client in enumerate(clients):
            client.trainer = lead_trainer if index < 6 else support_trainer
            client.save(update_fields=["trainer"])

        self.set_memberships(clients, today, [32, 36, 41, 46, 50, 55, 28, 38])
        self.set_workouts(
            clients,
            today,
            [
                [1, 5, 9],
                [2, 7, 12],
                [3, 8],
                [19],
                [],
                [23],
                [2, 6, 10],
                [4, 11],
            ],
        )

    def set_memberships(self, clients, today, end_offsets):
        for index, (client, end_offset) in enumerate(zip(clients, end_offsets), start=1):
            membership = client.current_membership()
            if not membership:
                continue
            membership.start_date = today - timedelta(days=30)
            membership.end_date = today + timedelta(days=end_offset)
            membership.visits_total = 30 if index % 2 else 12
            membership.visits_left = 8 if end_offset >= 0 else 0
            membership.status = MembershipStatus.ACTIVE
            membership.save()

    def set_workouts(self, clients, today, workout_offsets):
        for client, offsets in zip(clients, workout_offsets):
            plan = client.active_plan()
            days = list(plan.days.prefetch_related("exercises").all()) if plan else []
            if not days:
                continue
            for index, offset in enumerate(offsets):
                workout_day = days[index % len(days)]
                completed_at = timezone.make_aware(
                    timezone.datetime.combine(today - timedelta(days=offset), time(hour=18, minute=index))
                )
                workout = CompletedWorkout.objects.create(
                    client=client,
                    workout_day=workout_day,
                    completed_at=completed_at,
                    mood="Scenario",
                    comment="AI scenario workout",
                )
                CompletedExercise.objects.bulk_create(
                    [
                        CompletedExercise(
                            completed_workout=workout,
                            workout_exercise=item,
                            actual_sets=item.sets_count,
                            actual_reps=item.reps_count,
                            actual_weight=item.recommended_weight,
                            is_completed=True,
                            comment="Scenario",
                        )
                        for item in workout_day.exercises.all()
                    ]
                )

    def summary(self, clients, today):
        recent_border = today - timedelta(days=30)
        inactive_border = today - timedelta(days=14)
        workouts_30 = CompletedWorkout.objects.filter(client__in=clients, completed_at__date__gte=recent_border).count()
        active_period_clients = (
            ClientProfile.objects.filter(pk__in=[client.pk for client in clients], completed_workouts__completed_at__date__gte=recent_border)
            .distinct()
            .count()
        )
        low_activity = sum(
            1
            for client in clients
            if not client.last_workout_at() or client.last_workout_at().date() < inactive_border
        )
        expiring = sum(1 for client in clients if client.current_membership() and client.current_membership().expires_soon)
        expired = sum(
            1
            for client in clients
            if client.current_membership() and client.current_membership().status == MembershipStatus.EXPIRED
        )
        return (
            f"clients={len(clients)}, active_period_clients_30={active_period_clients}, "
            f"workouts_30={workouts_30}, low_activity={low_activity}, "
            f"expiring_memberships_7={expiring}, expired_memberships={expired}"
        )
