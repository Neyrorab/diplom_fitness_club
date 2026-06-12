from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import ClientProfile, Membership, MembershipStatus, Role, TrainerProfile


def bootstrap_roles():
    for role_name, _ in Role.CHOICES:
        Group.objects.get_or_create(name=role_name)


def user_role(user):
    if not user.is_authenticated:
        return None
    if user.is_superuser or user.groups.filter(name=Role.ADMIN).exists():
        return Role.ADMIN
    if user.groups.filter(name=Role.TRAINER).exists():
        return Role.TRAINER
    if user.groups.filter(name=Role.CLIENT).exists():
        return Role.CLIENT
    return None


def is_admin(user):
    return user_role(user) == Role.ADMIN


def is_trainer(user):
    return user_role(user) == Role.TRAINER


def is_client(user):
    return user_role(user) == Role.CLIENT


def trainer_profile_for(user):
    if not is_trainer(user):
        return None
    return getattr(user, "trainer_profile", None)


def client_profile_for(user):
    if not is_client(user):
        return None
    return getattr(user, "client_profile", None)


def clients_available_for(user):
    if is_admin(user):
        return ClientProfile.objects.select_related("trainer", "user").all()
    if is_trainer(user):
        trainer = trainer_profile_for(user)
        return ClientProfile.objects.select_related("trainer", "user").filter(trainer=trainer)
    if is_client(user):
        client = client_profile_for(user)
        return ClientProfile.objects.select_related("trainer", "user").filter(pk=getattr(client, "pk", None))
    return ClientProfile.objects.none()


def get_client_for_user(user, pk):
    client = get_object_or_404(ClientProfile.objects.select_related("trainer", "user"), pk=pk)
    if not can_view_client(user, client):
        raise PermissionDenied("Недостаточно прав для просмотра карточки клиента.")
    return client


def can_view_client(user, client):
    if is_admin(user):
        return True
    if is_trainer(user):
        trainer = trainer_profile_for(user)
        return trainer is not None and client.trainer_id == trainer.id
    if is_client(user):
        own_client = client_profile_for(user)
        return own_client is not None and own_client.id == client.id
    return False


def can_manage_client(user, client):
    return is_admin(user) or (is_trainer(user) and client.trainer_id == getattr(trainer_profile_for(user), "id", None))


def dashboard_metrics(user):
    clients = clients_available_for(user)
    today = timezone.localdate()
    recent_border = today - timedelta(days=30)
    inactive_border = today - timedelta(days=14)

    active_clients = clients.filter(status="active").count()
    total_clients = clients.count()
    expiring_memberships = Membership.objects.filter(
        client__in=clients,
        status=MembershipStatus.ACTIVE,
        end_date__range=(today, today + timedelta(days=7)),
    ).select_related("client")
    active_period_clients = clients.filter(completed_workouts__completed_at__date__gte=recent_border).distinct().count()
    low_activity_clients = clients.annotate(last_activity_at=Max("completed_workouts__completed_at")).filter(
        Q(last_activity_at__isnull=True) | Q(last_activity_at__date__lt=inactive_border)
    )

    return {
        "total_clients": total_clients,
        "active_clients": active_clients,
        "active_period_clients": active_period_clients,
        "expiring_memberships": expiring_memberships,
        "low_activity_clients": low_activity_clients,
    }


def trainer_load_queryset(clients=None):
    queryset = TrainerProfile.objects.all()
    if clients is not None:
        trainer_ids = clients.exclude(trainer__isnull=True).values_list("trainer_id", flat=True)
        queryset = queryset.filter(id__in=trainer_ids)
    return queryset.annotate(
        clients_count=Count("clients", distinct=True),
        active_clients_count=Count("clients", filter=Q(clients__status="active"), distinct=True),
    )
