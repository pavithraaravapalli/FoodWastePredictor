/* Waste analytics: trend line (with threshold), weekday bars, animated list bars. */
(function () {
    document.querySelectorAll('.fill[data-w]').forEach(function (bar) {
        var max = parseFloat(bar.dataset.max) || 1;
        requestAnimationFrame(function () { bar.style.width = Math.max(parseFloat(bar.dataset.w) / max * 100, 2) + '%'; });
    });
    if (!window.Chart) return;

    var css = getComputedStyle(document.documentElement);
    var forest = css.getPropertyValue('--forest').trim(), orange = css.getPropertyValue('--orange').trim();
    var sage = css.getPropertyValue('--sage').trim(), muted = css.getPropertyValue('--text-muted').trim();
    var threshold = JSON.parse(document.getElementById('threshold-data').textContent);
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    var grid = { color: '#eee9dc' };

    function fmt(iso, weekly) {
        var s = window.fwpFormatDate(iso);
        return weekly ? 'Wk of ' + s : s;
    }

    var t = JSON.parse(document.getElementById('trend-data').textContent);
    var weekly = t.unit === 'week';
    new Chart(document.getElementById('trend-chart'), {
        type: 'line',
        data: {
            labels: t.labels.map(function (l) { return fmt(l, weekly); }),
            datasets: [
                { label: 'Waste rate', data: t.waste_pct, borderColor: forest, backgroundColor: forest, tension: .3, pointRadius: 3, pointHoverRadius: 5, borderWidth: 2, spanGaps: true },
                { label: 'Alert threshold', data: t.labels.map(function () { return threshold; }), borderColor: orange, borderDash: [6, 5], borderWidth: 1.5, pointRadius: 0, pointHitRadius: 0 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    filter: function (item) { return item.datasetIndex === 0; },
                    callbacks: {
                        label: function (c) { return 'Waste rate: ' + (c.parsed.y === null ? 'n/a' : c.parsed.y + '%'); },
                        footer: function (items) { return 'Food cost wasted: ' + t.cost[items[0].dataIndex].toLocaleString('en-IN', { maximumFractionDigits: 0 }); }
                    }
                }
            },
            scales: {
                x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, maxTicksLimit: 8 } },
                y: { beginAtZero: true, grid: grid, border: { display: false }, ticks: { color: muted, callback: function (v) { return v + '%'; } } }
            }
        }
    });

    var w = JSON.parse(document.getElementById('weekday-data').textContent);
    new Chart(document.getElementById('weekday-chart'), {
        type: 'bar',
        data: {
            labels: w.map(function (d) { return d.name; }),
            datasets: [{
                label: 'Waste rate', data: w.map(function (d) { return d.waste_pct; }), borderRadius: 4,
                backgroundColor: w.map(function (d) { return d.waste_pct !== null && d.waste_pct > threshold ? orange : sage; })
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { callbacks: {
                label: function (c) { var d = w[c.dataIndex]; return d.waste_pct === null ? 'No records' : d.waste_pct + '% (' + d.n + ' records)'; } } } },
            scales: {
                x: { grid: { display: false }, ticks: { color: muted } },
                y: { beginAtZero: true, grid: grid, border: { display: false }, ticks: { color: muted, callback: function (v) { return v + '%'; } } }
            }
        }
    });
})();
