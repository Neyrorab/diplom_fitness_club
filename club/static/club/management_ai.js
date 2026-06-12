(function () {
    const loading = document.querySelector("[data-ai-loading], [data-management-ai-loading]");
    if (!loading) {
        return;
    }

    const statusUrl = loading.dataset.statusUrl;
    const statusText = loading.querySelector("[data-ai-status-text]");
    const queuedText = loading.dataset.queuedText || "Задание ждет запуска. Метрики сохранены, страницу можно покинуть.";
    const runningText = loading.dataset.runningText || "Модель анализирует метрики и формирует структурированный ответ.";
    const retryText = loading.dataset.retryText || "Проверяем статус фонового анализа. Обновление продолжится автоматически.";
    let timer = null;

    function statusRequestUrl() {
        const url = new URL(statusUrl, window.location.href);
        url.searchParams.set("_", Date.now().toString());
        return url.toString();
    }

    function resultUrl(url) {
        const target = new URL(url, window.location.href);
        target.searchParams.set("_ai_done", Date.now().toString());
        return target.toString();
    }

    function schedule() {
        window.clearTimeout(timer);
        timer = window.setTimeout(poll, 1400);
    }

    function poll() {
        fetch(statusRequestUrl(), {
            cache: "no-store",
            headers: { Accept: "application/json" },
            credentials: "same-origin",
        })
            .then((response) => {
                if (!response.ok) {
                    throw new Error("status request failed");
                }
                return response.json();
            })
            .then((data) => {
                if (statusText) {
                    statusText.textContent = data.status === "queued" ? queuedText : runningText;
                }
                if (data.finished) {
                    window.location.assign(resultUrl(data.dashboard_url));
                    return;
                }
                schedule();
            })
            .catch(() => {
                if (statusText) {
                    statusText.textContent = retryText;
                }
                schedule();
            });
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            poll();
        }
    });

    poll();
})();
