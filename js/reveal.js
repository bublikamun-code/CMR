/**
 * Reveal animations on scroll.
 * Адаптировано из /Users/yaroslav/Downloads/app/src/components/Reveal.tsx
 * Добавляет класс .is-in элементам с .reveal, когда они появляются в viewport.
 */
(function () {
    'use strict';

    const VERSION = 1;
    if (window.__revealVersion >= VERSION) return;
    window.__revealVersion = VERSION;

    // Отключаем анимации, если в URL есть ?noanim или система просит уменьшить motion
    const noAnim = location.search.indexOf('noanim') !== -1 ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    if (noAnim) {
        document.documentElement.classList.add('no-anim');
        return;
    }

    function reveal(el) {
        if (!el.classList.contains('is-in')) {
            el.classList.add('is-in');
        }
    }

    const observed = new Set();
    let io = null;

    function getObserver() {
        if (!io) {
            io = new IntersectionObserver(
                (entries) => {
                    entries.forEach((entry) => {
                        if (entry.isIntersecting) {
                            reveal(entry.target);
                            io.unobserve(entry.target);
                            observed.delete(entry.target);
                        }
                    });
                },
                { threshold: 0.12, rootMargin: '0px 0px -6% 0px' }
            );
        }
        return io;
    }

    function observeEl(el) {
        if (observed.has(el) || el.classList.contains('is-in')) return;
        getObserver().observe(el);
        observed.add(el);

        // Страховка: если через 1.6с элемент всё ещё не показан и он на экране — показываем
        setTimeout(() => {
            const r = el.getBoundingClientRect();
            if (r.top < window.innerHeight) reveal(el);
        }, 1600);
    }

    function init(root) {
        root = root || document;
        const items = Array.from(root.querySelectorAll('.reveal'));
        if (!items.length) return;

        items.forEach((el) => {
            // Если элемент уже в viewport — показываем с небольшой задержкой
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) {
                // Элемент скрыт (display:none / visibility:hidden) — наблюдаем,
                // когда он станет видимым, сработает IntersectionObserver.
                observeEl(el);
            } else if (rect.top < window.innerHeight * 0.98) {
                setTimeout(() => reveal(el), 30 + parseInt(getComputedStyle(el).getPropertyValue('--d') || '0', 10));
            } else {
                observeEl(el);
            }
        });
    }

    // Публичный API: можно вызвать после динамической подгрузки контента
    // или переключения вкладок/страниц.
    window.revealRefresh = function (root) {
        init(root);
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => init());
    } else {
        init();
    }
})();
