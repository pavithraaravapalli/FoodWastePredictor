/* Date pickers: show DD-MM-YYYY everywhere, submit ISO (YYYY-MM-DD).
   Native <input type=date> displays in the OS locale (e.g. MM/DD/YYYY) and cannot be
   changed from a web page, so every date input is upgraded to flatpickr, which shows the
   alternate field as d-m-Y while the real input still carries the ISO value Django parses.
   Without JavaScript the native input still works and submits ISO. */
(function () {
    if (!window.flatpickr) return;
    var MIN = '2000-01-01';

    function upgrade(el) {
        if (el._flatpickr) return;
        var isDateTime = el.type === 'datetime-local';
        var opts = {
            allowInput: true,
            altInput: true,
            altFormat: isDateTime ? 'd-m-Y H:i' : 'd-m-Y',
            dateFormat: isDateTime ? 'Y-m-d\\TH:i' : 'Y-m-d',
            enableTime: isDateTime,
            time_24hr: true,
            minDate: el.getAttribute('min') || MIN,
            maxDate: el.getAttribute('max') || null,
            locale: { firstDayOfWeek: 1 },
            disableMobile: true
        };
        var label = el.getAttribute('aria-label') || (el.id && document.querySelector('label[for="' + el.id + '"]'));
        var fp = window.flatpickr(el, opts);
        if (fp.altInput) {
            fp.altInput.className = (el.className.replace('flatpickr-input', '').trim() || 'input') + ' flatpickr-alt';
            fp.altInput.setAttribute('placeholder', isDateTime ? 'DD-MM-YYYY HH:MM' : 'DD-MM-YYYY');
            fp.altInput.setAttribute('autocomplete', 'off');
            fp.altInput.setAttribute('inputmode', 'numeric');
            if (label) fp.altInput.setAttribute('aria-label', typeof label === 'string' ? label : label.textContent.trim());
            // Typing 31/12/2026 or 31.12.2026 is accepted as 31-12-2026.
            fp.altInput.addEventListener('input', function () {
                var v = fp.altInput.value, n = v.replace(/[\/.]/g, '-');
                if (v !== n) fp.altInput.value = n;
            });
            if (el.required) fp.altInput.required = false; // server validates; hidden input can't take focus
        }
    }
    document.querySelectorAll('input[type="date"], input[type="datetime-local"]').forEach(upgrade);
})();
