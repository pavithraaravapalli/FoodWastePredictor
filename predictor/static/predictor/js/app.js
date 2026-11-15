/* Shared behaviour: icons, sidebar, toasts, loading buttons. */
/* Shared: ISO date (2026-12-31) -> display date (31-12-2026). */
window.fwpFormatDate = function (iso) {
    var p = String(iso).slice(0, 10).split('-');
    return p.length === 3 ? p[2] + '-' + p[1] + '-' + p[0] : iso;
};

(function () {
    var root = document.documentElement;
    var body = document.body;

    function icons() { if (window.lucide) window.lucide.createIcons(); }
    icons();

    function store(value) { try { localStorage.setItem('fwp-sidebar', value); } catch (e) {} }

    // Desktop collapse
    document.querySelectorAll('[data-sidebar-toggle]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var collapsed = root.classList.toggle('sidebar-collapsed');
            store(collapsed ? 'collapsed' : 'expanded');
        });
    });

    // Collapsible navigation groups
    document.querySelectorAll('.nav-group').forEach(function (group) {
        var groupId = group.dataset.navGroup;
        var btn = group.querySelector('[data-nav-group-toggle]');
        var hasActive = group.classList.contains('has-active') || group.querySelector('.nav-item.is-active') !== null;

        if (hasActive) {
            group.classList.remove('is-collapsed');
            if (btn) btn.setAttribute('aria-expanded', 'true');
        } else if (groupId) {
            try {
                if (localStorage.getItem('fwp-group-' + groupId) === 'collapsed') {
                    group.classList.add('is-collapsed');
                    if (btn) btn.setAttribute('aria-expanded', 'false');
                }
            } catch (e) {}
        }

        if (btn) {
            btn.addEventListener('click', function (e) {
                e.preventDefault();
                var collapsed = group.classList.toggle('is-collapsed');
                btn.setAttribute('aria-expanded', String(!collapsed));
                if (groupId) {
                    try {
                        localStorage.setItem('fwp-group-' + groupId, collapsed ? 'collapsed' : 'expanded');
                    } catch (e) {}
                }
            });
        }
    });

    // Mobile drawer
    function setNav(open) { body.classList.toggle('nav-open', open); }
    document.querySelectorAll('[data-sidebar-open]').forEach(function (b) { b.addEventListener('click', function () { setNav(true); }); });
    document.querySelectorAll('[data-sidebar-close]').forEach(function (b) { b.addEventListener('click', function () { setNav(false); }); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setNav(false); });

    // Toasts: dismiss button + auto-hide for success messages
    function hide(toast) {
        toast.classList.add('is-hiding');
        setTimeout(function () { toast.remove(); }, 220);
    }
    document.querySelectorAll('.toast').forEach(function (t) {
        var btn = t.querySelector('[data-dismiss]');
        if (btn) btn.addEventListener('click', function () { hide(t); });
        if (t.classList.contains('toast-success')) setTimeout(function () { hide(t); }, 6000);
    });

    // Loading state on form submit (prevents double submits)
    document.querySelectorAll('form[data-loading-form]').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            var btn = e.submitter || form.querySelector('button[type="submit"]');
            if (!btn) return;
            // Defer so the browser still submits the form before the button is disabled.
            setTimeout(function () {
                btn.classList.add('is-loading');
                var label = btn.querySelector('span');
                if (label) label.textContent = form.dataset.busyText || 'Saving…';
            }, 0);
        });
    });

    // Confirmation dialog for destructive / state-changing POST actions.
    var dialog = document.getElementById('confirm-dialog');
    if (dialog && dialog.showModal) {
        var form = document.getElementById('confirm-form');
        document.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-confirm-action]');
            if (!btn) return;
            form.action = btn.dataset.confirmAction;
            // Optional extra hidden fields, e.g. data-confirm-fields="decision=decline"
            form.querySelectorAll('input[data-extra]').forEach(function (n) { n.remove(); });
            (btn.dataset.confirmFields || '').split('&').filter(Boolean).forEach(function (pair) {
                var kv = pair.split('=');
                var input = document.createElement('input');
                input.type = 'hidden'; input.name = kv[0]; input.value = kv.slice(1).join('='); input.dataset.extra = '1';
                form.appendChild(input);
            });
            document.getElementById('confirm-next').value = btn.dataset.confirmNext || '';
            document.getElementById('confirm-title').textContent = btn.dataset.confirmTitle || 'Are you sure?';
            document.getElementById('confirm-text').textContent = btn.dataset.confirmText || '';
            var submit = document.getElementById('confirm-submit');
            // An explicitly empty label means "nothing to confirm": just show the message.
            submit.hidden = btn.dataset.confirmButton === '';
            submit.textContent = btn.dataset.confirmButton || 'Confirm';
            dialog.querySelector('[data-dialog-cancel]').textContent = submit.hidden ? 'Got it' : 'Cancel';
            dialog.dataset.tone = btn.dataset.confirmTone || 'danger';
            dialog.showModal();
        });
        dialog.querySelector('[data-dialog-cancel]').addEventListener('click', function () { dialog.close(); });
        dialog.addEventListener('click', function (e) { if (e.target === dialog) dialog.close(); });
    }

    // Filter forms: selects/dates apply instantly, typing waits for a pause.
    document.querySelectorAll('form[data-autosubmit]').forEach(function (f) {
        f.querySelectorAll('.select-auto, .date-auto').forEach(function (el) {
            el.addEventListener('change', function () { f.submit(); });
        });
        var search = f.querySelector('input[type="search"]');
        if (search) {
            var timer;
            search.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(function () { f.submit(); }, 450); });
            if (search.value) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
        }
    });
})();
