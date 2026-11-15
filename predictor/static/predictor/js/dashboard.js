/* Dashboard: trend chart + animated bars. */
(function () {
    var el = document.getElementById('chart-data');
    var canvas = document.getElementById('trend-chart');

    // Horizontal bars: width relative to the largest value in the list.
    document.querySelectorAll('.fill[data-w]').forEach(function (bar) {
        var max = parseFloat(bar.dataset.max) || 1;
        requestAnimationFrame(function () { bar.style.width = (parseFloat(bar.dataset.w) / max * 100) + '%'; });
    });

    if (!el || !canvas || !window.Chart) return;
    var data = JSON.parse(el.textContent);
    var css = getComputedStyle(document.documentElement);
    var forest = css.getPropertyValue('--forest').trim();
    var orange = css.getPropertyValue('--orange').trim();
    var muted = css.getPropertyValue('--text-muted').trim();

    function fmt(iso) {
        return window.fwpFormatDate(iso);
    }

    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.labels.map(fmt),
            datasets: [
                { label: 'Sold', data: data.sold, backgroundColor: forest, borderRadius: 3, stack: 'a' },
                { label: 'Wasted', data: data.wasted, backgroundColor: orange, borderRadius: 3, stack: 'a' }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        footer: function (items) {
                            var i = items[0].dataIndex, p = data.prepared[i];
                            return p ? 'Prepared ' + p + ' · waste ' + (data.wasted[i] / p * 100).toFixed(1) + '%' : '';
                        }
                    }
                }
            },
            scales: {
                x: { stacked: true, grid: { display: false }, ticks: { color: muted, maxTicksLimit: 8, maxRotation: 0 } },
                y: { stacked: true, beginAtZero: true, grid: { color: '#eee9dc' }, border: { display: false }, ticks: { color: muted, precision: 0 }, title: { display: true, text: 'Portions', color: muted } }
            }
        }
    });
})();
