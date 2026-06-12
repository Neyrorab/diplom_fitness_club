import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone


ZAI_CHAT_COMPLETIONS_URL = "https://api.z.ai/api/paas/v4/chat/completions"
OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
GPTUNNEL_CHAT_COMPLETIONS_URL = "https://gptunnel.ru/v1/chat/completions"
DEFAULT_ZAI_MODELS = {"glm-5.1", "glm-5", "glm-5-turbo", "glm-4.7", "glm-4.7-flash", "glm-4.5-flash"}
AI_PROVIDER_LABELS = {
    "gptunnel": "GPTunnel",
    "openrouter": "OpenRouter",
    "zai": "Z.ai",
}
MANAGEMENT_SYSTEM_PROMPT = """
Ты senior business analyst фитнес-центра. Твоя задача - не пересказать метрики, а дать руководителю
структурированный план управления клубом на основе данных.

Отвечай только валидным JSON без markdown, без ``` и без поясняющего текста вокруг JSON.
Не оставляй ключевые поля пустыми. Даже если данных мало, сделай осторожные выводы из доступных метрик.

Строгая структура ответа:
{
  "summary": "4-6 предложений: что сейчас происходит с клубом, главные управленческие выводы",
  "health_score": 0-100,
  "priority_actions": [
    {
      "priority": "high|medium|low",
      "title": "короткое название действия",
      "metric": "короткая метрика на русском, не длиннее 24 символов",
      "action": "что именно сделать администратору или тренеру",
      "expected_effect": "какой результат ожидается",
      "owner": "администратор|тренеры|отдел продаж",
      "deadline": "срок в формате ДД.ММ.ГГГГ"
    }
  ],
  "risks": [
    {
      "title": "название риска",
      "detail": "почему это риск",
      "signal": "какая метрика сигнализирует о риске",
      "mitigation": "как снизить риск"
    }
  ],
  "trainer_actions": [
    {
      "trainer": "имя тренера или группа тренеров",
      "focus": "управленческий фокус",
      "action": "что сделать"
    }
  ],
  "growth_actions": [
    {
      "title": "идея роста",
      "action": "что сделать",
      "metric": "как измерить эффект"
    }
  ],
  "next_7_days": [
    "конкретный шаг на ближайшую неделю"
  ]
}

Верни 3-5 priority_actions, 2-4 risks, 2-4 trainer_actions, 2-3 growth_actions и 5-7 next_7_days.
Даты deadline должны быть не раньше текущей даты, которую пользователь передаст в сообщении.
Не используй английские технические имена метрик вроде risk_clients_count в поле metric; переводи их на русский кратко.
"""

TRAINER_SYSTEM_PROMPT = """
Ты ИИ-ассистент тренера фитнес-клуба. Твоя задача - превратить данные по закрепленным клиентам в рабочий план тренера:
кому написать сегодня, у кого падает регулярность, кому нужно изменить тренировочный план, как подготовиться к ближайшим
тренировкам, какие свободные окна расписания предложить клиентам и как помочь клиенту дойти до продления абонемента
через качество тренерской работы.

Отвечай только валидным JSON без markdown, без ``` и без поясняющего текста вокруг JSON.
Не придумывай клиентов, тренировки, травмы, цели и абонементы вне переданных данных. Если данных мало, сделай осторожный вывод.

Строгая структура ответа:
{
  "summary": "4-6 предложений: состояние клиентской группы тренера и главный фокус на неделю",
  "focus_score": 0-100,
  "priority_clients": [
    {
      "priority": "high|medium|low",
      "client": "имя клиента из данных",
      "scenario": "контакт|план|активность|сообщение|тренировка|продление",
      "title": "короткое название задачи",
      "reason": "почему клиент попал в фокус",
      "recommended_action": "что именно сделать тренеру",
      "message_draft": "готовый короткий текст сообщения клиенту от лица тренера",
      "business_effect": "как это помогает удержанию, продлению или качеству сервиса",
      "deadline": "срок в формате ДД.ММ.ГГГГ",
      "evidence": "какой сигнал или метрика это подтверждает"
    }
  ],
  "plan_adjustments": [
    {
      "client": "имя клиента",
      "current_signal": "что видно по данным",
      "adjustment": "как скорректировать план",
      "why": "зачем это делать"
    }
  ],
  "communication_scripts": [
    {
      "client": "имя клиента или группа клиентов",
      "goal": "цель сообщения",
      "message": "готовый текст сообщения"
    }
  ],
  "upcoming_workouts": [
    {
      "client": "имя клиента",
      "preparation": "что проверить до ближайшей тренировки",
      "watch_out": "на что обратить внимание во время тренировки"
    }
  ],
  "renewal_support": [
    {
      "client": "имя клиента",
      "action": "как тренер может поддержать продление",
      "signal": "какой абонементный или поведенческий сигнал это подтверждает"
    }
  ],
  "next_7_days": [
    "конкретный шаг тренера на ближайшую неделю"
  ]
}

Верни 3-6 priority_clients, 2-4 plan_adjustments, 2-4 communication_scripts, 2-4 upcoming_workouts,
0-4 renewal_support и 5-7 next_7_days. Сроки deadline не должны быть раньше текущей даты из сообщения пользователя.
"""

CLIENT_SYSTEM_PROMPT = """
Ты персональный ИИ-коуч клиента фитнес-клуба. Дай короткий недельный план только по переданным данным.
Отвечай валидным JSON без markdown. Не выдумывай факты и не давай медицинских назначений.
Если есть ограничения здоровья, предложи согласовать нагрузку с тренером или врачом.

Схема:
{
  "summary": "2-3 предложения",
  "readiness_score": 0-100,
  "priority_steps": [
    {
      "priority": "high|medium|low",
      "category": "тренировки|питание|прогресс|абонемент|запись|общее",
      "title": "коротко",
      "reason": "почему важно",
      "action": "что сделать",
      "deadline": "ДД.ММ.ГГГГ",
      "evidence": "сигнал из данных"
    }
  ],
  "workout_focus": [{"title": "коротко", "action": "что сделать", "why": "зачем"}],
  "nutrition_focus": [{"title": "коротко", "action": "что сделать", "why": "зачем"}],
  "progress_focus": [{"title": "коротко", "action": "что сделать", "why": "зачем"}],
  "questions_for_trainer": ["вопрос тренеру"],
  "next_7_days": ["шаг на неделю"]
}

Верни 2-3 priority_steps, по 1 workout_focus, nutrition_focus и progress_focus,
1-2 questions_for_trainer и ровно 3 next_7_days. Сроки deadline не должны быть раньше текущей даты из сообщения пользователя.
"""

WEIGHT_FORECAST_SYSTEM_PROMPT = """
Ты аналитик прогресса фитнес-клуба. Твоя задача - построить осторожный прогноз динамики веса клиента на основе
истории замеров, цели, тренировочной активности и контекста питания.

Отвечай только валидным JSON без markdown, без ``` и без поясняющего текста вокруг JSON.
Не давай медицинских диагнозов и не обещай точный результат. Прогноз должен быть вероятностным и консервативным.
Если данных немного, расширяй коридор неопределенности и прямо укажи это в summary/risks.

Строгая структура ответа:
{
  "summary": "3-5 предложений: что показывает текущий тренд и насколько надежен прогноз",
  "confidence": "low|medium|high",
  "horizon_days": 56,
  "trend_label": "снижение|рост|стабилизация|нестабильно",
  "points": [
    {
      "date": "YYYY-MM-DD",
      "probable": 78.4,
      "lower": 77.6,
      "upper": 79.3
    }
  ],
  "assumptions": [
    "какое условие должно сохраниться, чтобы прогноз был релевантен"
  ],
  "risks": [
    "что может сдвинуть прогноз вверх или вниз"
  ],
  "recommendations": [
    "что клиенту или тренеру сделать, чтобы прогноз стал надежнее"
  ]
}

Верни ровно expected_points точек из forecast_request, даты должны идти с шагом step_days после последней даты history.
Числа веса округляй до 0.1 кг. Для каждой точки lower <= probable <= upper.
"""


class ManagementAIError(Exception):
    pass


class EmptyAIContentError(ManagementAIError):
    pass


def compact_json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def get_management_ai_recommendations(metrics_payload, provider=None, model=None):
    provider = normalize_ai_provider(provider or getattr(settings, "AI_PROVIDER", "gptunnel"))
    if provider == "gptunnel":
        return get_gptunnel_recommendations(metrics_payload, model=model)
    if provider == "openrouter":
        return get_openrouter_recommendations(metrics_payload, model=model)
    if provider == "zai":
        return get_zai_recommendations(metrics_payload, model=model)
    raise ManagementAIError("Неизвестный AI_PROVIDER. Используйте gptunnel, openrouter или zai.")


def get_trainer_ai_recommendations(metrics_payload, provider=None, model=None):
    provider = normalize_ai_provider(provider or getattr(settings, "AI_PROVIDER", "gptunnel"))
    if provider == "gptunnel":
        return get_gptunnel_trainer_recommendations(metrics_payload, model=model)
    if provider == "openrouter":
        return get_openrouter_trainer_recommendations(metrics_payload, model=model)
    if provider == "zai":
        return get_zai_trainer_recommendations(metrics_payload, model=model)
    raise ManagementAIError("Неизвестный AI_PROVIDER. Используйте gptunnel, openrouter или zai.")


def get_client_ai_recommendations(metrics_payload, provider=None, model=None):
    provider = normalize_ai_provider(provider or getattr(settings, "AI_PROVIDER", "gptunnel"))
    if provider == "gptunnel":
        return get_gptunnel_client_recommendations(metrics_payload, model=model)
    if provider == "openrouter":
        return get_openrouter_client_recommendations(metrics_payload, model=model)
    if provider == "zai":
        return get_zai_client_recommendations(metrics_payload, model=model)
    raise ManagementAIError("Неизвестный AI_PROVIDER. Используйте gptunnel, openrouter или zai.")


def get_weight_forecast(metrics_payload, provider=None, model=None):
    provider = normalize_ai_provider(provider or getattr(settings, "AI_PROVIDER", "gptunnel"))
    if provider == "gptunnel":
        return get_gptunnel_weight_forecast(metrics_payload, model=model)
    if provider == "openrouter":
        return get_openrouter_weight_forecast(metrics_payload, model=model)
    if provider == "zai":
        return get_zai_weight_forecast(metrics_payload, model=model)
    raise ManagementAIError("Неизвестный AI_PROVIDER. Используйте gptunnel, openrouter или zai.")


def normalize_ai_provider(value):
    value = (value or "").strip().lower()
    aliases = {
        "gptunnel": "gptunnel",
        "openrouter": "openrouter",
        "open router": "openrouter",
        "zai": "zai",
        "z.ai": "zai",
    }
    return aliases.get(value, value)


def ai_provider_label(value):
    return AI_PROVIDER_LABELS.get(normalize_ai_provider(value), value or "GPTunnel")


def get_gptunnel_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "GPTUNNEL_API_KEY", "")
    models = gptunnel_model_chain(model)

    if not api_key:
        raise ManagementAIError("Укажите API-ключ GPTunnel в переменной GPTUNNEL_API_KEY или в файле .env и перезапустите сервер.")

    last_error = None
    for model in models:
        request = urllib.request.Request(
            GPTUNNEL_CHAT_COMPLETIONS_URL,
            data=json.dumps(build_chat_request_body(metrics_payload, model)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )
        try:
            response_body = send_chat_request(request, "GPTunnel", timeout=75)
            content = extract_chat_content(response_body, "GPTunnel")
            return parse_ai_recommendation(content, response_body.get("model") or model, metrics_payload)
        except ManagementAIError as error:
            last_error = error

    if len(models) > 1:
        raise ManagementAIError(
            f"GPTunnel не завершил анализ через модели {', '.join(models)}. Последняя ошибка: {last_error}"
        ) from last_error
    raise last_error


def get_gptunnel_trainer_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "GPTUNNEL_API_KEY", "")
    models = gptunnel_model_chain(model)

    if not api_key:
        raise ManagementAIError("Укажите API-ключ GPTunnel в переменной GPTUNNEL_API_KEY или в файле .env и перезапустите сервер.")

    last_error = None
    for model in models:
        request = urllib.request.Request(
            GPTUNNEL_CHAT_COMPLETIONS_URL,
            data=json.dumps(build_trainer_chat_request_body(metrics_payload, model)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )
        try:
            response_body = send_chat_request(request, "GPTunnel", timeout=75)
            content = extract_chat_content(response_body, "GPTunnel")
            return parse_trainer_ai_recommendation(content, response_body.get("model") or model, metrics_payload)
        except ManagementAIError as error:
            last_error = error

    if len(models) > 1:
        raise ManagementAIError(
            f"GPTunnel не завершил тренерский анализ через модели {', '.join(models)}. Последняя ошибка: {last_error}"
        ) from last_error
    raise last_error


def get_gptunnel_client_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "GPTUNNEL_API_KEY", "")
    models = gptunnel_model_chain(model)

    if not api_key:
        raise ManagementAIError("Укажите API-ключ GPTunnel в переменной GPTUNNEL_API_KEY или в файле .env и перезапустите сервер.")

    last_error = None
    for model in models:
        request_body = build_client_chat_request_body(metrics_payload, model)
        request_body["response_format"] = {"type": "json_object"}
        request_body["max_tokens"] = max(request_body.get("max_tokens", 0), 3000)
        request = urllib.request.Request(
            GPTUNNEL_CHAT_COMPLETIONS_URL,
            data=compact_json_bytes(request_body),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )
        try:
            response_body = send_chat_request(request, "GPTunnel", timeout=75)
            content = extract_chat_content(response_body, "GPTunnel")
            return parse_client_ai_recommendation(content, response_body.get("model") or model, metrics_payload)
        except ManagementAIError as error:
            last_error = error

    if len(models) > 1:
        raise ManagementAIError(
            f"GPTunnel не завершил клиентский анализ через модели {', '.join(models)}. Последняя ошибка: {last_error}"
        ) from last_error
    raise last_error


def get_gptunnel_weight_forecast(metrics_payload, model=None):
    api_key = getattr(settings, "GPTUNNEL_API_KEY", "")
    models = gptunnel_model_chain(model)

    if not api_key:
        raise ManagementAIError("Укажите API-ключ GPTunnel в переменной GPTUNNEL_API_KEY или в файле .env и перезапустите сервер.")

    last_error = None
    for model in models:
        request = urllib.request.Request(
            GPTUNNEL_CHAT_COMPLETIONS_URL,
            data=json.dumps(build_weight_forecast_chat_request_body(metrics_payload, model)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": api_key,
            },
            method="POST",
        )
        try:
            response_body = send_chat_request(request, "GPTunnel", timeout=75)
            content = extract_chat_content(response_body, "GPTunnel")
            return parse_weight_forecast(content, response_body.get("model") or model, metrics_payload)
        except ManagementAIError as error:
            last_error = error

    if len(models) > 1:
        raise ManagementAIError(
            f"GPTunnel не построил прогноз веса через модели {', '.join(models)}. Последняя ошибка: {last_error}"
        ) from last_error
    raise last_error


def gptunnel_model_chain(primary=None):
    primary = primary or getattr(settings, "GPTUNNEL_API_MODEL", "claude-4.6-sonnet")
    fallback_raw = getattr(settings, "GPTUNNEL_FALLBACK_MODELS", "")
    return configured_model_chain(primary, fallback_raw, "claude-4.6-sonnet")


def get_openrouter_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    models = openrouter_model_chain(model)
    model = models[0]

    if not api_key:
        raise ManagementAIError("Укажите API-ключ OpenRouter в переменной OPENROUTER_API_KEY или в файле .env и перезапустите сервер.")

    request_body = build_chat_request_body(metrics_payload, model)
    request_body["models"] = models
    request_body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=compact_json_bytes(request_body),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
            "X-OpenRouter-Title": getattr(settings, "OPENROUTER_APP_TITLE", "Fitness Club Diploma"),
        },
        method="POST",
    )

    response_body = send_chat_request(request, "OpenRouter", timeout=75)
    content = extract_chat_content(response_body, "OpenRouter")
    return parse_ai_recommendation(content, response_body.get("model") or model, metrics_payload)


def get_openrouter_trainer_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    models = openrouter_model_chain(model)
    model = models[0]

    if not api_key:
        raise ManagementAIError("Укажите API-ключ OpenRouter в переменной OPENROUTER_API_KEY или в файле .env и перезапустите сервер.")

    request_body = build_trainer_chat_request_body(metrics_payload, model)
    request_body["models"] = models
    request_body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
            "X-OpenRouter-Title": getattr(settings, "OPENROUTER_APP_TITLE", "Fitness Club Diploma"),
        },
        method="POST",
    )

    response_body = send_chat_request(request, "OpenRouter", timeout=75)
    content = extract_chat_content(response_body, "OpenRouter")
    return parse_trainer_ai_recommendation(content, response_body.get("model") or model, metrics_payload)


def get_openrouter_client_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    models = openrouter_model_chain(model)
    model = models[0]

    if not api_key:
        raise ManagementAIError("Укажите API-ключ OpenRouter в переменной OPENROUTER_API_KEY или в файле .env и перезапустите сервер.")

    request_body = build_client_chat_request_body(metrics_payload, model)
    request_body["models"] = models
    request_body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
            "X-OpenRouter-Title": getattr(settings, "OPENROUTER_APP_TITLE", "Fitness Club Diploma"),
        },
        method="POST",
    )

    response_body = send_chat_request(request, "OpenRouter", timeout=75)
    content = extract_chat_content(response_body, "OpenRouter")
    return parse_client_ai_recommendation(content, response_body.get("model") or model, metrics_payload)


def get_openrouter_weight_forecast(metrics_payload, model=None):
    api_key = getattr(settings, "OPENROUTER_API_KEY", "")
    models = openrouter_model_chain(model)
    model = models[0]

    if not api_key:
        raise ManagementAIError("Укажите API-ключ OpenRouter в переменной OPENROUTER_API_KEY или в файле .env и перезапустите сервер.")

    request_body = build_weight_forecast_chat_request_body(metrics_payload, model)
    request_body["models"] = models
    request_body["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENROUTER_CHAT_COMPLETIONS_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
            "X-OpenRouter-Title": getattr(settings, "OPENROUTER_APP_TITLE", "Fitness Club Diploma"),
        },
        method="POST",
    )

    try:
        response_body = send_chat_request(request, "OpenRouter", timeout=75)
    except ManagementAIError as error:
        if not is_transient_ai_connection_error(error):
            raise
        try:
            response_body = send_chat_request(request, "OpenRouter", timeout=75)
        except ManagementAIError as retry_error:
            if is_transient_ai_connection_error(retry_error):
                return build_provider_weight_forecast_fallback(metrics_payload, model, "OpenRouter", retry_error)
            raise
    try:
        content = extract_chat_content(response_body, "OpenRouter")
    except EmptyAIContentError as first_error:
        retry_body = build_weight_forecast_chat_request_body(metrics_payload, model)
        retry_body["models"] = models
        retry_request = urllib.request.Request(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            data=json.dumps(retry_body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": getattr(settings, "OPENROUTER_SITE_URL", "http://127.0.0.1:8000"),
                "X-OpenRouter-Title": getattr(settings, "OPENROUTER_APP_TITLE", "Fitness Club Diploma"),
            },
            method="POST",
        )
        try:
            retry_response_body = send_chat_request(retry_request, "OpenRouter", timeout=75)
        except ManagementAIError as retry_error:
            if is_transient_ai_connection_error(retry_error):
                return build_provider_weight_forecast_fallback(metrics_payload, response_body.get("model") or model, "OpenRouter", retry_error)
            raise
        try:
            content = extract_chat_content(retry_response_body, "OpenRouter")
            response_body = retry_response_body
        except EmptyAIContentError:
            fallback = build_local_weight_forecast(metrics_payload, response_body.get("model") or model)
            fallback["summary"] = (
                "OpenRouter вернул пустой ответ без текста прогноза даже после повторного запроса без response_format. "
                "Показан локальный осторожный расчет по истории веса; для ИИ-версии повторите запрос другой моделью или агрегатором. "
                f"Первый ответ: {first_error}"
            )
            fallback["provider_warning"] = str(first_error)
            return fallback
    return parse_weight_forecast(content, response_body.get("model") or model, metrics_payload)


def openrouter_model_chain(primary=None):
    primary = primary or getattr(settings, "OPENROUTER_API_MODEL", "anthropic/claude-sonnet-4.6")
    fallback_raw = getattr(settings, "OPENROUTER_FALLBACK_MODELS", "")
    return configured_model_chain(primary, fallback_raw, "openrouter/free")


def configured_model_chain(primary, fallback_raw, default_model):
    models = [primary]
    models.extend(item.strip() for item in fallback_raw.split(",") if item.strip())
    result = []
    for model in models:
        if model not in result:
            result.append(model)
    return (result or [default_model])[:3]


def allowed_zai_models():
    configured = getattr(settings, "ZAI_ALLOWED_MODELS", None)
    if not configured:
        return DEFAULT_ZAI_MODELS
    return {str(model).strip().lower() for model in configured if str(model).strip()}


def ensure_zai_model_allowed(model, feature_name):
    models = allowed_zai_models()
    if model not in models:
        allowed = ", ".join(sorted(models))
        raise ManagementAIError(f"Для {feature_name} выбрана неподдерживаемая модель Z.ai: {model}. Разрешены: {allowed}.")


def get_zai_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "ZAI_API_KEY", "")
    model = (model or getattr(settings, "ZAI_API_MODEL", "glm-5")).lower()

    if not api_key:
        raise ManagementAIError("Укажите API-ключ в переменной окружения ZAI_API_KEY и перезапустите сервер.")
    ensure_zai_model_allowed(model, "ИИ-анализа")

    request_body = build_chat_request_body(metrics_payload, model)

    request = urllib.request.Request(
        ZAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    response_body = send_chat_request(request, "Z.ai", timeout=45)
    content = extract_chat_content(response_body, "Z.ai")
    return parse_ai_recommendation(content, model, metrics_payload)


def get_zai_trainer_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "ZAI_API_KEY", "")
    model = (model or getattr(settings, "ZAI_API_MODEL", "glm-5")).lower()

    if not api_key:
        raise ManagementAIError("Укажите API-ключ в переменной окружения ZAI_API_KEY и перезапустите сервер.")
    ensure_zai_model_allowed(model, "ИИ-ассистента тренера")

    request = urllib.request.Request(
        ZAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(build_trainer_chat_request_body(metrics_payload, model)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    response_body = send_chat_request(request, "Z.ai", timeout=45)
    content = extract_chat_content(response_body, "Z.ai")
    return parse_trainer_ai_recommendation(content, model, metrics_payload)


def get_zai_client_recommendations(metrics_payload, model=None):
    api_key = getattr(settings, "ZAI_API_KEY", "")
    model = (model or getattr(settings, "ZAI_API_MODEL", "glm-5")).lower()

    if not api_key:
        raise ManagementAIError("Укажите API-ключ в переменной окружения ZAI_API_KEY и перезапустите сервер.")
    ensure_zai_model_allowed(model, "ИИ-коуча клиента")

    request = urllib.request.Request(
        ZAI_CHAT_COMPLETIONS_URL,
        data=compact_json_bytes(build_client_chat_request_body(metrics_payload, model)),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    response_body = send_chat_request(request, "Z.ai", timeout=45)
    content = extract_chat_content(response_body, "Z.ai")
    return parse_client_ai_recommendation(content, model, metrics_payload)


def get_zai_weight_forecast(metrics_payload, model=None):
    api_key = getattr(settings, "ZAI_API_KEY", "")
    model = (model or getattr(settings, "ZAI_API_MODEL", "glm-5")).lower()

    if not api_key:
        raise ManagementAIError("Укажите API-ключ в переменной окружения ZAI_API_KEY и перезапустите сервер.")
    ensure_zai_model_allowed(model, "ИИ-прогноза веса")

    request = urllib.request.Request(
        ZAI_CHAT_COMPLETIONS_URL,
        data=json.dumps(build_weight_forecast_chat_request_body(metrics_payload, model)).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    response_body = send_chat_request(request, "Z.ai", timeout=45)
    content = extract_chat_content(response_body, "Z.ai")
    return parse_weight_forecast(content, model, metrics_payload)


def build_chat_request_body(metrics_payload, model):
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": MANAGEMENT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"Текущая дата: {today:%d.%m.%Y}. Все сроки deadline должны быть в диапазоне "
                    f"с {today:%d.%m.%Y} по {week_end:%d.%m.%Y} или позже, но не в прошлом. "
                    "В поле metric используй короткие русские подписи до 24 символов.\n"
                    "Проанализируй эти метрики администратора фитнес-клуба и подготовь развернутые, "
                    "но конкретные управленческие рекомендации:\n"
                    f"{json.dumps(metrics_payload, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.25,
        "max_tokens": 4200,
    }


def build_trainer_chat_request_body(metrics_payload, model):
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": TRAINER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"Текущая дата: {today:%d.%m.%Y}. Все deadline должны быть не раньше {today:%d.%m.%Y} "
                    f"и желательно попадать в рабочий горизонт до {week_end:%d.%m.%Y}. "
                    "Проанализируй только клиентов этого тренера. Для каждого действия укажи evidence из переданных сигналов.\n"
                    "Данные тренера и клиентов:\n"
                    f"{json.dumps(metrics_payload, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.25,
        "max_tokens": 4800,
    }


def build_client_chat_request_body(metrics_payload, model):
    today = timezone.localdate()
    week_end = today + timedelta(days=7)
    payload_json = json.dumps(metrics_payload, ensure_ascii=False, separators=(",", ":"))
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": CLIENT_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"Дата: {today:%d.%m.%Y}. Deadline не раньше {today:%d.%m.%Y}, "
                    f"горизонт до {week_end:%d.%m.%Y}. Только текущий клиент; evidence бери из JSON.\n"
                    "Данные:\n"
                    f"{payload_json}"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 1800,
    }


def build_weight_forecast_chat_request_body(metrics_payload, model):
    today = timezone.localdate()
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": WEIGHT_FORECAST_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"Текущая дата: {today:%d.%m.%Y}. Построй прогноз веса только по переданным данным. "
                    "Не добавляй медицинских назначений. Если тренд нестабилен, расширь lower/upper и объясни почему.\n"
                    "Данные клиента и история веса:\n"
                    f"{json.dumps(metrics_payload, ensure_ascii=False)}"
                ),
            },
        ],
        "temperature": 0.18,
        "max_tokens": 3000,
    }


def send_chat_request(request, provider_name, timeout):
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = json.loads(response.read().decode("utf-8"))
            error_detail = response_error_detail(response_body)
            if error_detail:
                raise ManagementAIError(f"{provider_name} вернул ошибку: {error_detail}")
            return response_body
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise ManagementAIError(f"{provider_name} вернул ошибку {error.code}: {detail[:400]}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        if is_socket_permission_error(error):
            raise ManagementAIError(
                f"Не удалось связаться с {provider_name}: Windows запретил исходящее соединение Python "
                "(WinError 10013). Перезапустите Django-сервер из обычного PowerShell или разрешите "
                "python.exe в брандмауэре/антивирусе, затем повторите анализ."
            ) from error
        raise ManagementAIError(f"Не удалось связаться с {provider_name}: {error}") from error
    except json.JSONDecodeError as error:
        raise ManagementAIError(f"{provider_name} вернул ответ в неожиданном формате.") from error


def is_socket_permission_error(error):
    reason = getattr(error, "reason", error)
    winerror = getattr(reason, "winerror", None)
    errno = getattr(reason, "errno", None)
    return winerror == 10013 or errno == 10013 or "WinError 10013" in str(error)


def extract_chat_content(response_body, provider_name):
    error_detail = response_error_detail(response_body)
    if error_detail:
        raise ManagementAIError(f"{provider_name} вернул ошибку: {error_detail}")

    choices = response_body.get("choices") if isinstance(response_body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise EmptyAIContentError(f"В ответе {provider_name} нет массива choices.")

    choice = choices[0] or {}
    choice_error = response_error_detail(choice)
    if choice_error:
        raise ManagementAIError(f"{provider_name} вернул ошибку: {choice_error}")

    message = choice.get("message") or {}
    content = message.get("content")
    if content is None:
        content = message.get("text") or message.get("reasoning") or choice.get("text") or choice.get("content")
    if content is None and isinstance(choice.get("delta"), dict):
        content = choice["delta"].get("content") or choice["delta"].get("text")
    text = content_to_text(content)
    if text:
        return text

    finish_reason = choice.get("finish_reason") or choice.get("native_finish_reason") or "не указан"
    raise EmptyAIContentError(f"В ответе {provider_name} пустое поле content (finish_reason: {finish_reason}).")


def response_error_detail(response_body):
    if not isinstance(response_body, dict):
        return ""
    error = response_body.get("error")
    if not error:
        return ""
    if isinstance(error, str):
        return error[:500]
    if isinstance(error, dict):
        parts = [
            str(error.get("message") or error.get("detail") or error.get("code") or "ошибка"),
        ]
        metadata = error.get("metadata")
        if isinstance(metadata, dict):
            metadata_message = metadata.get("message") or metadata.get("raw") or metadata.get("reason")
            if metadata_message:
                parts.append(str(metadata_message))
        return " ".join(part for part in parts if part)[:500]
    return str(error)[:500]


def content_to_text(content):
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                nested = item.get("text") or item.get("content")
                text = content_to_text(nested)
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        return content_to_text(content.get("text") or content.get("content"))
    return ""


def parse_ai_recommendation(content, model, metrics_payload=None):
    parsed = parse_json_content(content)
    if parsed is None:
        if metrics_payload:
            return build_local_management_recommendations(metrics_payload, model)
        return {
            "model": model,
            "summary": content.strip(),
            "health_score": None,
            "priority_actions": [],
            "risks": [],
            "trainer_actions": [],
            "growth_actions": [],
            "next_7_days": [],
            "raw": True,
        }

    result = {
        "model": model,
        "summary": first_text(parsed, "summary", "вывод", "итог", "analysis", "recommendation"),
        "health_score": parsed.get("health_score") or parsed.get("score") or parsed.get("оценка"),
        "priority_actions": normalize_actions(first_list(parsed, "priority_actions", "actions", "recommendations", "приоритетные_действия")),
        "risks": normalize_risks(first_list(parsed, "risks", "риски")),
        "trainer_actions": normalize_trainer_actions(first_list(parsed, "trainer_actions", "тренеры", "trainer_recommendations")),
        "growth_actions": normalize_growth_actions(first_list(parsed, "growth_actions", "growth", "рост")),
        "next_7_days": normalize_steps(first_list(parsed, "next_7_days", "plan", "план_на_7_дней", "week_plan")),
        "raw": False,
    }
    if not result["summary"] and not result["priority_actions"] and metrics_payload:
        return build_local_management_recommendations(metrics_payload, model)
    if not result["summary"]:
        result["summary"] = content.strip()
    return result


def parse_trainer_ai_recommendation(content, model, metrics_payload=None):
    parsed = parse_json_content(content)
    if parsed is None:
        if metrics_payload:
            return build_local_trainer_recommendations(metrics_payload, model)
        return {
            "model": model,
            "summary": content.strip(),
            "focus_score": None,
            "priority_clients": [],
            "plan_adjustments": [],
            "communication_scripts": [],
            "upcoming_workouts": [],
            "renewal_support": [],
            "next_7_days": [],
            "raw": True,
        }

    result = {
        "model": model,
        "summary": first_text(parsed, "summary", "вывод", "итог", "analysis"),
        "focus_score": parsed.get("focus_score") or parsed.get("score") or parsed.get("health_score") or parsed.get("оценка"),
        "priority_clients": normalize_priority_clients(
            first_list(parsed, "priority_clients", "clients", "client_actions", "recommendations", "приоритетные_клиенты")
        ),
        "plan_adjustments": normalize_plan_adjustments(first_list(parsed, "plan_adjustments", "plan_actions", "планы")),
        "communication_scripts": normalize_communication_scripts(
            first_list(parsed, "communication_scripts", "messages", "scripts", "сообщения")
        ),
        "upcoming_workouts": normalize_upcoming_workouts(first_list(parsed, "upcoming_workouts", "workouts", "training_preparation")),
        "renewal_support": normalize_renewal_support(first_list(parsed, "renewal_support", "renewals", "продления")),
        "next_7_days": normalize_steps(first_list(parsed, "next_7_days", "plan", "week_plan", "план_на_7_дней")),
        "raw": False,
    }
    if not result["summary"] and not result["priority_clients"] and metrics_payload:
        return build_local_trainer_recommendations(metrics_payload, model)
    if not result["summary"]:
        result["summary"] = content.strip()
    return result


def parse_client_ai_recommendation(content, model, metrics_payload=None):
    parsed = parse_json_content(content)
    if parsed is None:
        if metrics_payload:
            return build_local_client_recommendations(metrics_payload, model)
        return {
            "model": model,
            "summary": content.strip(),
            "readiness_score": None,
            "priority_steps": [],
            "workout_focus": [],
            "nutrition_focus": [],
            "progress_focus": [],
            "questions_for_trainer": [],
            "next_7_days": [],
            "raw": True,
        }

    result = {
        "model": model,
        "summary": first_text(parsed, "summary", "вывод", "итог", "analysis"),
        "readiness_score": parsed.get("readiness_score") or parsed.get("score") or parsed.get("health_score") or parsed.get("оценка"),
        "priority_steps": normalize_client_priority_steps(
            first_list(parsed, "priority_steps", "steps", "actions", "recommendations", "приоритетные_шаги")
        ),
        "workout_focus": normalize_client_focus_items(first_list(parsed, "workout_focus", "workouts", "training", "тренировки")),
        "nutrition_focus": normalize_client_focus_items(first_list(parsed, "nutrition_focus", "nutrition", "питание")),
        "progress_focus": normalize_client_focus_items(first_list(parsed, "progress_focus", "progress", "прогресс")),
        "questions_for_trainer": normalize_steps(first_list(parsed, "questions_for_trainer", "questions", "вопросы_тренеру")),
        "next_7_days": normalize_steps(first_list(parsed, "next_7_days", "plan", "week_plan", "план_на_7_дней")),
        "raw": False,
    }
    if not result["summary"] and not result["priority_steps"] and metrics_payload:
        return build_local_client_recommendations(metrics_payload, model)
    if not result["summary"]:
        result["summary"] = content.strip()
    return result


def parse_weight_forecast(content, model, metrics_payload=None):
    parsed = parse_json_content(content)
    if parsed is None:
        if metrics_payload:
            return build_local_weight_forecast(metrics_payload, model)
        return {
            "model": model,
            "summary": content.strip(),
            "confidence": "low",
            "trend_label": "нестабильно",
            "points": [],
            "assumptions": [],
            "risks": [],
            "recommendations": [],
            "raw": True,
        }

    result = {
        "model": model,
        "summary": first_text(parsed, "summary", "вывод", "итог", "analysis"),
        "confidence": normalize_confidence(parsed.get("confidence") or parsed.get("уверенность")),
        "horizon_days": number_value(parsed.get("horizon_days")) or (metrics_payload or {}).get("forecast_request", {}).get("horizon_days"),
        "trend_label": first_text(parsed, "trend_label", "trend", "тренд") or "нестабильно",
        "points": normalize_forecast_points(
            first_list(parsed, "points", "forecast", "weekly_forecast", "прогноз"),
            metrics_payload,
        ),
        "assumptions": normalize_steps(first_list(parsed, "assumptions", "условия", "допущения")),
        "risks": normalize_steps(first_list(parsed, "risks", "риски")),
        "recommendations": normalize_steps(first_list(parsed, "recommendations", "actions", "рекомендации")),
        "raw": False,
    }
    expected_points = (metrics_payload or {}).get("forecast_request", {}).get("expected_points", 0)
    if not result["summary"] and metrics_payload:
        return build_local_weight_forecast(metrics_payload, model)
    minimum_points = min(expected_points, max(2, expected_points // 2)) if expected_points else 0
    if minimum_points and len(result["points"]) < minimum_points and metrics_payload:
        return build_local_weight_forecast(metrics_payload, model)
    if not result["summary"]:
        result["summary"] = content.strip()
    return result


def parse_json_content(content):
    text = (content or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def normalize_forecast_points(points, metrics_payload=None):
    result = []
    for item in points or []:
        if not isinstance(item, dict):
            continue
        probable = number_value(
            item.get("probable")
            or item.get("expected")
            or item.get("median")
            or item.get("weight")
            or item.get("вероятный")
        )
        lower = number_value(item.get("lower") or item.get("low") or item.get("min") or item.get("нижняя"))
        upper = number_value(item.get("upper") or item.get("high") or item.get("max") or item.get("верхняя"))
        if probable is None:
            continue
        if lower is None:
            lower = probable
        if upper is None:
            upper = probable
        lower, probable, upper = sorted([lower, probable, upper])
        date_value = normalize_forecast_date(item.get("date") or item.get("week") or item.get("дата"))
        if not date_value:
            continue
        result.append(
            {
                "date": date_value,
                "probable": round(probable, 1),
                "lower": round(lower, 1),
                "upper": round(upper, 1),
            }
        )

    result.sort(key=lambda point: point["date"])
    expected_points = (metrics_payload or {}).get("forecast_request", {}).get("expected_points")
    return result[:expected_points] if expected_points else result


def normalize_forecast_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return text[:10]


def normalize_confidence(value):
    value = str(value or "medium").strip().lower()
    aliases = {
        "low": "low",
        "низкая": "low",
        "низкий": "low",
        "medium": "medium",
        "средняя": "medium",
        "средний": "medium",
        "high": "high",
        "высокая": "high",
        "высокий": "high",
    }
    return aliases.get(value, "medium")


def number_value(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def is_transient_ai_connection_error(error):
    text = str(error).lower()
    fragments = (
        "не удалось связаться",
        "ssl",
        "unexpected_eof",
        "eof occurred",
        "urlopen error",
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
    )
    return any(fragment in text for fragment in fragments)


def build_provider_weight_forecast_fallback(metrics_payload, model, provider_name, error):
    fallback = build_local_weight_forecast(metrics_payload, model)
    fallback["summary"] = (
        f"{provider_name} временно не ответил на запрос прогноза веса. "
        "Показан локальный осторожный расчет по истории веса; для ИИ-версии повторите прогноз позже "
        "или выберите другой агрегатор."
    )
    fallback["provider_warning"] = str(error)
    return fallback


def build_local_weight_forecast(metrics_payload, model):
    history = metrics_payload.get("history") or []
    request = metrics_payload.get("forecast_request") or {}
    expected_points = request.get("expected_points") or 8
    step_days = request.get("step_days") or 7
    horizon_days = request.get("horizon_days") or expected_points * step_days
    stats = metrics_payload.get("history_stats") or {}
    latest = history[-1] if history else {}
    latest_weight = number_value(latest.get("weight")) or number_value(stats.get("latest_weight")) or 0
    weekly_delta = number_value(stats.get("weekly_delta_kg")) or 0
    weekly_delta = max(min(weekly_delta, 1.2), -1.2)
    last_date = normalize_forecast_date(latest.get("date")) or timezone.localdate().isoformat()
    last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
    noise = weight_history_noise(history)
    points = []
    for index in range(1, expected_points + 1):
        date = last_date + timedelta(days=step_days * index)
        probable = latest_weight + weekly_delta * index
        band = 0.35 + noise + index * 0.18
        points.append(
            {
                "date": date.isoformat(),
                "probable": round(probable, 1),
                "lower": round(probable - band, 1),
                "upper": round(probable + band, 1),
            }
        )

    trend_label = "стабилизация"
    if weekly_delta <= -0.15:
        trend_label = "снижение"
    elif weekly_delta >= 0.15:
        trend_label = "рост"

    client_name = (metrics_payload.get("client") or {}).get("name", "клиента")
    return {
        "model": model,
        "summary": (
            f"Для {client_name} построен осторожный прогноз по текущему тренду веса. "
            "Ответ модели был неструктурированным или пустым, поэтому показан локальный расчет с расширенным коридором. "
            "Используйте его как ориентир и обновляйте прогноз после новых замеров."
        ),
        "confidence": "low",
        "horizon_days": horizon_days,
        "trend_label": trend_label,
        "points": points,
        "assumptions": ["Сохраняются текущие регулярность тренировок, питание и частота замеров."],
        "risks": ["Разовые колебания воды, питания и пропуски замеров могут заметно сместить прогноз."],
        "recommendations": ["Добавляйте замеры 1-2 раза в неделю в одинаковых условиях, чтобы сузить коридор прогноза."],
        "raw": False,
        "source": "local_fallback",
    }


def weight_history_noise(history):
    weights = [number_value(item.get("weight")) for item in history if number_value(item.get("weight")) is not None]
    if len(weights) < 3:
        return 0.35
    deltas = [abs(weights[index] - weights[index - 1]) for index in range(1, len(weights))]
    return min(sum(deltas) / len(deltas) * 0.35, 0.8)


def first_text(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_list(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            return value
    return []


def normalize_actions(items):
    actions = []
    for item in normalized_list(items):
        if isinstance(item, str):
            actions.append({"priority": "medium", "title": item, "action": item})
            continue
        if not isinstance(item, dict):
            continue
        title = first_text(item, "title", "name", "название") or first_text(item, "action", "действие")[:80]
        action = first_text(item, "action", "detail", "description", "действие", "рекомендация")
        if title or action:
            actions.append(
                {
                    "priority": normalize_priority(first_text(item, "priority", "приоритет")),
                    "title": title or "Рекомендация",
                    "metric": humanize_metric(first_text(item, "metric", "signal", "метрика")),
                    "action": action or title,
                    "expected_effect": first_text(item, "expected_effect", "effect", "эффект"),
                    "owner": first_text(item, "owner", "responsible", "ответственный"),
                    "deadline": normalize_deadline(first_text(item, "deadline", "срок")),
                }
            )
    return actions


def normalize_priority_clients(items):
    actions = []
    for item in normalized_list(items):
        if isinstance(item, str):
            actions.append(
                {
                    "priority": "medium",
                    "client": "Клиенты тренера",
                    "scenario": "сообщение",
                    "title": item,
                    "reason": item,
                    "recommended_action": item,
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        action = first_text(item, "recommended_action", "action", "detail", "description", "рекомендация")
        title = first_text(item, "title", "name", "task", "название") or action[:80]
        if title or action:
            actions.append(
                {
                    "priority": normalize_priority(first_text(item, "priority", "приоритет")),
                    "client": first_text(item, "client", "name", "клиент") or "Клиент",
                    "scenario": first_text(item, "scenario", "category", "сценарий") or "фокус",
                    "title": title or "Рабочая задача",
                    "reason": first_text(item, "reason", "why", "signal", "причина"),
                    "recommended_action": action or title,
                    "message_draft": first_text(item, "message_draft", "message", "script", "сообщение"),
                    "business_effect": first_text(item, "business_effect", "effect", "эффект"),
                    "deadline": normalize_deadline(first_text(item, "deadline", "срок")),
                    "evidence": first_text(item, "evidence", "metric", "signal", "основание"),
                }
            )
    return actions


def normalize_client_priority_steps(items):
    steps = []
    for item in normalized_list(items):
        if isinstance(item, str):
            steps.append(
                {
                    "priority": "medium",
                    "category": "общее",
                    "title": item,
                    "reason": item,
                    "action": item,
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        action = first_text(item, "action", "recommended_action", "detail", "description", "действие", "рекомендация")
        title = first_text(item, "title", "name", "task", "название") or action[:80]
        if title or action:
            steps.append(
                {
                    "priority": normalize_priority(first_text(item, "priority", "приоритет")),
                    "category": first_text(item, "category", "scenario", "раздел", "категория") or "общее",
                    "title": title or "Следующий шаг",
                    "reason": first_text(item, "reason", "why", "signal", "причина"),
                    "action": action or title,
                    "deadline": normalize_deadline(first_text(item, "deadline", "срок")),
                    "evidence": first_text(item, "evidence", "metric", "signal", "основание"),
                }
            )
    return steps


def normalize_client_focus_items(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"title": item, "action": item, "why": ""})
            continue
        if isinstance(item, dict):
            action = first_text(item, "action", "recommendation", "действие")
            title = first_text(item, "title", "name", "название") or action[:80]
            why = first_text(item, "why", "reason", "effect", "зачем")
            if title or action:
                result.append(
                    {
                        "title": title or "Фокус",
                        "action": action or title,
                        "why": why,
                    }
                )
    return result


def normalize_plan_adjustments(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"client": "Клиенты тренера", "current_signal": item, "adjustment": item})
            continue
        if isinstance(item, dict):
            adjustment = first_text(item, "adjustment", "action", "recommendation", "корректировка")
            if adjustment:
                result.append(
                    {
                        "client": first_text(item, "client", "name", "клиент") or "Клиент",
                        "current_signal": first_text(item, "current_signal", "signal", "reason", "сигнал"),
                        "adjustment": adjustment,
                        "why": first_text(item, "why", "effect", "зачем"),
                    }
                )
    return result


def normalize_communication_scripts(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"client": "Клиенты тренера", "goal": "Контакт", "message": item})
            continue
        if isinstance(item, dict):
            message = first_text(item, "message", "text", "script", "сообщение")
            if message:
                result.append(
                    {
                        "client": first_text(item, "client", "name", "клиент") or "Клиент",
                        "goal": first_text(item, "goal", "purpose", "цель"),
                        "message": message,
                    }
                )
    return result


def normalize_upcoming_workouts(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"client": "Клиенты тренера", "preparation": item, "watch_out": ""})
            continue
        if isinstance(item, dict):
            preparation = first_text(item, "preparation", "action", "подготовка")
            watch_out = first_text(item, "watch_out", "focus", "risk", "контроль")
            if preparation or watch_out:
                result.append(
                    {
                        "client": first_text(item, "client", "name", "клиент") or "Клиент",
                        "preparation": preparation,
                        "watch_out": watch_out,
                    }
                )
    return result


def normalize_renewal_support(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"client": "Клиенты тренера", "action": item, "signal": ""})
            continue
        if isinstance(item, dict):
            action = first_text(item, "action", "recommendation", "действие")
            if action:
                result.append(
                    {
                        "client": first_text(item, "client", "name", "клиент") or "Клиент",
                        "action": action,
                        "signal": first_text(item, "signal", "metric", "evidence", "сигнал"),
                    }
                )
    return result


def normalize_risks(items):
    risks = []
    for item in normalized_list(items):
        if isinstance(item, str):
            risks.append({"title": item, "detail": item})
            continue
        if not isinstance(item, dict):
            continue
        title = first_text(item, "title", "name", "название")
        detail = first_text(item, "detail", "description", "risk", "описание")
        if title or detail:
            risks.append(
                {
                    "title": title or "Риск",
                    "detail": detail or title,
                    "signal": first_text(item, "signal", "metric", "метрика"),
                    "mitigation": first_text(item, "mitigation", "action", "решение"),
                }
            )
    return risks


def normalize_trainer_actions(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"trainer": "Команда тренеров", "focus": "Активность клиентов", "action": item})
            continue
        if isinstance(item, dict):
            action = first_text(item, "action", "действие", "recommendation")
            if action:
                result.append(
                    {
                        "trainer": first_text(item, "trainer", "name", "тренер") or "Команда тренеров",
                        "focus": first_text(item, "focus", "metric", "фокус"),
                        "action": action,
                    }
                )
    return result


def normalize_growth_actions(items):
    result = []
    for item in normalized_list(items):
        if isinstance(item, str):
            result.append({"title": item, "action": item})
            continue
        if isinstance(item, dict):
            action = first_text(item, "action", "действие", "description")
            title = first_text(item, "title", "name", "название")
            if action or title:
                result.append(
                    {
                        "title": title or "Идея роста",
                        "action": action or title,
                        "metric": first_text(item, "metric", "kpi", "метрика"),
                    }
                )
    return result


def normalize_steps(items):
    steps = []
    for item in normalized_list(items):
        if isinstance(item, str) and item.strip():
            steps.append(item.strip())
        elif isinstance(item, dict):
            text = first_text(item, "step", "action", "title", "шаг")
            if text:
                steps.append(text)
    return steps


def normalize_priority(value):
    value = (value or "").lower()
    if value in {"high", "высокий", "высокая"}:
        return "high"
    if value in {"low", "низкий", "низкая"}:
        return "low"
    return "medium"


def humanize_metric(value):
    value = (value or "").strip()
    mapping = {
        "risk_clients_count": "Клиенты в риске",
        "expiring_memberships_7_days": "Истекают абонементы",
        "low_activity_count": "Низкая активность",
        "weekly_activity.workouts": "Тренировки по неделям",
        "active_plan_average_completion_percent": "Выполнение планов",
        "average_workouts_per_client_30_days": "Тренировки на клиента",
    }
    value = mapping.get(value, value)
    if len(value) <= 28:
        return value
    return f"{value[:27].rstrip()}…"


def normalize_deadline(value):
    value = (value or "").strip()
    if not value:
        return value
    today = timezone.localdate()
    parsed = None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed = timezone.datetime.strptime(value, fmt).date()
            break
        except ValueError:
            continue
    if parsed and parsed < today:
        return (today + timedelta(days=7)).strftime("%d.%m.%Y")
    return value


def build_local_management_recommendations(metrics_payload, model):
    kpis = metrics_payload.get("business_kpis", {})
    risk_count = int(kpis.get("risk_clients_count") or 0)
    expiring = int(kpis.get("expiring_memberships_7_days") or 0)
    workouts_30 = int(kpis.get("workouts_30_days") or 0)
    avg_workouts = float(kpis.get("average_workouts_per_client_30_days") or 0)
    completion = float(kpis.get("active_plan_average_completion_percent") or 0)
    trainers = metrics_payload.get("trainer_load", [])
    overloaded = [row for row in trainers if row.get("low_activity_count")]

    actions = []
    if risk_count:
        actions.append(
            {
                "priority": "high",
                "title": "Вернуть клиентов из зоны риска",
                "metric": f"{risk_count} клиентов в зоне риска",
                "action": "Разделить список клиентов по тренерам и связаться с каждым: предложить удобное время тренировки, уточнить причину пропусков и зафиксировать следующий визит.",
                "expected_effect": "Снижение риска оттока и рост активности в ближайшие 7-14 дней.",
                "owner": "тренеры",
                "deadline": "2 дня",
            }
        )
    if expiring:
        actions.append(
            {
                "priority": "high",
                "title": "Продлить истекающие абонементы",
                "metric": f"{expiring} абонементов истекает за 7 дней",
                "action": "Подготовить персональные предложения продления и связать их с прогрессом клиента: новая цель, обновленный план и бонусная консультация.",
                "expected_effect": "Удержание выручки и меньше внезапных завершений абонементов.",
                "owner": "администратор",
                "deadline": "3 дня",
            }
        )
    if avg_workouts < 3:
        actions.append(
            {
                "priority": "medium",
                "title": "Поднять регулярность тренировок",
                "metric": f"{avg_workouts:.1f} тренировки на клиента за 30 дней",
                "action": "Запустить короткие напоминания и предложить клиентам фиксированные слоты на неделю вперед.",
                "expected_effect": "Рост посещаемости и больше данных для оценки прогресса.",
                "owner": "администратор и тренеры",
                "deadline": "7 дней",
            }
        )
    if completion < 60:
        actions.append(
            {
                "priority": "medium",
                "title": "Пересмотреть выполнение планов",
                "metric": f"{completion:.1f}% среднее выполнение активных программ",
                "action": "Проверить планы с низким процентом выполнения и упростить первые недели: меньше упражнений, понятные цели, контрольная точка после 2 тренировок.",
                "expected_effect": "Клиентам проще завершать план, тренерам проще управлять нагрузкой.",
                "owner": "тренеры",
                "deadline": "7 дней",
            }
        )
    if not actions:
        actions.append(
            {
                "priority": "low",
                "title": "Развивать продажи дополнительных услуг",
                "metric": f"{workouts_30} тренировок за 30 дней",
                "action": "Предложить активным клиентам диагностику состава тела, персональную тренировку или консультацию по питанию.",
                "expected_effect": "Рост среднего чека без давления на клиентов.",
                "owner": "администратор",
                "deadline": "7 дней",
            }
        )

    risks = []
    if risk_count:
        risks.append(
            {
                "title": "Отток клиентов",
                "detail": "Клиенты без свежих тренировок или с проблемным абонементом могут перестать посещать клуб.",
                "signal": f"{risk_count} клиентов в зоне риска",
                "mitigation": "Назначить ответственных тренеров и закрыть контакт по каждому клиенту.",
            }
        )
    if expiring:
        risks.append(
            {
                "title": "Потеря продлений",
                "detail": "Истекающие абонементы требуют контакта до даты окончания, иначе клиент может уйти без разговора.",
                "signal": f"{expiring} абонементов истекает за 7 дней",
                "mitigation": "Сформировать список продлений и предложить следующий тренировочный цикл.",
            }
        )
    if not risks:
        risks.append(
            {
                "title": "Недостаточно выраженных рисков",
                "detail": "Критичные сигналы не выделены, но стоит продолжать мониторинг активности и продлений.",
                "signal": "Стабильные ключевые метрики",
                "mitigation": "Проверять дашборд минимум 2 раза в неделю.",
            }
        )

    trainer_actions = [
        {
            "trainer": row.get("trainer", "Тренер"),
            "focus": f"{row.get('low_activity_count', 0)} клиентов с низкой активностью",
            "action": "Проверить клиентов с низкой активностью и назначить ближайшую тренировку или контрольный звонок.",
        }
        for row in overloaded[:4]
    ] or [
        {
            "trainer": "Команда тренеров",
            "focus": "Регулярность тренировок",
            "action": "Раз в неделю обновлять статусы клиентов и отмечать причины пропусков.",
        }
    ]

    next_7_days = [
        "День 1: выгрузить список клиентов в зоне риска и назначить ответственных тренеров.",
        "День 2: связаться с клиентами без тренировок за 14 дней.",
        "День 3: подготовить предложения для клиентов с истекающими абонементами.",
        "День 4: проверить планы с низким процентом выполнения.",
        "День 5: собрать обратную связь тренеров по причинам пропусков.",
        "День 7: повторно открыть дашборд и сравнить активность с текущей неделей.",
    ]

    score = max(20, min(95, 80 - risk_count * 4 - expiring * 3 + min(workouts_30, 30)))
    return {
        "model": f"{model} + локальная структура",
        "summary": (
            f"В клубе {kpis.get('total_clients', 0)} клиентов, за 30 дней зафиксировано {workouts_30} тренировок, "
            f"средняя регулярность составляет {avg_workouts:.1f} тренировки на клиента. Основной управленческий фокус - "
            f"{risk_count} клиентов в зоне риска и {expiring} абонементов, которые истекают в ближайшие 7 дней. "
            "Рекомендуется сначала закрыть удержание и регулярность, а затем переходить к росту продаж дополнительных услуг."
        ),
        "health_score": score,
        "priority_actions": actions,
        "risks": risks,
        "trainer_actions": trainer_actions,
        "growth_actions": [
            {
                "title": "Пакет продления с новой целью",
                "action": "При продлении абонемента предлагать обновление цели, новый план и контрольный замер.",
                "metric": "доля продленных абонементов",
            },
            {
                "title": "Реактивация неактивных клиентов",
                "action": "Запустить персональное сообщение с предложением удобного времени и короткой диагностики.",
                "metric": "возврат клиентов к тренировкам",
            },
        ],
        "next_7_days": next_7_days,
        "raw": False,
    }


def build_local_trainer_recommendations(metrics_payload, model):
    trainer = metrics_payload.get("trainer", {})
    clients = metrics_payload.get("clients", [])
    if not clients:
        return {
            "model": f"{model} + локальная структура",
            "summary": "У тренера пока нет закрепленных клиентов. ИИ-ассистент сможет сформировать рабочие сценарии после назначения клиентов и появления тренировочных данных.",
            "focus_score": None,
            "priority_clients": [],
            "plan_adjustments": [],
            "communication_scripts": [],
            "upcoming_workouts": [],
            "renewal_support": [],
            "next_7_days": ["Назначить тренеру клиентов.", "Создать активные тренировочные планы.", "Зафиксировать первые выполненные тренировки."],
            "raw": False,
        }

    high = int(trainer.get("high_signals") or 0)
    medium = int(trainer.get("medium_signals") or 0)
    low_activity = int(trainer.get("low_activity_clients") or 0)
    no_plan = int(trainer.get("clients_without_active_plan") or 0)
    expiring = int(trainer.get("expiring_memberships_7_days") or 0)
    focus_score = max(25, min(95, 88 - high * 7 - medium * 3 - low_activity * 4 - no_plan * 4 - expiring * 3))

    prioritized_rows = sorted(
        [row for row in clients if row.get("signals")],
        key=lambda row: (signal_rank(first_relevant_signal(row)), -len(row.get("signals", [])), row.get("name", "")),
    )
    priority_clients = []
    for row in prioritized_rows[:6]:
        signal = first_relevant_signal(row)
        if signal:
            priority_clients.append(local_priority_client(row, signal))

    plan_adjustments = [
        local_plan_adjustment(row, signal)
        for row in clients
        for signal in row.get("signals", [])
        if signal.get("id") in {"no_active_plan", "low_plan_completion", "health_limitations"}
    ][:4]
    if not plan_adjustments:
        plan_adjustments = [local_plan_maintenance(row) for row in clients if row.get("active_plan")][:2]

    communication_scripts = [local_communication_script(item) for item in priority_clients[:4]]
    upcoming_workouts = [local_upcoming_workout(row) for row in clients if (row.get("active_plan") or {}).get("next_workout")][:4]
    renewal_support = [
        local_renewal_support(row)
        for row in clients
        if row.get("membership", {}).get("expires_soon") or row.get("membership", {}).get("is_expired")
    ][:4]

    summary = (
        f"У тренера {trainer.get('name', '')} {trainer.get('clients_count', len(clients))} клиентов, "
        f"{trainer.get('active_clients_30_days', 0)} из них тренировались за последние 30 дней. "
        f"В фокусе {low_activity} клиентов с низкой активностью, {no_plan} клиентов без активного плана "
        f"и {expiring} абонементов, требующих поддержки продления. "
        "Главная задача тренера на неделю - закрыть персональные контакты, обновить планы с низким выполнением "
        "и заранее подготовить ближайшие тренировки с учетом целей и ограничений клиентов."
    )

    return {
        "model": f"{model} + локальная структура",
        "summary": summary,
        "focus_score": focus_score,
        "priority_clients": priority_clients,
        "plan_adjustments": plan_adjustments,
        "communication_scripts": communication_scripts,
        "upcoming_workouts": upcoming_workouts,
        "renewal_support": renewal_support,
        "next_7_days": local_trainer_week_plan(low_activity, no_plan, expiring),
        "raw": False,
    }


def build_local_client_recommendations(metrics_payload, model):
    client = metrics_payload.get("client", {})
    workouts = metrics_payload.get("workouts", {})
    nutrition = metrics_payload.get("nutrition", {})
    progress = metrics_payload.get("progress", {})
    membership = metrics_payload.get("membership", {})
    plan = metrics_payload.get("active_plan")
    schedule = metrics_payload.get("schedule", {})
    local = metrics_payload.get("local_recommendations", {})
    local_items = local.get("items", [])
    score = int(local.get("score") or 55)

    priority_steps = [local_client_priority_step(item) for item in local_items[:6]]
    if not priority_steps:
        priority_steps = [
            {
                "priority": "low",
                "category": "общее",
                "title": "Сохранить текущий режим",
                "reason": "Критичных отклонений по данным личного кабинета не видно.",
                "action": "Продолжайте отмечать тренировки, питание и прогресс, чтобы рекомендации оставались точными.",
                "deadline": client_deadline(7),
                "evidence": "Стабильные сигналы личного кабинета",
            }
        ]

    workout_focus = local_client_workout_focus(plan, workouts, schedule)
    nutrition_focus = local_client_nutrition_focus(nutrition, client)
    progress_focus = local_client_progress_focus(progress)
    questions = local_client_questions(client, plan, nutrition, progress, membership)
    next_7_days = local_client_week_plan(priority_steps, plan, nutrition, progress)
    client_name = client.get("name", "Клиент")
    workouts_30 = int(workouts.get("workouts_30_days") or 0)
    food_days = int(nutrition.get("days_with_food") or 0)
    plan_completion = (plan or {}).get("completion_percent") if plan else None

    summary = (
        f"{client_name}, основной фокус на неделю - регулярность, понятный ближайший шаг и обновление данных. "
        f"За последние 30 дней отмечено {workouts_30} тренировок, питание заполнено за {food_days} дней из 7. "
        f"{'Активный план выполнен примерно на ' + str(plan_completion) + '%.' if plan_completion is not None else 'Активного плана в данных нет.'} "
        "Начните с шагов высокого приоритета, затем закрепите режим через запись к тренеру, дневник питания и контроль прогресса."
    )

    return {
        "model": f"{model} + локальная структура",
        "summary": summary,
        "readiness_score": score,
        "priority_steps": priority_steps,
        "workout_focus": workout_focus,
        "nutrition_focus": nutrition_focus,
        "progress_focus": progress_focus,
        "questions_for_trainer": questions,
        "next_7_days": next_7_days,
        "raw": False,
    }


def local_client_priority_step(item):
    priority = item.get("priority", "medium")
    days = 2 if priority == "high" else 5 if priority == "medium" else 7
    return {
        "priority": priority,
        "category": item.get("category", "общее").lower(),
        "title": item.get("title", "Следующий шаг"),
        "reason": item.get("reason", ""),
        "action": item.get("action", item.get("title", "Проверить личный кабинет.")),
        "deadline": client_deadline(days),
        "evidence": item.get("reason", ""),
    }


def local_client_workout_focus(plan, workouts, schedule):
    focus = []
    days_since_last = workouts.get("days_since_last_workout")
    if not plan:
        focus.append(
            {
                "title": "Получить активный план",
                "action": "Напишите тренеру и попросите короткий план на ближайшие 2 недели.",
                "why": "Без понятного плана сложнее держать регулярность и отслеживать выполнение.",
            }
        )
    else:
        next_workout = plan.get("next_workout") or {}
        focus.append(
            {
                "title": next_workout.get("title") or "Ближайшая тренировка",
                "action": "Откройте текущий план и выполните следующий тренировочный день в умеренном темпе.",
                "why": f"Выполнение плана сейчас {plan.get('completion_percent', 0)}%, следующий шаг уже определен.",
            }
        )
    if days_since_last is None or days_since_last > 7:
        focus.append(
            {
                "title": "Вернуть регулярность",
                "action": "Запланируйте 1-2 тренировки на ближайшие 7 дней и начните с доступной нагрузки.",
                "why": "Пауза в тренировках снижает устойчивость результата.",
            }
        )
    if (schedule.get("next_appointment") or {}).get("start_at") == "нет":
        focus.append(
            {
                "title": "Записаться к тренеру",
                "action": "Откройте расписание и выберите ближайшее удобное окно у своего тренера.",
                "why": "Запись превращает намерение в конкретный следующий визит.",
            }
        )
    return focus[:3]


def local_client_nutrition_focus(nutrition, client):
    days = int(nutrition.get("days_with_food") or 0)
    target = nutrition.get("target")
    deviation = nutrition.get("deviation") or {}
    if days < 3:
        return [
            {
                "title": "Заполнить питание",
                "action": "Внесите основные приемы пищи хотя бы 5 дней подряд.",
                "why": "Без истории питания система и тренер не увидят, что мешает цели.",
            }
        ]
    if not target:
        return [
            {
                "title": "Уточнить норму питания",
                "action": "Попросите тренера задать норму калорий и БЖУ под вашу цель.",
                "why": "Так дневник питания станет не просто журналом, а инструментом контроля.",
            }
        ]
    if client.get("goal") == "Похудение" and deviation.get("calories", 0) > 150:
        return [
            {
                "title": "Снизить превышение калорий",
                "action": "Проверьте самый калорийный перекус или вечерний прием пищи и уменьшите его на 150-250 ккал.",
                "why": "Средняя калорийность выше заданной нормы.",
            }
        ]
    return [
        {
            "title": "Держать пищевой ритм",
            "action": "Продолжайте заполнять питание и следите, чтобы белок и калории были рядом с нормой.",
            "why": "Заполненные дни уже дают основу для точных корректировок.",
        }
    ]


def local_client_progress_focus(progress):
    days_since_update = progress.get("days_since_update")
    if days_since_update is None or days_since_update > 14:
        return [
            {
                "title": "Обновить замеры",
                "action": "Добавьте вес, талию, грудь и бедра в одинаковых условиях утром.",
                "why": "Свежие замеры помогают понять, работает ли план.",
            }
        ]
    return [
        {
            "title": "Сверить динамику",
            "action": "Сравните последние замеры с целью и обсудите с тренером, нужен ли новый акцент.",
            "why": "Прогресс полезнее оценивать по серии измерений, а не по одному дню.",
        }
    ]


def local_client_questions(client, plan, nutrition, progress, membership):
    questions = [
        "Какую частоту тренировок на этой неделе считать минимально достаточной для моей цели?",
        "Какие упражнения лучше оставить ключевыми, если времени на тренировку меньше обычного?",
    ]
    if plan:
        questions.append(f"Нужно ли корректировать план «{plan.get('title', 'текущий план')}» с учетом моего выполнения?")
    else:
        questions.append("Какой короткий план на 2 недели лучше начать сейчас?")
    if not nutrition.get("target"):
        questions.append("Какую норму калорий и БЖУ лучше поставить в дневнике питания?")
    if progress.get("weight_delta_total") is not None:
        questions.append("Соответствует ли моя динамика веса текущей цели?")
    if membership.get("expires_soon") or membership.get("is_expired"):
        questions.append("Какой следующий тренировочный цикл стоит запланировать перед продлением?")
    if client.get("health_limitations"):
        questions.append("Какие упражнения заменить или ограничить с учетом моих ограничений по здоровью?")
    return questions[:5]


def local_client_week_plan(priority_steps, plan, nutrition, progress):
    steps = [
        "День 1: выполнить самый приоритетный шаг из ИИ-рекомендаций.",
        "День 2: записаться на ближайшую тренировку или подтвердить уже выбранное время.",
        "День 3: заполнить питание за день без пропусков основных приемов пищи.",
        "День 4: открыть текущий план и выполнить следующий тренировочный день.",
        "День 5: отправить тренеру один вопрос по плану или нагрузке.",
        "День 7: обновить замеры и сравнить неделю с предыдущей.",
    ]
    if priority_steps:
        steps[0] = f"День 1: {priority_steps[0]['action']}"
    if not plan:
        steps[3] = "День 4: согласовать с тренером короткий план на ближайшие 2 недели."
    if int(nutrition.get("days_with_food") or 0) < 3:
        steps.insert(3, "День 3: внести питание за вчера и сегодня, чтобы восстановить историю.")
    if progress.get("days_since_update") is None or progress.get("days_since_update", 0) > 14:
        steps.append("День 7: добавить свежий вес и замеры тела.")
    return steps[:7]


def signal_rank(signal):
    if not signal:
        return 9
    return {"high": 0, "medium": 1, "low": 2}.get(signal.get("severity"), 3)


def first_relevant_signal(row):
    signals = row.get("signals", [])
    for severity in ("high", "medium", "low"):
        for signal in signals:
            if signal.get("severity") == severity:
                return signal
    return None


def local_priority_client(row, signal):
    scenario = signal.get("scenario", "сообщение")
    client = row.get("name", "Клиент")
    scenario_labels = {
        "contact_today": "контакт",
        "plan_adjustment": "план",
        "activity_drop": "активность",
        "motivational_message": "сообщение",
        "workout_preparation": "тренировка",
        "renewal_support": "продление",
    }
    return {
        "priority": signal.get("severity", "medium"),
        "client": client,
        "scenario": scenario_labels.get(scenario, scenario),
        "title": signal.get("title", "Рабочий фокус"),
        "reason": signal.get("detail", ""),
        "recommended_action": local_action_for_signal(row, signal),
        "message_draft": local_message_for_signal(row, signal),
        "business_effect": local_business_effect(signal),
        "deadline": trainer_deadline(2 if signal.get("severity") == "high" else 5),
        "evidence": signal.get("title", ""),
    }


def local_action_for_signal(row, signal):
    signal_id = signal.get("id")
    if signal_id in {"no_workouts_yet", "no_recent_activity", "low_regular_activity", "activity_drop", "activity_decline"}:
        return "Связаться с клиентом, уточнить причину паузы и сразу предложить 2 удобных слота ближайшей тренировки."
    if signal_id == "no_upcoming_appointment":
        return "Открыть расписание, выбрать 2 свободных окна тренера и предложить клиенту записаться через сайт."
    if signal_id == "no_active_plan":
        return "Открыть карточку клиента, создать короткий план на 2 недели и назначить первую контрольную тренировку."
    if signal_id == "low_plan_completion":
        return "Упростить ближайшие тренировки: оставить ключевые упражнения, сократить лишний объем и согласовать реалистичный график."
    if signal_id in {"membership_expires_soon", "membership_expired"}:
        return "Подготовить мини-итог прогресса клиента и предложить следующий тренировочный цикл как основание для продления."
    if signal_id == "health_limitations":
        return "Проверить упражнения ближайшего дня и убрать движения, которые конфликтуют с указанными ограничениями."
    if signal_id == "progress_check_needed":
        return "Назначить контрольный замер и связать его с текущей целью клиента."
    return "Проверить карточку клиента и зафиксировать следующий конкретный шаг сопровождения."


def local_message_for_signal(row, signal):
    name = row.get("name", "Привет")
    first_name = name.split()[0] if name else "Привет"
    signal_id = signal.get("id")
    if signal_id in {"membership_expires_soon", "membership_expired"}:
        return f"{first_name}, давай подведем итоги текущего цикла и выберем следующую цель. Я подготовлю обновленный план, чтобы продление было осмысленным."
    if signal_id in {"no_active_plan", "low_plan_completion"}:
        return f"{first_name}, хочу обновить твой план под текущий ритм, чтобы тренировки снова были выполнимыми и давали результат."
    if signal_id == "progress_check_needed":
        return f"{first_name}, предлагаю на ближайшей тренировке сделать короткий контроль прогресса и сверить план с твоей целью."
    if signal_id == "no_upcoming_appointment":
        return f"{first_name}, вижу, что у нас пока нет будущей записи. Я открыл свободные окна в расписании, выбери удобное время на сайте."
    return f"{first_name}, привет! Вижу паузу в тренировках. Давай подберем удобное время на этой неделе и спокойно вернемся в режим."


def local_business_effect(signal):
    scenario = signal.get("scenario")
    if scenario == "renewal_support":
        return "Повышает вероятность продления за счет видимого прогресса и следующего понятного цикла."
    if scenario in {"contact_today", "activity_drop", "motivational_message"}:
        return "Снижает риск ухода и возвращает клиента к регулярным посещениям."
    if scenario == "plan_adjustment":
        return "Повышает выполнение плана и качество тренерского сопровождения."
    return "Поддерживает персональный сервис и лояльность клиента."


def local_plan_adjustment(row, signal):
    return {
        "client": row.get("name", "Клиент"),
        "current_signal": signal.get("title", ""),
        "adjustment": local_action_for_signal(row, signal),
        "why": signal.get("detail", ""),
    }


def local_plan_maintenance(row):
    plan = row.get("active_plan") or {}
    return {
        "client": row.get("name", "Клиент"),
        "current_signal": f"Активный план: {plan.get('title', 'план')}, выполнение {plan.get('completion_percent', 0)}%",
        "adjustment": "Проверить ближайший тренировочный день и оставить один понятный акцент на тренировку.",
        "why": "Даже при нормальной активности клиенту проще удерживать темп, когда следующий шаг конкретен.",
    }


def local_communication_script(item):
    return {
        "client": item.get("client", "Клиент"),
        "goal": item.get("title", "Контакт"),
        "message": item.get("message_draft", ""),
    }


def local_upcoming_workout(row):
    next_workout = (row.get("active_plan") or {}).get("next_workout") or {}
    exercises = next_workout.get("exercises", [])
    exercise_names = ", ".join(item.get("name", "") for item in exercises[:4] if item.get("name"))
    return {
        "client": row.get("name", "Клиент"),
        "preparation": f"Проверить блок «{next_workout.get('title', 'следующая тренировка')}» и заранее подготовить акцент: {exercise_names or 'основные упражнения'}.",
        "watch_out": row.get("health_limitations") or "Следить за техникой, самочувствием и соответствием нагрузки текущей цели.",
    }


def local_renewal_support(row):
    membership = row.get("membership", {})
    return {
        "client": row.get("name", "Клиент"),
        "action": "Показать клиенту прогресс, предложить новую цель и следующий план тренировок до разговора о продлении.",
        "signal": f"{membership.get('status', 'абонемент')} до {membership.get('end_date', 'не указано')}",
    }


def local_trainer_week_plan(low_activity, no_plan, expiring):
    steps = [
        "День 1: открыть список приоритетных клиентов и закрыть контакты с теми, у кого высокий риск.",
        "День 2: предложить каждому клиенту с паузой два конкретных слота для ближайшей тренировки.",
        "День 3: обновить планы клиентов без активного плана или с низким выполнением.",
        "День 4: подготовить ближайшие тренировки и проверить ограничения по здоровью.",
        "День 5: отправить клиентам короткие мотивационные сообщения с привязкой к их цели.",
        "День 7: сравнить число тренировок за неделю и отметить, кто вернулся к активности.",
    ]
    if expiring:
        steps.insert(2, "День 2: подготовить итоги прогресса для клиентов с истекающими абонементами.")
    if no_plan:
        steps.insert(3, "День 3: создать короткие двухнедельные планы для клиентов без активной программы.")
    if low_activity:
        steps.insert(1, "День 1: зафиксировать причину паузы у клиентов без тренировок более 14 дней.")
    return steps[:7]


def trainer_deadline(days):
    return (timezone.localdate() + timedelta(days=days)).strftime("%d.%m.%Y")


def client_deadline(days):
    return (timezone.localdate() + timedelta(days=days)).strftime("%d.%m.%Y")


def normalized_list(value):
    return value if isinstance(value, list) else []
