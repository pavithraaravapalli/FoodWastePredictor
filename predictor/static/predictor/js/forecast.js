/* Forecast page: recent sales bars + forecast bars for the target date. */
(function () {
    var el = document.getElementById('fc-chart-data'), canvas = document.getElementById('fc-chart');
    if (!el || !canvas || !window.Chart) return;
    var d = JSON.parse(el.textContent);
    var css = getComputedStyle(document.documentElement);
    var forest = css.getPropertyValue('--forest').trim(), sage = css.getPropertyValue('--sage').trim();
    var orange = css.getPropertyValue('--orange').trim(), muted = css.getPropertyValue('--text-muted').trim();
    var n = d.labels.length - 1;
    var pad = function (v) { return d.sold.concat([null]).map(function (x, i) { return i === n ? v : null; }); };

    function fmt(iso) { return window.fwpFormatDate(iso); }
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: d.labels.map(fmt),
            datasets: [
                { label: 'Sold', data: d.sold.concat([null]), backgroundColor: forest, borderRadius: 3 },
                { label: 'Expected demand', data: pad(d.expected), backgroundColor: sage, borderRadius: 3 },
                { label: 'Prepare', data: pad(d.recommended), backgroundColor: orange, borderRadius: 3 }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, maxTicksLimit: 8 } },
                y: { beginAtZero: true, grid: { color: '#eee9dc' }, border: { display: false }, ticks: { color: muted, precision: 0 }, title: { display: true, text: 'Portions', color: muted } }
            }
        }
    });
})();
