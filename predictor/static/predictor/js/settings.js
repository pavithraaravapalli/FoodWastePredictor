/* Settings page: tabs and "use my location". */
(function () {
    var tabs = document.querySelector('[data-tabs]');
    if (!tabs) return;
    document.body.classList.add('tabs-js');
    var buttons = tabs.querySelectorAll('[data-tab]');
    var panels = document.querySelectorAll('[data-panel]');

    function show(name) {
        buttons.forEach(function (b) {
            var on = b.dataset.tab === name;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        panels.forEach(function (p) { p.classList.toggle('is-active', p.dataset.panel === name); });
        try { history.replaceState(null, '', '?tab=' + name); } catch (e) {}
    }
    buttons.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });
    show(tabs.dataset.initial || 'profile');

    var geo = document.querySelector('[data-geolocate]');
    if (geo) {
        var msg = document.querySelector('[data-geo-msg]');
        geo.addEventListener('click', function () {
            if (!navigator.geolocation) { msg.textContent = 'Your browser cannot share its location.'; return; }
            msg.textContent = 'Asking your browser for its location…';
            navigator.geolocation.getCurrentPosition(function (pos) {
                document.getElementById('id_latitude').value = pos.coords.latitude.toFixed(6);
                document.getElementById('id_longitude').value = pos.coords.longitude.toFixed(6);
                msg.textContent = 'Filled in from this device. Check it is where your restaurant is, then save.';
            }, function () {
                msg.textContent = 'Could not get the location (permission denied or unavailable). You can type it in instead.';
            }, { timeout: 10000 });
        });
    }
})();
