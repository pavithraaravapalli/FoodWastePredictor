/* Reports: prepared vs sold per day. */
(function () {
    var el = document.getElementById('report-data'), canvas = document.getElementById('report-chart');
    if (!el || !canvas || !window.Chart) return;
    var d = JSON.parse(el.textContent);
    var css = getComputedStyle(document.documentElement);
    var forest = css.getPropertyValue('--forest').trim(), sage = css.getPropertyValue('--sage').trim(), muted = css.getPropertyValue('--text-muted').trim();
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    function fmt(iso) { return window.fwpFormatDate(iso); }
    new Chart(canvas, {
        type: 'bar',
        data: { labels: d.labels.map(fmt), datasets: [
            { label: 'Prepared', data: d.prepared, backgroundColor: sage, borderRadius: 3 },
            { label: 'Sold', data: d.sold, backgroundColor: forest, borderRadius: 3 }] },
        options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
            plugins: { legend: { display: false } },
            scales: { x: { grid: { display: false }, ticks: { color: muted, maxRotation: 0, maxTicksLimit: 10 } },
                      y: { beginAtZero: true, grid: { color: '#eee9dc' }, border: { display: false }, ticks: { color: muted, precision: 0 } } } }
    });
})();
