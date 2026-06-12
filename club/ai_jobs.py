from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone

from .ai_management import (
    ManagementAIError,
    get_client_ai_recommendations,
    get_management_ai_recommendations,
    get_trainer_ai_recommendations,
    get_weight_forecast,
)
from .models import ClientAIAnalysis, ManagementAIAnalysis, TrainerAIAnalysis, WeightForecastAnalysis


management_ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="management-ai")
trainer_ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="trainer-ai")
client_ai_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="client-ai")
weight_forecast_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="weight-forecast-ai")
STALE_ANALYSIS_AFTER = timedelta(minutes=30)


def queue_management_analysis(analysis_id):
    management_ai_executor.submit(run_management_analysis, analysis_id)


def queue_trainer_analysis(analysis_id):
    trainer_ai_executor.submit(run_trainer_analysis, analysis_id)


def queue_client_analysis(analysis_id):
    client_ai_executor.submit(run_client_analysis, analysis_id)


def queue_weight_forecast_analysis(analysis_id):
    weight_forecast_executor.submit(run_weight_forecast_analysis, analysis_id)


def run_management_analysis(analysis_id):
    close_old_connections()
    analysis = None
    try:
        analysis = ManagementAIAnalysis.objects.get(pk=analysis_id)
        if analysis.status == ManagementAIAnalysis.Status.DONE:
            return

        analysis.status = ManagementAIAnalysis.Status.RUNNING
        analysis.started_at = analysis.started_at or timezone.now()
        analysis.error = ""
        analysis.save(update_fields=["status", "started_at", "error"])

        result = get_management_ai_recommendations(analysis.payload, provider=analysis.provider, model=analysis.model)
        analysis.result = result
        analysis.model = result.get("model") or analysis.model
        analysis.status = ManagementAIAnalysis.Status.DONE
        analysis.finished_at = timezone.now()
        analysis.save(update_fields=["result", "model", "status", "finished_at"])
    except ManagementAIError as error:
        mark_failed_analysis(analysis or find_analysis(analysis_id), str(error))
    except Exception as error:
        mark_failed_analysis(analysis or find_analysis(analysis_id), f"Не удалось завершить ИИ-анализ: {error}")
    finally:
        close_old_connections()


def run_trainer_analysis(analysis_id):
    close_old_connections()
    analysis = None
    try:
        analysis = TrainerAIAnalysis.objects.get(pk=analysis_id)
        if analysis.status == TrainerAIAnalysis.Status.DONE:
            return

        analysis.status = TrainerAIAnalysis.Status.RUNNING
        analysis.started_at = analysis.started_at or timezone.now()
        analysis.error = ""
        analysis.save(update_fields=["status", "started_at", "error"])

        result = get_trainer_ai_recommendations(analysis.payload, provider=analysis.provider, model=analysis.model)
        analysis.result = result
        analysis.model = result.get("model") or analysis.model
        analysis.status = TrainerAIAnalysis.Status.DONE
        analysis.finished_at = timezone.now()
        analysis.save(update_fields=["result", "model", "status", "finished_at"])
    except ManagementAIError as error:
        mark_failed_analysis(analysis or find_trainer_analysis(analysis_id), str(error))
    except Exception as error:
        mark_failed_analysis(analysis or find_trainer_analysis(analysis_id), f"Не удалось завершить ИИ-анализ тренера: {error}")
    finally:
        close_old_connections()


def run_client_analysis(analysis_id):
    close_old_connections()
    analysis = None
    try:
        analysis = ClientAIAnalysis.objects.get(pk=analysis_id)
        if analysis.status == ClientAIAnalysis.Status.DONE:
            return

        analysis.status = ClientAIAnalysis.Status.RUNNING
        analysis.started_at = analysis.started_at or timezone.now()
        analysis.error = ""
        analysis.save(update_fields=["status", "started_at", "error"])

        result = get_client_ai_recommendations(analysis.payload, provider=analysis.provider, model=analysis.model)
        analysis.result = result
        analysis.model = result.get("model") or analysis.model
        analysis.status = ClientAIAnalysis.Status.DONE
        analysis.finished_at = timezone.now()
        analysis.save(update_fields=["result", "model", "status", "finished_at"])
    except ManagementAIError as error:
        mark_failed_analysis(analysis or find_client_analysis(analysis_id), str(error))
    except Exception as error:
        mark_failed_analysis(analysis or find_client_analysis(analysis_id), f"Не удалось завершить ИИ-анализ клиента: {error}")
    finally:
        close_old_connections()


def run_weight_forecast_analysis(analysis_id):
    close_old_connections()
    analysis = None
    try:
        analysis = WeightForecastAnalysis.objects.get(pk=analysis_id)
        if analysis.status == WeightForecastAnalysis.Status.DONE:
            return

        analysis.status = WeightForecastAnalysis.Status.RUNNING
        analysis.started_at = analysis.started_at or timezone.now()
        analysis.error = ""
        analysis.save(update_fields=["status", "started_at", "error"])

        result = get_weight_forecast(analysis.payload, provider=analysis.provider, model=analysis.model)
        analysis.result = result
        analysis.model = result.get("model") or analysis.model
        analysis.status = WeightForecastAnalysis.Status.DONE
        analysis.finished_at = timezone.now()
        analysis.save(update_fields=["result", "model", "status", "finished_at"])
    except ManagementAIError as error:
        mark_failed_analysis(analysis or find_weight_forecast_analysis(analysis_id), str(error))
    except Exception as error:
        mark_failed_analysis(analysis or find_weight_forecast_analysis(analysis_id), f"Не удалось построить прогноз веса: {error}")
    finally:
        close_old_connections()


def find_analysis(analysis_id):
    try:
        return ManagementAIAnalysis.objects.get(pk=analysis_id)
    except ManagementAIAnalysis.DoesNotExist:
        return None


def find_trainer_analysis(analysis_id):
    try:
        return TrainerAIAnalysis.objects.get(pk=analysis_id)
    except TrainerAIAnalysis.DoesNotExist:
        return None


def find_client_analysis(analysis_id):
    try:
        return ClientAIAnalysis.objects.get(pk=analysis_id)
    except ClientAIAnalysis.DoesNotExist:
        return None


def find_weight_forecast_analysis(analysis_id):
    try:
        return WeightForecastAnalysis.objects.get(pk=analysis_id)
    except WeightForecastAnalysis.DoesNotExist:
        return None


def mark_failed_analysis(analysis, error):
    if not analysis:
        return
    analysis.status = analysis.Status.FAILED
    analysis.error = error[:1200]
    analysis.finished_at = timezone.now()
    analysis.save(update_fields=["status", "error", "finished_at"])


def mark_stale_analysis_failed(analysis, label="ИИ-анализ"):
    if not analysis or analysis.status not in {analysis.Status.QUEUED, analysis.Status.RUNNING}:
        return analysis

    reference_time = analysis.started_at if analysis.status == analysis.Status.RUNNING else analysis.created_at
    if reference_time and timezone.now() - reference_time >= STALE_ANALYSIS_AFTER:
        mark_failed_analysis(
            analysis,
            f"{label} был прерван или сервер был перезапущен. Запустите анализ заново.",
        )
        analysis.refresh_from_db(fields=["status", "error", "finished_at"])
    return analysis
