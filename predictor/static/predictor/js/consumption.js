/* Expected vs actual ingredient usage (values are in base units: g / ml / pcs). */
(function () {
    var canvas = document.getElementById('usage-chart');
    if (!canvas || !window.Chart) return;
    var d = JSON.parse(document.getElementById('usage-data').textContent);
    var css = getComputedStyle(document.documentElement);
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    new Chart(canvas, {
        type: 'bar',
        data: { labels: d.labels, datasets: [
            { label: 'Expected (recipe)', data: d.expected, backgroundColor: css.getPropertyValue('--sage').trim() },
            { label: 'Actual (recorded by staff)', data: d.actual, backgroundColor: css.getPropertyValue('--forest').trim() }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: '#eee9dc' } }, x: { grid: { display: false } } } }
    });
})();
