/* Reports: export menu and print. */
(function () {
    var menu = document.querySelector('[data-menu]');
    if (menu) {
        var toggle = menu.querySelector('[data-menu-toggle]'), list = menu.querySelector('.menu-list');
        function close() { list.hidden = true; toggle.setAttribute('aria-expanded', 'false'); }
        toggle.addEventListener('click', function (e) {
            e.stopPropagation();
            list.hidden = !list.hidden; toggle.setAttribute('aria-expanded', list.hidden ? 'false' : 'true');
        });
        document.addEventListener('click', function (e) { if (!menu.contains(e.target)) close(); });
        document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
    }
    var print = document.querySelector('[data-print]');
    if (print) print.addEventListener('click', function () { window.print(); });
})();
