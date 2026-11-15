/* Sign-up wizard: two steps, live checklist, inline validation, password suggestion. */
(function () {
    var form = document.querySelector('form[data-wizard]');
    if (!form) return;

    var steps = form.querySelectorAll('[data-step]');
    var dots = document.querySelectorAll('[data-dot]');
    var sub = document.querySelector('[data-step-sub]');
    var next = form.querySelector('[data-next]');
    var name = form.querySelector('[name="full_name"]');
    var email = form.querySelector('[name="email"]');
    var pw1 = form.querySelector('[name="password1"]');
    var pw2 = form.querySelector('[name="password2"]');
    var current = 1;
    var touched = {};

    function icons() { if (window.lucide) window.lucide.createIcons(); }
    function msgFor(field) { return form.querySelector('[data-msg-for="' + field + '"]'); }

    /* ---- validation rules ---- */
    var EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
    var checks = {
        full_name: function () { return name.value.trim().length >= 2 ? '' : 'Please enter your full name.'; },
        email: function () {
            var v = email.value.trim();
            if (!v) return 'Enter your email address.';
            return EMAIL.test(v) ? '' : 'That doesn\'t look like a valid email address.';
        }
    };
    var rules = {
        len: function (v) { return v.length >= 8; },
        letter: function (v) { return /[A-Za-z]/.test(v); },
        number: function (v) { return /\d/.test(v); },
        match: function (v) { return v.length > 0 && v === pw2.value; }
    };

    function showField(field, input) {
        var text = checks[field]();
        var el = msgFor(field), wrap = input.closest('.input-wrap');
        if (touched[field]) {
            el.textContent = text; el.hidden = !text;
            input.classList.toggle('is-invalid', !!text);
            wrap.classList.toggle('is-valid', !text);
        }
    }

    function updateRules() {
        var v = pw1.value;
        form.querySelectorAll('[data-rule]').forEach(function (li) {
            var ok = rules[li.dataset.rule](v);
            li.classList.toggle('is-ok', ok);
            var i = li.querySelector('svg, i');
            if (i && i.dataset) {
                var icon = ok ? 'circle-check' : 'circle';
                var fresh = document.createElement('i'); fresh.setAttribute('data-lucide', icon);
                i.replaceWith(fresh);
            }
        });
        icons();
    }

    function step1Valid() {
        return !checks.full_name() && !checks.email() &&
            rules.len(pw1.value) && rules.letter(pw1.value) && rules.number(pw1.value) && rules.match(pw1.value);
    }
    function refresh() { updateRules(); next.disabled = !step1Valid(); }

    name.addEventListener('blur', function () { touched.full_name = true; showField('full_name', name); });
    email.addEventListener('blur', function () { touched.email = true; showField('email', email); });
    name.addEventListener('input', function () { showField('full_name', name); refresh(); });
    email.addEventListener('input', function () { showField('email', email); refresh(); });
    pw1.addEventListener('input', refresh);
    pw2.addEventListener('input', refresh);

    /* Caps Lock hint */
    var caps = form.querySelector('[data-caps]');
    [pw1, pw2].forEach(function (el) {
        el.addEventListener('keyup', function (e) {
            if (e.getModifierState) caps.hidden = !e.getModifierState('CapsLock');
        });
        el.addEventListener('blur', function () { caps.hidden = true; });
    });

    /* ---- password suggestion ---- */
    function suggest() {
        var sets = ['abcdefghijkmnopqrstuvwxyz', 'ABCDEFGHJKLMNPQRSTUVWXYZ', '23456789', '!@#$%&*?'];
        var all = sets.join('');
        var buf = new Uint32Array(16);
        (window.crypto || window.msCrypto).getRandomValues(buf);
        var out = sets.map(function (s, i) { return s[buf[i] % s.length]; });      // one of each kind
        for (var i = out.length; i < 16; i++) out.push(all[buf[i] % all.length]);
        for (var j = out.length - 1; j > 0; j--) {                                  // shuffle
            var k = buf[j] % (j + 1), t = out[j]; out[j] = out[k]; out[k] = t;
        }
        return out.join('');
    }
    form.querySelector('[data-suggest]').addEventListener('click', function () {
        var pw = suggest();
        [pw1, pw2].forEach(function (el) {
            el.value = pw; el.type = 'text';
            el.dispatchEvent(new Event('input', { bubbles: true }));
        });
        form.querySelectorAll('[data-reveal]').forEach(function (b) {
            b.setAttribute('aria-label', 'Hide password'); b.innerHTML = '<i data-lucide="eye-off"></i>';
        });
        form.querySelector('[data-suggest-note]').hidden = false;
        icons();
    });
    var copy = form.querySelector('[data-copy]');
    copy.addEventListener('click', function () {
        var done = function (ok) { copy.textContent = ok ? 'copied!' : 'press Ctrl+C'; };
        try { navigator.clipboard.writeText(pw1.value).then(function () { done(true); }, function () { done(false); }); }
        catch (e) { pw1.select(); done(false); }
    });

    /* ---- step navigation ---- */
    function go(n, focus) {
        current = n;
        steps.forEach(function (s) { s.hidden = Number(s.dataset.step) !== n; });
        dots.forEach(function (d) {
            var i = Number(d.dataset.dot);
            d.classList.toggle('is-current', i === n);
            d.classList.toggle('is-done', i < n);
        });
        sub.textContent = n === 1 ? 'Start tracking food waste in a few minutes.' : 'One more step: the owner approves new accounts.';
        if (n === 2) {
            var first = name.value.trim().split(' ')[0] || 'there';
            form.querySelector('[data-summary]').textContent = 'Hi ' + first + ' · ' + email.value.trim();
        }
        if (focus !== false) {
            var target = steps[n - 1].querySelector('input:not([type=hidden]):not([type=radio]), select');
            if (target) setTimeout(function () { target.focus(); }, 30);
        }
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    next.addEventListener('click', function () {
        touched.full_name = touched.email = true;
        showField('full_name', name); showField('email', email);
        if (step1Valid()) go(2);
    });
    form.querySelector('[data-back]').addEventListener('click', function () { go(1); });
    // Enter on step 1 moves on instead of submitting the whole form early.
    form.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && current === 1 && e.target.tagName !== 'BUTTON') {
            e.preventDefault(); if (!next.disabled) next.click();
        }
    });
    // Guard: never submit from step 1.
    form.addEventListener('submit', function (e) {
        if (current === 1) { e.preventDefault(); if (!next.disabled) next.click(); }
    });

    /* Initial state (including after a server-side error). */
    refresh();
    var errStep = Number(form.dataset.errorStep) || 1;
    if (form.querySelector('.field-error:not([hidden])') && errStep === 2) {
        touched.full_name = touched.email = true;
        go(2, false);
    } else if (pw1.value || name.value) { touched.full_name = touched.email = true; showField('full_name', name); showField('email', email); }
})();
