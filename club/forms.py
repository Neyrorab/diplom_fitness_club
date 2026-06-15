from datetime import datetime, timedelta

from django import forms
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ClubReview,
    ClientProfile,
    Exercise,
    Meal,
    MealItem,
    Membership,
    NutritionTarget,
    Product,
    ProgressRecord,
    Role,
    ScheduleSlot,
    TrainerComment,
    TrainerReview,
    TrainerProfile,
    WorkoutDay,
    WorkoutExercise,
    WorkoutPlan,
)


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            css = "form-control"
            if isinstance(field.widget, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                css = "form-select"
            field.widget.attrs["class"] = f"{field.widget.attrs.get('class', '')} {css}".strip()
            if field.required:
                field.label = f"{field.label} *"


class DateInput(forms.DateInput):
    input_type = "date"


class TimeInput(forms.TimeInput):
    input_type = "time"


def ensure_role_group(role_name):
    group, _ = Group.objects.get_or_create(name=role_name)
    return group


class ClientCreateForm(StyledFormMixin, forms.Form):
    username = forms.CharField(label="Логин", max_length=150)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    email = forms.EmailField(label="Email", required=False)
    full_name = forms.CharField(label="ФИО", max_length=160)
    phone = forms.CharField(label="Телефон", max_length=32, required=False)
    birth_date = forms.DateField(label="Дата рождения", required=False, widget=DateInput)
    height = forms.IntegerField(label="Рост, см", min_value=80, max_value=240, required=False)
    goal = forms.ChoiceField(label="Цель", choices=ClientProfile._meta.get_field("goal").choices)
    training_level = forms.CharField(label="Уровень подготовки", required=False)
    health_limitations = forms.CharField(label="Ограничения по здоровью", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    trainer = forms.ModelChoiceField(label="Тренер", queryset=TrainerProfile.objects.none(), required=False)
    status = forms.ChoiceField(label="Статус", choices=ClientProfile._meta.get_field("status").choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trainer"].queryset = TrainerProfile.objects.filter(status="active")

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise ValidationError("Пользователь с таким логином уже существует.")
        return username

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("phone") and not cleaned.get("email"):
            raise ValidationError("Укажите телефон или email клиента.")
        return cleaned

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            username=data["username"],
            password=data["password"],
            email=data.get("email", ""),
            first_name=data["full_name"],
        )
        user.groups.add(ensure_role_group(Role.CLIENT))
        return ClientProfile.objects.create(
            user=user,
            full_name=data["full_name"],
            phone=data.get("phone", ""),
            birth_date=data.get("birth_date"),
            height=data.get("height"),
            goal=data["goal"],
            training_level=data.get("training_level", ""),
            health_limitations=data.get("health_limitations", ""),
            trainer=data.get("trainer"),
            status=data["status"],
        )


class ClientEditForm(StyledFormMixin, forms.ModelForm):
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = ClientProfile
        fields = [
            "full_name",
            "phone",
            "birth_date",
            "height",
            "goal",
            "training_level",
            "health_limitations",
            "trainer",
            "status",
        ]
        widgets = {"birth_date": DateInput(), "health_limitations": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, allow_contact_fields=True, **kwargs):
        self.allow_contact_fields = allow_contact_fields
        super().__init__(*args, **kwargs)
        if not allow_contact_fields:
            self.fields.pop("phone", None)
            self.fields.pop("email", None)
            return
        self.fields["email"].initial = self.instance.user.email if self.instance.pk else ""

    def save(self, commit=True):
        instance = super().save(commit)
        if "email" in self.fields:
            instance.user.email = self.cleaned_data.get("email", "")
        instance.user.first_name = instance.full_name
        update_fields = ["first_name"]
        if "email" in self.fields:
            update_fields.append("email")
        instance.user.save(update_fields=update_fields)
        return instance


class TrainerCreateForm(StyledFormMixin, forms.Form):
    username = forms.CharField(label="Логин", max_length=150)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    email = forms.EmailField(label="Email", required=False)
    full_name = forms.CharField(label="ФИО", max_length=160)
    phone = forms.CharField(label="Телефон", max_length=32)
    specialization = forms.CharField(label="Специализация", max_length=160)
    experience_years = forms.IntegerField(label="Стаж, лет", min_value=0, max_value=80, initial=1)
    status = forms.ChoiceField(label="Статус", choices=TrainerProfile._meta.get_field("status").choices)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise ValidationError("Пользователь с таким логином уже существует.")
        return username

    @transaction.atomic
    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            username=data["username"],
            password=data["password"],
            email=data.get("email", ""),
            first_name=data["full_name"],
        )
        user.groups.add(ensure_role_group(Role.TRAINER))
        return TrainerProfile.objects.create(
            user=user,
            full_name=data["full_name"],
            phone=data["phone"],
            specialization=data["specialization"],
            experience_years=data["experience_years"],
            status=data["status"],
        )


class TrainerEditForm(StyledFormMixin, forms.ModelForm):
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = TrainerProfile
        fields = ["full_name", "phone", "specialization", "experience_years", "status"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].initial = self.instance.user.email if self.instance.pk else ""

    def save(self, commit=True):
        instance = super().save(commit)
        instance.user.email = self.cleaned_data.get("email", "")
        instance.user.first_name = instance.full_name
        instance.user.save(update_fields=["email", "first_name"])
        return instance


class MembershipForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["type", "start_date", "end_date", "visits_total", "visits_left", "status"]
        widgets = {"start_date": DateInput(), "end_date": DateInput()}

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise ValidationError("Дата окончания не может быть раньше даты начала.")
        return cleaned


class ExerciseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Exercise
        fields = ["name", "muscle_group", "exercise_type", "technique_description", "is_active"]
        widgets = {"technique_description": forms.Textarea(attrs={"rows": 3})}


class ProductForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "calories_per_100g", "protein_per_100g", "fat_per_100g", "carbs_per_100g", "is_active"]


class WorkoutPlanForm(StyledFormMixin, forms.ModelForm):
    template = forms.ModelChoiceField(
        label="Шаблон",
        queryset=WorkoutPlan.objects.none(),
        required=False,
        help_text="Можно оставить пустым и создать план с нуля.",
    )

    class Meta:
        model = WorkoutPlan
        fields = ["title", "goal", "description", "start_date", "end_date", "status", "template"]
        widgets = {"start_date": DateInput(), "end_date": DateInput(), "description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = WorkoutPlan.objects.filter(is_template=True)


class WorkoutDayForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = WorkoutDay
        fields = ["title", "day_number", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class WorkoutExerciseForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = WorkoutExercise
        fields = ["exercise", "sets_count", "reps_count", "recommended_weight", "rest_seconds", "comment", "order_number"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["exercise"].queryset = Exercise.objects.filter(is_active=True)


class ScheduleSlotForm(StyledFormMixin, forms.Form):
    date = forms.DateField(label="Дата", initial=timezone.localdate, widget=DateInput)
    start_time = forms.TimeField(label="Начало", widget=TimeInput(format="%H:%M"))
    end_time = forms.TimeField(label="Окончание", widget=TimeInput(format="%H:%M"))
    repeat_weeks = forms.IntegerField(label="Повторить по неделям", min_value=0, max_value=8, initial=0)
    note = forms.CharField(label="Комментарий", required=False, max_length=240)

    def __init__(self, *args, trainer=None, created_by=None, **kwargs):
        self.trainer = trainer
        self.created_by = created_by
        self._slots = []
        super().__init__(*args, **kwargs)
        self.fields["repeat_weeks"].help_text = "0 - создать только один слот, 4 - повторить это время на 4 недели вперед."

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get("date")
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")
        repeat_weeks = cleaned.get("repeat_weeks") or 0
        if not (date and start_time and end_time):
            return cleaned
        if not self.trainer:
            raise ValidationError("Для создания расписания нужен профиль тренера.")

        start_at = timezone.make_aware(datetime.combine(date, start_time))
        end_at = timezone.make_aware(datetime.combine(date, end_time))
        if end_at <= start_at:
            raise ValidationError("Время окончания должно быть позже времени начала.")

        slots = []
        for week in range(repeat_weeks + 1):
            slot = ScheduleSlot(
                trainer=self.trainer,
                start_at=start_at + timedelta(weeks=week),
                end_at=end_at + timedelta(weeks=week),
                note=cleaned.get("note", ""),
                created_by=self.created_by,
            )
            try:
                slot.full_clean()
            except ValidationError as error:
                raise ValidationError(f"{slot.start_at:%d.%m.%Y %H:%M}: {error.messages[0]}") from error
            slots.append(slot)

        self._slots = slots
        return cleaned

    def save(self):
        created = []
        for slot in self._slots:
            slot.save()
            created.append(slot)
        return created


class AppointmentCancelForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(label="Причина отмены", required=False, max_length=240)


class MealItemForm(StyledFormMixin, forms.Form):
    meal_date = forms.DateField(label="Дата", initial=timezone.localdate, widget=DateInput)
    meal_type = forms.ChoiceField(label="Прием пищи", choices=Meal._meta.get_field("meal_type").choices)
    product = forms.ModelChoiceField(label="Продукт", queryset=Product.objects.none(), required=False)
    custom_name = forms.CharField(label="Свое блюдо", required=False, max_length=140)
    weight_grams = forms.DecimalField(label="Вес, г", min_value=1, max_digits=7, decimal_places=1)
    calories = forms.DecimalField(label="Ккал", required=False, min_value=0, max_digits=8, decimal_places=1)
    protein = forms.DecimalField(label="Белки", required=False, min_value=0, max_digits=7, decimal_places=1)
    fat = forms.DecimalField(label="Жиры", required=False, min_value=0, max_digits=7, decimal_places=1)
    carbs = forms.DecimalField(label="Углеводы", required=False, min_value=0, max_digits=7, decimal_places=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        custom_name = cleaned.get("custom_name")
        if not product and not custom_name:
            raise ValidationError("Выберите продукт или укажите свое блюдо.")
        if not product:
            required = ("calories", "protein", "fat", "carbs")
            if any(cleaned.get(name) is None for name in required):
                raise ValidationError("Для своего блюда укажите калории и БЖУ.")
        return cleaned

    @transaction.atomic
    def save_for_client(self, client):
        data = self.cleaned_data
        meal, _ = Meal.objects.get_or_create(
            client=client,
            meal_date=data["meal_date"],
            meal_type=data["meal_type"],
        )
        return MealItem.objects.create(
            meal=meal,
            product=data.get("product"),
            custom_name=data.get("custom_name", ""),
            weight_grams=data["weight_grams"],
            calories=data.get("calories") or 0,
            protein=data.get("protein") or 0,
            fat=data.get("fat") or 0,
            carbs=data.get("carbs") or 0,
        )


class NutritionTargetForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = NutritionTarget
        fields = ["calories_target", "protein_target", "fat_target", "carbs_target", "start_date", "end_date"]
        widgets = {"start_date": DateInput(), "end_date": DateInput()}

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            raise ValidationError("Дата окончания нормы не может быть раньше даты начала.")
        return cleaned


class ProgressRecordForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ProgressRecord
        fields = ["record_date", "weight", "waist", "chest", "hips", "comment"]
        widgets = {"record_date": DateInput()}


class TrainerCommentForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TrainerComment
        fields = ["related_type", "text"]
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}


class ClubReviewForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = ClubReview
        fields = ["rating", "title", "text"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: внимательные тренеры и чистый зал"}),
            "text": forms.Textarea(attrs={"rows": 5, "placeholder": "Расскажите, что понравилось в клубе и тренировках."}),
        }

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean_text(self):
        text = self.cleaned_data["text"].strip()
        if len(text) < 10:
            raise ValidationError("Отзыв должен быть подробнее: минимум 10 символов.")
        return text


class TrainerReviewForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TrainerReview
        fields = ["trainer", "rating", "title", "text"]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Например: внимательный подход и понятная программа"}),
            "text": forms.Textarea(attrs={"rows": 4, "placeholder": "Расскажите, что понравилось в работе тренера и что можно улучшить."}),
        }

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["trainer"].queryset = TrainerProfile.objects.filter(status="active")
        if client and client.trainer_id and not self.initial.get("trainer") and not self.data:
            self.initial["trainer"] = client.trainer_id

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean_text(self):
        text = self.cleaned_data["text"].strip()
        if len(text) < 10:
            raise ValidationError("Отзыв о тренере должен быть подробнее: минимум 10 символов.")
        return text
