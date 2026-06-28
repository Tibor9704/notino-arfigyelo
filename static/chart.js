const chartEl = document.getElementById("chart-data");

if (chartEl) {
    const chartData = JSON.parse(chartEl.textContent);

    const ctx = document.getElementById('priceChart');
    const colors = [
        '#2563eb',
        '#16a34a',
        '#dc2626',
        '#9333ea',
        '#ea580c',
        '#0891b2',
        '#be123c',
        '#4f46e5'
    ];

    const uniqueLabels = [...new Set((chartData.labels || []).filter(Boolean))];

    const originalDatasets = (chartData.datasets || [{
        label: 'Ár (Ft)',
        data: chartData.prices
    }]).map((dataset, index) => ({
        ...dataset,
        borderColor: colors[index % colors.length],
        backgroundColor: colors[index % colors.length],
        borderWidth: 3,
        pointRadius: 3,
        pointHoverRadius: 6,
        spanGaps: true,
        tension: 0.3
    }));

    const chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: uniqueLabels,
            datasets: originalDatasets.map(dataset => ({ ...dataset }))
        },
        options: {
            responsive: true,
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const value = context.parsed.y;
                            if (value === null || value === undefined) {
                                return `${context.dataset.label}: nincs adat`;
                            }
                            return `${context.dataset.label}: ${Math.round(value)} Ft`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    ticks: {
                        callback: function(value) {
                            return `${value} Ft`;
                        }
                    }
                }
            }
        }
    });

    window.updateChart = function(days) {
        let labels = chartData.labels;
        let datasets = originalDatasets;

        if (days === 'all') {
            chart.data.labels = uniqueLabels;
            chart.data.datasets = datasets.map(dataset => ({ ...dataset }));
        } else {
            chart.data.labels = uniqueLabels.slice(-days);
            chart.data.datasets = datasets.map(dataset => ({
                ...dataset,
                data: dataset.data.slice(-days)
            }));
        }

        chart.update();
    };
}
