(function () {
    const palette = ["#0f766e", "#c75f28", "#2563eb", "#7c3aed", "#b7791f", "#b42318"];
    const charts = window.dashboardCharts || {};

    if (window.chartData && document.getElementById("weightChart")) {
        const legacyDatasets = [{ label: "Вес", values: window.chartData.values || [], color: "#0f766e" }];
        if (Array.isArray(window.chartData.waist)) {
            legacyDatasets.push(
                { label: "Талия", values: window.chartData.waist, color: "#c75f28" },
                { label: "Грудь", values: window.chartData.chest || [], color: "#2563eb" },
                { label: "Бедра", values: window.chartData.hips || [], color: "#7c3aed" }
            );
        }
        charts.legacyWeight = {
            emptyText: "Нет замеров за период",
            labels: window.chartData.labels || [],
            datasets: legacyDatasets,
            suffix: legacyDatasets.length === 1 ? " кг" : "",
        };
        document.getElementById("weightChart").dataset.chart = "line";
        document.getElementById("weightChart").dataset.chartKey = "legacyWeight";
    }

    document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
        const data = charts[canvas.dataset.chartKey];
        if (!data) {
            return;
        }

        if (canvas.dataset.chart === "line") {
            renderLine(canvas, data);
        } else if (canvas.dataset.chart === "bar") {
            renderBar(canvas, data);
        } else if (canvas.dataset.chart === "groupedBar") {
            renderGroupedBar(canvas, data);
        } else if (canvas.dataset.chart === "donut") {
            renderDonut(canvas, data);
        } else if (canvas.dataset.chart === "heatmap") {
            renderHeatmap(canvas, data);
        }
    });

    function setup(canvas) {
        const context = canvas.getContext("2d");
        const ratio = window.devicePixelRatio || 1;
        const width = canvas.clientWidth || 640;
        const height = canvas.clientHeight || Number(canvas.getAttribute("height")) || 240;
        canvas.width = width * ratio;
        canvas.height = height * ratio;
        context.scale(ratio, ratio);
        context.clearRect(0, 0, width, height);
        context.fillStyle = "#ffffff";
        context.fillRect(0, 0, width, height);
        return { context, width, height };
    }

    function numericValues(datasets) {
        return datasets.flatMap((dataset) => (dataset.values || []).filter((value) => value !== null && value !== undefined));
    }

    function drawEmpty(context, text) {
        context.fillStyle = "#66736d";
        context.font = "14px Segoe UI, Arial, sans-serif";
        context.fillText(text || "Нет данных", 24, 38);
    }

    function renderLine(canvas, data) {
        const { context, width, height } = setup(canvas);
        const labels = data.labels || [];
        const datasets = data.datasets || [];
        const values = numericValues(datasets);
        const padding = { top: 42, right: 26, bottom: 42, left: 58 };
        const plotWidth = width - padding.left - padding.right;
        const plotHeight = height - padding.top - padding.bottom;
        const ySteps = data.ySteps || 6;

        drawAxes(context, padding, plotWidth, plotHeight, ySteps);
        drawLegend(context, datasets, padding.left, 20);

        if (values.length === 0) {
            drawEmpty(context, data.emptyText);
            return;
        }

        const minValue = Math.min(...values);
        const maxValue = Math.max(...values);
        const span = Math.max(maxValue - minValue, 1);
        const x = (index) => padding.left + (labels.length <= 1 ? plotWidth / 2 : (plotWidth / (labels.length - 1)) * index);
        const y = (value) => padding.top + plotHeight - ((value - minValue) / span) * plotHeight;

        drawValueAxisLabels(context, padding, plotHeight, minValue, maxValue, data.suffix || "", ySteps);

        datasets.forEach((dataset, datasetIndex) => {
            const color = dataset.color || palette[datasetIndex % palette.length];
            const markerStep = axisLabelStep(labels, plotWidth, 52);
            context.strokeStyle = color;
            context.lineWidth = dataset.lineWidth || 3;
            context.setLineDash(dataset.dash || []);
            context.beginPath();
            let started = false;

            (dataset.values || []).forEach((value, index) => {
                if (value === null || value === undefined) {
                    return;
                }
                if (!started) {
                    context.moveTo(x(index), y(value));
                    started = true;
                } else {
                    context.lineTo(x(index), y(value));
                }
            });
            context.stroke();
            context.setLineDash([]);

            (dataset.values || []).forEach((value, index) => {
                if (value === null || value === undefined) {
                    return;
                }
                if (labels.length > 80 && index % markerStep !== 0 && index !== labels.length - 1) {
                    return;
                }
                context.fillStyle = "#ffffff";
                context.strokeStyle = color;
                context.lineWidth = 2;
                context.beginPath();
                context.arc(x(index), y(value), 4, 0, Math.PI * 2);
                context.fill();
                context.stroke();
            });
        });

        if (data.showMissingDates) {
            drawMissingDateMarkers(context, labels, datasets, padding, plotWidth, plotHeight);
        }
        drawXAxisLabels(context, labels, padding, plotWidth, height);
    }

    function renderBar(canvas, data) {
        const { context, width, height } = setup(canvas);
        const labels = data.labels || [];
        const values = data.values || [];
        const colors = data.colors || palette;
        const padding = { top: 26, right: 42, bottom: 24, left: 178 };
        const plotWidth = width - padding.left - padding.right;
        const plotHeight = height - padding.top - padding.bottom;
        const maxValue = Math.max(...values, 1);

        if (values.length === 0) {
            drawEmpty(context, data.emptyText);
            return;
        }

        context.strokeStyle = "#eef2ed";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(padding.left, padding.top);
        context.lineTo(padding.left, padding.top + plotHeight);
        context.stroke();

        const rowGap = 10;
        const rowHeight = Math.max((plotHeight - rowGap * (values.length - 1)) / Math.max(values.length, 1), 24);
        const barHeight = Math.min(rowHeight * 0.62, 24);
        values.forEach((value, index) => {
            const rowY = padding.top + index * (rowHeight + rowGap);
            const labelY = rowY + rowHeight / 2 + 4;
            const barWidth = Math.max((value / maxValue) * plotWidth, value > 0 ? 8 : 0);
            const y = rowY + (rowHeight - barHeight) / 2;

            context.fillStyle = "#66736d";
            context.font = "13px Segoe UI, Arial, sans-serif";
            context.textAlign = "right";
            context.fillText(fitLabel(context, labels[index], padding.left - 20), padding.left - 12, labelY);

            context.fillStyle = colors[index % colors.length];
            roundRect(context, padding.left, y, barWidth, barHeight, 6);
            context.fill();

            context.fillStyle = "#252927";
            context.textAlign = "left";
            context.font = "700 13px Segoe UI, Arial, sans-serif";
            context.fillText(value, padding.left + barWidth + 8, labelY);
        });
        context.textAlign = "left";
    }

    function renderGroupedBar(canvas, data) {
        const { context, width, height } = setup(canvas);
        const labels = data.labels || [];
        const datasets = data.datasets || [];
        const values = numericValues(datasets);
        const padding = { top: 42, right: 22, bottom: 58, left: 50 };
        const plotWidth = width - padding.left - padding.right;
        const plotHeight = height - padding.top - padding.bottom;
        const maxValue = Math.max(...values, 1);
        const tickStep = maxValue <= 4 ? 1 : Math.ceil(maxValue / 4);
        const scaleMax = Math.max(Math.ceil(maxValue / tickStep) * tickStep, tickStep);
        const groupGap = labels.length > 26 ? 1 : labels.length > 14 ? 8 : 18;

        drawAxes(context, padding, plotWidth, plotHeight, scaleMax / tickStep);
        drawYAxisNumbers(context, padding, plotHeight, scaleMax, tickStep);
        drawLegend(context, datasets, padding.left, 20);
        if (values.length === 0) {
            drawEmpty(context, data.emptyText);
            return;
        }

        const groupWidth = Math.max((plotWidth - groupGap * (labels.length - 1)) / Math.max(labels.length, 1), 4);
        const barWidth = Math.max(groupWidth / Math.max(datasets.length, 1) - 3, labels.length > 26 ? 5 : 6);
        const labelMinSpacing = labels.length > 24 ? 136 : labels.length > 14 ? 104 : 74;
        const labelStep = axisLabelStep(labels, plotWidth, labelMinSpacing);
        labels.forEach((label, labelIndex) => {
            const groupX = padding.left + labelIndex * (groupWidth + groupGap);
            datasets.forEach((dataset, datasetIndex) => {
                const value = (dataset.values || [])[labelIndex] || 0;
                const barHeight = (value / scaleMax) * plotHeight;
                const x = groupX + datasetIndex * (barWidth + 4);
                const y = padding.top + plotHeight - barHeight;
                context.fillStyle = dataset.color || palette[datasetIndex % palette.length];
                roundRect(context, x, y, barWidth, barHeight, 5);
                context.fill();
            });
            if (labelIndex % labelStep !== 0 && labelIndex !== labels.length - 1) {
                return;
            }
            context.fillStyle = "#66736d";
            context.font = `${labels.length > 24 ? 10 : 12}px Segoe UI, Arial, sans-serif`;
            context.textAlign = "center";
            context.textBaseline = "top";
            context.fillText(fitLabel(context, label, Math.max(labelMinSpacing - 18, 70)), groupX + groupWidth / 2, height - 28);
            context.textAlign = "left";
            context.textBaseline = "alphabetic";
        });
    }

    function renderDonut(canvas, data) {
        const { context, width, height } = setup(canvas);
        const labels = data.labels || [];
        const values = data.values || [];
        const colors = data.colors || palette;
        const total = values.reduce((sum, value) => sum + value, 0);
        const radius = Math.min(width, height) * 0.25;
        const centerX = width < 520 ? width * 0.34 : width * 0.36;
        const centerY = height * 0.48;
        let startAngle = -Math.PI / 2;

        if (total === 0) {
            drawEmpty(context, data.emptyText);
            return;
        }

        values.forEach((value, index) => {
            const angle = (value / total) * Math.PI * 2;
            context.beginPath();
            context.moveTo(centerX, centerY);
            context.arc(centerX, centerY, radius, startAngle, startAngle + angle);
            context.closePath();
            context.fillStyle = colors[index % colors.length];
            context.fill();
            startAngle += angle;
        });

        context.globalCompositeOperation = "destination-out";
        context.beginPath();
        context.arc(centerX, centerY, radius * 0.58, 0, Math.PI * 2);
        context.fill();
        context.globalCompositeOperation = "source-over";
        context.fillStyle = "#252927";
        context.font = "700 24px Segoe UI, Arial, sans-serif";
        context.textAlign = "center";
        context.fillText(data.centerLabel || total, centerX, centerY + 8);
        context.textAlign = "left";

        labels.forEach((label, index) => {
            const y = 38 + index * 24;
            const x = width < 520 ? width * 0.62 : width * 0.64;
            context.fillStyle = colors[index % colors.length];
            context.fillRect(x, y - 10, 12, 12);
            context.fillStyle = "#252927";
            context.font = "12px Segoe UI, Arial, sans-serif";
            context.fillText(`${fitLabel(context, label, width - x - 52)}: ${values[index]}`, x + 18, y);
        });
    }

    function renderHeatmap(canvas, data) {
        const { context, width, height } = setup(canvas);
        const days = data.days || [];
        const weekdays = data.weekdays || ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
        const colors = ["#e2ebe6", "#65c3a8", "#35a98a", "#1d8f78", "#0f766e"];
        const padding = { top: 30, right: 16, bottom: 34, left: 24 };
        const rows = 7;
        const firstWeekday = Math.max(0, Math.min(days[0]?.weekday ?? 0, rows - 1));
        const columns = Math.max(Math.ceil((days.length + firstWeekday) / rows), 1);
        const gap = columns > 32 ? 1 : columns > 16 ? 3 : 5;
        const cell = Math.min(
            (width - padding.left - padding.right - gap * (columns - 1)) / columns,
            (height - padding.top - padding.bottom - gap * (rows - 1)) / rows,
            24
        );
        const gridWidth = columns * cell + (columns - 1) * gap;
        const startX = padding.left + Math.max((width - padding.left - padding.right - gridWidth) / 2, 0);
        const startY = padding.top;

        if (days.length === 0) {
            drawEmpty(context, data.emptyText);
            return;
        }

        context.fillStyle = "#66736d";
        context.font = "11px Segoe UI, Arial, sans-serif";
        weekdays.forEach((label, index) => {
            context.fillText(label, 8, startY + index * (cell + gap) + cell * 0.68);
        });

        days.forEach((day, index) => {
            const gridIndex = index + firstWeekday;
            const column = Math.floor(gridIndex / rows);
            const row = day.weekday ?? gridIndex % rows;
            const x = startX + column * (cell + gap);
            const y = startY + row * (cell + gap);
            const colorIndex = Math.max(0, Math.min(day.level || 0, colors.length - 1));
            context.fillStyle = day.inPeriod === false ? "#f3f6f4" : colors[colorIndex];
            context.strokeStyle = day.value ? "#0f766e" : day.inPeriod === false ? "#e8eee9" : "#cfd9d3";
            context.lineWidth = 1;
            roundRect(context, x, y, cell, cell, Math.min(5, cell / 2));
            context.fill();
            context.stroke();
            if (day.value && cell < 14) {
                context.fillStyle = "#0f766e";
                context.beginPath();
                context.arc(x + cell / 2, y + cell / 2, Math.max(1.8, cell * 0.24), 0, Math.PI * 2);
                context.fill();
            } else if (day.value && cell >= 14) {
                context.fillStyle = "#ffffff";
                context.font = "700 11px Segoe UI, Arial, sans-serif";
                context.textAlign = "center";
                context.fillText(day.value, x + cell / 2, y + cell * 0.68);
                context.textAlign = "left";
            }
        });

        const first = days[0];
        const last = days[days.length - 1];
        context.fillStyle = "#66736d";
        context.font = "12px Segoe UI, Arial, sans-serif";
        if (gridWidth < 96 && first && last) {
            context.textAlign = "center";
            context.fillText(`${first.label}-${last.label}`, startX + gridWidth / 2, height - 12);
        } else {
            context.fillText(first ? first.label : "", startX, height - 12);
            context.textAlign = "right";
            context.fillText(last ? last.label : "", startX + gridWidth, height - 12);
        }
        if (data.hasData === false) {
            context.textAlign = "center";
            context.fillStyle = "#66736d";
            context.font = "13px Segoe UI, Arial, sans-serif";
            context.fillText(data.emptyText || "Нет активности за период", width / 2, 20);
        }
        context.textAlign = "left";
    }

    function drawAxes(context, padding, plotWidth, plotHeight, gridSteps = 4) {
        context.strokeStyle = "#dfe5dc";
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(padding.left, padding.top);
        context.lineTo(padding.left, padding.top + plotHeight);
        context.lineTo(padding.left + plotWidth, padding.top + plotHeight);
        context.stroke();

        context.strokeStyle = "#eef2ed";
        for (let step = 1; step < gridSteps; step += 1) {
            const y = padding.top + (plotHeight / gridSteps) * step;
            context.beginPath();
            context.moveTo(padding.left, y);
            context.lineTo(padding.left + plotWidth, y);
            context.stroke();
        }
    }

    function drawYAxisNumbers(context, padding, plotHeight, maxValue, step) {
        context.save();
        context.fillStyle = "#66736d";
        context.font = "11px Segoe UI, Arial, sans-serif";
        context.textAlign = "right";
        context.textBaseline = "middle";
        for (let value = 0; value <= maxValue; value += step) {
            const y = padding.top + plotHeight - (value / maxValue) * plotHeight;
            context.fillText(String(value), padding.left - 10, y);
        }
        context.restore();
    }

    function drawValueAxisLabels(context, padding, plotHeight, minValue, maxValue, suffix = "", steps = 4) {
        const span = Math.max(maxValue - minValue, 1);
        context.save();
        context.fillStyle = "#66736d";
        context.font = "11px Segoe UI, Arial, sans-serif";
        context.textAlign = "right";
        context.textBaseline = "middle";
        for (let step = 0; step <= steps; step += 1) {
            const value = maxValue - (span / steps) * step;
            const y = padding.top + (plotHeight / steps) * step;
            context.fillText(`${value.toFixed(1)}${suffix}`, padding.left - 10, y);
        }
        context.restore();
    }

    function drawMissingDateMarkers(context, labels, datasets, padding, plotWidth, plotHeight) {
        const markerStep = axisLabelStep(labels, plotWidth, 44);
        labels.forEach((label, index) => {
            const hasValue = datasets.some((dataset) => {
                const value = (dataset.values || [])[index];
                return value !== null && value !== undefined;
            });
            if (hasValue || (labels.length > 80 && index % markerStep !== 0 && index !== labels.length - 1)) {
                return;
            }
            const x = padding.left + (labels.length <= 1 ? plotWidth / 2 : (plotWidth / (labels.length - 1)) * index);
            const y = padding.top + plotHeight;
            context.fillStyle = "#f5f8f6";
            context.strokeStyle = "#b7c2bc";
            context.lineWidth = 1;
            context.beginPath();
            context.arc(x, y, 3, 0, Math.PI * 2);
            context.fill();
            context.stroke();
        });
    }

    function drawLegend(context, datasets, x, y) {
        context.font = "12px Segoe UI, Arial, sans-serif";
        datasets.forEach((dataset, index) => {
            const color = dataset.color || palette[index % palette.length];
            const itemX = x + index * 135;
            context.fillStyle = color;
            context.fillRect(itemX, y - 9, 12, 12);
            context.fillStyle = "#66736d";
            context.fillText(fitLabel(context, dataset.label || `Показатель ${index + 1}`, 110), itemX + 18, y + 1);
        });
    }

    function drawXAxisLabels(context, labels, padding, plotWidth, height) {
        context.fillStyle = "#66736d";
        context.font = "11px Segoe UI, Arial, sans-serif";
        context.textAlign = "center";
        const minSpacing = labels.length <= 7 ? 34 : labels.length > 80 ? 112 : 72;
        const step = labels.length <= 7 ? 1 : axisLabelStep(labels, plotWidth, minSpacing);
        let lastRight = -Infinity;
        labels.forEach((label, index) => {
            if (index % step !== 0 && index !== labels.length - 1) {
                return;
            }
            const x = padding.left + (labels.length <= 1 ? plotWidth / 2 : (plotWidth / (labels.length - 1)) * index);
            const textWidth = context.measureText(label).width;
            const left = x - textWidth / 2;
            const right = x + textWidth / 2;
            if (labels.length > 7 && left <= lastRight + 10) {
                return;
            }
            context.fillText(label, Math.max(padding.left, Math.min(x, padding.left + plotWidth)), height - 14);
            lastRight = right;
        });
        context.textAlign = "left";
    }

    function axisLabelStep(labels, plotWidth, minSpacing) {
        const slots = Math.max(Math.floor(plotWidth / minSpacing), 1);
        return Math.max(Math.ceil((labels || []).length / slots), 1);
    }

    function roundRect(context, x, y, width, height, radius) {
        const actualRadius = Math.min(radius, width / 2, height / 2);
        context.beginPath();
        context.moveTo(x + actualRadius, y);
        context.arcTo(x + width, y, x + width, y + height, actualRadius);
        context.arcTo(x + width, y + height, x, y + height, actualRadius);
        context.arcTo(x, y + height, x, y, actualRadius);
        context.arcTo(x, y, x + width, y, actualRadius);
        context.closePath();
    }

    function shortLabel(label) {
        if (!label) {
            return "";
        }
        return label.length > 16 ? `${label.slice(0, 15)}…` : label;
    }

    function fitLabel(context, label, maxWidth) {
        if (!label) {
            return "";
        }
        if (context.measureText(label).width <= maxWidth) {
            return label;
        }
        let value = label;
        while (value.length > 1 && context.measureText(`${value}…`).width > maxWidth) {
            value = value.slice(0, -1);
        }
        return `${value}…`;
    }
})();
