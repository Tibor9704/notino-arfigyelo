const chartEl = document.getElementById("chart-data");
if (chartEl) {
    const chartData = JSON.parse(chartEl.textContent);
    const labels = [...new Set((chartData.labels || []).filter(Boolean))];
    const canvas = document.getElementById("priceChart");
    const ctx = canvas.getContext("2d");
    const accent = "#b81450";
    const palette = ["#7c3aed", "#147340", "#d97706", "#2563eb"];
    const makeGradient = color => {
        const rgb = color.match(/[0-9a-f]{2}/gi).map(value => parseInt(value, 16));
        const gradient = ctx.createLinearGradient(0, 0, 0, 360);
        gradient.addColorStop(0, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.13)`);
        gradient.addColorStop(0.35, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.055)`);
        gradient.addColorStop(0.72, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0.015)`);
        gradient.addColorStop(1, `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, 0)`);
        return gradient;
    };
    const originalDatasets = (chartData.datasets || [{ label: "Ár (Ft)", data: chartData.prices }]).map((dataset, index) => ({
        ...dataset,
        borderColor: index === 0 ? accent : palette[(index - 1) % palette.length],
        backgroundColor: "transparent",
        borderWidth: 2.5,
        pointRadius: labels.length > 40 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: "#fff",
        pointBorderWidth: 2,
        pointBorderColor: index === 0 ? accent : palette[(index - 1) % palette.length],
        spanGaps: true,
        tension: 0.35,
        fill: false
    }));
    const chart = new Chart(canvas, {
        type: "line",
        data: { labels, datasets: originalDatasets.map(dataset => ({ ...dataset })) },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: "index", intersect: false },
            layout: { padding: { top: 8, right: 12, bottom: 4 } },
            plugins: {
                legend: { display: originalDatasets.length > 1, position: "top", align: "end", labels: { usePointStyle: true, pointStyle: "circle", boxWidth: 7, boxHeight: 7, padding: 18, color: "#837580", font: { family: "Inter, system-ui, sans-serif", size: 12, weight: "600" } } },
                tooltip: {
                    backgroundColor: "#1a1518", titleColor: "#fcecf3", bodyColor: "#fff", padding: 12, cornerRadius: 10, displayColors: true, usePointStyle: true,
                    callbacks: {
                        title(items) { return items[0]?.label?.slice(0, 10) || ""; },
                        label(context) { const value = context.parsed.y; return value == null ? `${context.dataset.label}: nincs adat` : `${context.dataset.label}: ${Math.round(value).toLocaleString("hu-HU")} Ft`; }
                    }
                }
            },
            scales: {
                x: { grid: { display: false }, border: { display: false }, ticks: { color: "#837580", maxRotation: 0, autoSkip: true, maxTicksLimit: 7, font: { family: "Inter, system-ui, sans-serif", size: 11, weight: "500" }, callback(value) { return String(this.getLabelForValue(value)).slice(0, 10); } } },
                y: { beginAtZero: false, border: { display: false, dash: [3, 5] }, grid: { color: "rgba(227, 218, 240, 0.75)", drawTicks: false }, ticks: { color: "#837580", padding: 10, maxTicksLimit: 6, font: { family: "Inter, system-ui, sans-serif", size: 11 }, callback(value) { return `${Number(value).toLocaleString("hu-HU")} Ft`; } } }
            }
        }
    });
    window.updateChart = function(days) {
        const count = days === "all" ? labels.length : Number(days);
        const visibleLabels = labels.slice(-count);
        const startIndex = labels.length - visibleLabels.length;
        chart.data.labels = visibleLabels;
        chart.data.datasets = originalDatasets.map(dataset => ({ ...dataset, data: dataset.data.slice(startIndex, startIndex + visibleLabels.length) }));
        chart.update();
        document.querySelectorAll(".chart-filters button").forEach(button => {
            const active = button.dataset.period === String(days);
            button.classList.toggle("active", active);
            button.setAttribute("aria-pressed", String(active));
        });
    };
    window.updateChart("all");
}
