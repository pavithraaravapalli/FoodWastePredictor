/* Auth pages: show/hide password, strength meter, live confirm check. */
(function () {
    document.querySelectorAll('[data-reveal]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = btn.parentElement.querySelector('input');
            var show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            btn.innerHTML = '<i data-lucide="' + (show ? 'eye-off' : 'eye') + '"></i>';
            if (window.lucide) window.lucide.createIcons();
        });
    });

    var form = document.querySelector('form[data-signup]');
    if (form) {
        var pwFields = form.querySelectorAll('.input-wrap input');
        var pw1 = pwFields[pwFields.length - 2], pw2 = pwFields[pwFields.length - 1];  // last two: (current password,) new, confirm
        var meter = form.querySelector('[data-strength]');
        var text = form.querySelector('[data-strength-text]');
        var msg = form.querySelector('[data-match-msg]');
        var labels = ['Too short', 'Weak', 'Fair', 'Good', 'Strong'];

        function score(v) {
            if (v.length < 8) return v.length ? 0 : -1;
            var s = 1;
            if (v.length >= 12) s++;
            if (/[a-z]/.test(v) && /[A-Z]/.test(v)) s++;
            if (/\d/.test(v) && /[^A-Za-z0-9]/.test(v)) s++; else if (/\d/.test(v) && /[A-Za-z]/.test(v) && v.length >= 10) s++;
            return Math.min(s, 4);
        }
        function check() {
            var s = score(pw1.value);
            if (meter) {
                meter.dataset.level = Math.max(s, 0);
                text.textContent = s < 0 ? 'Use 8+ characters with letters and numbers' : labels[s];
            }
            var mismatch = pw2.value && pw1.value !== pw2.value;
            if (msg) msg.hidden = !mismatch;
            pw2.classList.toggle('is-invalid', !!mismatch);
        }
        pw1.addEventListener('input', check);
        pw2.addEventListener('input', check);
        form.addEventListener('submit', function (e) {
            if (pw1.value !== pw2.value) {
                e.preventDefault(); check(); form.classList.add('shake');
                setTimeout(function () { form.classList.remove('shake'); }, 350);
                pw2.focus();
            }
        });
    }
})();
