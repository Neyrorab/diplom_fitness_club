from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    return mapping.get(key)


@register.filter
def status_class(value):
    mapping = {
        "active": "status-good",
        "expired": "status-bad",
        "planned": "status-warn",
        "frozen": "status-warn",
        "paused": "status-warn",
        "archived": "status-muted",
        "draft": "status-muted",
        "completed": "status-good",
    }
    return mapping.get(value, "status-muted")


@register.filter
def exercise_type_class(value):
    normalized = str(value or "").strip().lower()
    mapping = {
        "силовое": "thumb-strength",
        "кардио": "thumb-cardio",
        "функциональное": "thumb-mobility",
        "функционал": "thumb-mobility",
    }
    return mapping.get(normalized, "thumb-other")


@register.filter
def product_macro_class(product):
    if not product:
        return "icon-other"

    protein = float(product.protein_per_100g or 0) * 4
    fat = float(product.fat_per_100g or 0) * 9
    carbs = float(product.carbs_per_100g or 0) * 4
    macros = {
        "icon-protein": protein,
        "icon-fats": fat,
        "icon-carbs": carbs,
    }
    total = sum(macros.values())
    if total <= 0:
        return "icon-other"

    macro_class, macro_value = max(macros.items(), key=lambda item: item[1])
    if macro_value / total < 0.45:
        return "icon-other"
    return macro_class


@register.filter
def initials(value):
    if not value:
        return "FC"
    parts = [part for part in str(value).replace("-", " ").split() if part]
    if not parts:
        return "FC"
    letters = [part[0] for part in parts[:2]]
    return "".join(letters).upper()


@register.filter
def avatar_variant(value):
    if not value:
        return "avatar-v1"
    score = sum(ord(char) for char in str(value))
    return f"avatar-v{score % 6 + 1}"
