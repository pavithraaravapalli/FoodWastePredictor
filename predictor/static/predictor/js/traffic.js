/* Traffic page: map centred on the restaurant (OpenStreetMap tiles, no API key). */
(function () {
    var el = document.getElementById('map');
    if (!el || !window.L) return;
    var lat = parseFloat(el.dataset.lat), lng = parseFloat(el.dataset.lng);
    if (isNaN(lat) || isNaN(lng)) return;
    L.Icon.Default.prototype.options.imagePath = el.dataset.icons;
    var map = L.map(el, { scrollWheelZoom: false }).setView([lat, lng], 14);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    L.marker([lat, lng]).addTo(map).bindPopup(document.createTextNode(el.dataset.name || 'Restaurant')).openPopup();
    map.on('focus', function () { map.scrollWheelZoom.enable(); });
    map.on('blur', function () { map.scrollWheelZoom.disable(); });
})();
