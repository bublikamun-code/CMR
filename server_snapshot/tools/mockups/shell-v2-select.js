/* Prototype-only, dependency-free select enhancement. Load CSS before calling enhance.
 * Native selects stay in place for form data/delegated input + change handlers.
 * Call enhance(root) after rendering or programmatic value/options/disabled edits;
 * call close() before replacing a drawer. No document-wide mutation observer.
 */
(function () {
    'use strict';
    var instances = new WeakMap();
    var active = null;
    var moving = false;
    var serial = 0;

    function uid() {
        var id;
        do { id = 'kb-select-' + (++serial); } while (document.getElementById(id));
        return id;
    }
    function focusButton(item) {
        if (item.button.isConnected && !item.button.disabled) item.button.focus({ preventScroll: true });
    }
    function available(option) {
        return !option.disabled && !option.hidden && !(option.parentElement &&
            option.parentElement.matches('optgroup[disabled], optgroup[hidden]'));
    }
    function close(restoreFocus) {
        if (!active) return;
        var item = active;
        active = null;
        var hadFocus = item.surface.contains(document.activeElement);
        moving = true;
        if (item.surface.hasAttribute('popover')) {
            if (item.surface.matches(':popover-open')) item.surface.hidePopover();
            item.surface.removeAttribute('popover');
        }
        item.button.setAttribute('aria-expanded', 'false');
        item.button.removeAttribute('aria-activedescendant');
        item.list.hidden = true;
        item.surface.classList.remove('is-open');
        item.surface.removeAttribute('style');
        item.list.style.removeProperty('max-height');
        item.anchor.appendChild(item.surface);
        item.anchor.style.removeProperty('height');
        item.anchor.style.removeProperty('width');
        if (item.cell) { item.cell.style.removeProperty('width'); item.cell = null; }
        item.buffer = '';
        if (restoreFocus !== false && hadFocus) focusButton(item);
        moving = false;
    }
    function setActive(item, index) {
        item.index = index;
        item.rows.forEach(function (row, i) { row.classList.toggle('is-active', i === index); });
        var row = item.rows[index];
        if (!row) { item.button.removeAttribute('aria-activedescendant'); return; }
        item.button.setAttribute('aria-activedescendant', row.id);
        // Scroll only the list, never the drawer or page (no scrollIntoView).
        var top = row.offsetTop;
        if (top < item.list.scrollTop) item.list.scrollTop = top;
        else if (top + row.offsetHeight > item.list.scrollTop + item.list.clientHeight) {
            item.list.scrollTop = top + row.offsetHeight - item.list.clientHeight;
        }
    }
    function sync(item) {
        var select = item.select;
        item.options = Array.from(select.options);
        item.text.textContent = item.options.filter(function (option) { return option.selected; })
            .map(function (option) { return option.label; }).join(', ');
        item.button.disabled = select.matches(':disabled');
        item.button.setAttribute('aria-disabled', String(item.button.disabled));
        item.button.setAttribute('aria-required', String(select.required));
        ['aria-invalid', 'aria-describedby', 'aria-errormessage'].forEach(function (name) {
            if (select.hasAttribute(name)) item.button.setAttribute(name, select.getAttribute(name));
            else item.button.removeAttribute(name);
        });
        var labelledby = select.getAttribute('aria-labelledby') || item.labelIds.join(' ');
        var label = select.getAttribute('aria-label');
        item.button.removeAttribute('aria-label');
        item.button.removeAttribute('aria-labelledby');
        if (label && !select.hasAttribute('aria-labelledby')) {
            item.button.setAttribute('aria-label', label + (item.text.textContent ? ': ' + item.text.textContent : ''));
            item.list.setAttribute('aria-label', label);
        } else if (labelledby) {
            item.button.setAttribute('aria-labelledby', labelledby + ' ' + item.text.id);
            item.list.setAttribute('aria-labelledby', labelledby);
        } else {
            item.button.setAttribute('aria-labelledby', item.text.id);
            item.list.setAttribute('aria-label', select.name || 'Варианты');
        }
        if (active === item && item.button.disabled) close();
    }
    function buildOptions(item) {
        item.list.replaceChildren();
        item.rows = item.options.map(function (option, index) {
            var row = document.createElement('li');
            row.id = item.list.id + '-' + index;
            row.className = 'kb-select-option';
            row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', String(option.selected));
            row.setAttribute('aria-disabled', String(!available(option)));
            row.hidden = option.hidden || !!option.closest('optgroup[hidden]');
            row.textContent = option.label;
            row.dataset.index = index;
            item.list.appendChild(row);
            return row;
        });
        if (item.select.multiple) item.list.setAttribute('aria-multiselectable', 'true');
    }
    function open(item) {
        sync(item);
        if (item.button.disabled || !item.anchor.isConnected) return;
        close();
        buildOptions(item);
        var rect = item.anchor.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        var view = window.visualViewport;
        var leftEdge = (view ? view.offsetLeft : 0) + 8;
        var topEdge = (view ? view.offsetTop : 0) + 8;
        var width = (view ? view.width : document.documentElement.clientWidth) - 16;
        var height = (view ? view.height : window.innerHeight) - 16;
        item.anchor.style.height = rect.height + 'px';
        // В таблице заданная ширина anchor меняет распределение колонок:
        // ячейка «Печать» разъезжается и сдвигает всю строку. Там держим
        // ширину ячейки, а anchor оставляем в потоке — он и так растянут
        // на всю ширину ячейки и без заданного значения не схлопывается.
        var cell = item.anchor.closest('td, th');
        if (cell) {
            item.cell = cell;
            cell.style.width = cell.getBoundingClientRect().width + 'px';
        } else {
            item.anchor.style.width = rect.width + 'px';
        }
        item.dialog = item.select.closest('dialog');
        moving = true;
        active = item;
        item.surface.classList.add('is-open');
        item.surface.style.width = Math.min(width, Math.max(210, rect.width)) + 'px';
        item.surface.style.left = Math.max(leftEdge, Math.min(rect.left,
            leftEdge + width - Math.min(width, Math.max(210, rect.width)))) + 'px';
        item.surface.style.top = topEdge + 'px';
        // Preserve typography when leaving a field container.
        item.surface.style.font = getComputedStyle(item.button).font;
        (item.dialog || document.body).appendChild(item.surface);
        // The top layer also escapes drawer overflow and animated transforms.
        if (typeof item.surface.showPopover === 'function') {
            item.surface.setAttribute('popover', 'manual');
            item.surface.showPopover();
        }
        item.list.hidden = false;
        item.button.setAttribute('aria-expanded', 'true');
        var triggerHeight = item.button.getBoundingClientRect().height;
        item.list.style.maxHeight = Math.max(0, Math.min(280, height - triggerHeight - 2)) + 'px';
        var surfaceHeight = item.surface.getBoundingClientRect().height;
        item.surface.style.top = Math.max(topEdge, Math.min(rect.top, topEdge + height - surfaceHeight)) + 'px';
        focusButton(item);
        var selected = item.options.findIndex(function (option) { return option.selected && available(option); });
        setActive(item, selected < 0 ? item.options.findIndex(available) : selected);
        item.scrollPositions = new Map();
        for (var parent = item.anchor.parentElement; parent; parent = parent.parentElement) {
            item.scrollPositions.set(parent, [parent.scrollLeft, parent.scrollTop]);
        }
        item.scrollPositions.set(document, [window.scrollX, window.scrollY]);
        moving = false;
    }
    function choose(item, index) {
        var option = item.options[index];
        if (!option || !available(option) || item.select.matches(':disabled')) return;
        var changed = item.select.multiple || item.select.selectedIndex !== index;
        if (item.select.multiple) option.selected = !option.selected;
        else item.select.selectedIndex = index;
        sync(item);
        // Restore original DOM order/focus BEFORE dispatch: change may rerender it.
        close();
        if (changed) {
            item.select.dispatchEvent(new Event('input', { bubbles: true }));
            item.select.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
    function keydown(item, event) {
        var key = event.key;
        if (item.select.matches(':disabled') || event.ctrlKey || event.metaKey) return;
        if (key === 'Tab') {
            if (active === item) close(); // Native Tab follows the original DOM order.
            return;
        }
        if (key === 'Escape') {
            if (active === item) { event.preventDefault(); event.stopPropagation(); close(); }
            return;
        }
        if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(key)) {
            event.preventDefault();
            var wasOpen = active === item;
            if (!wasOpen) open(item);
            if (active !== item) return;
            if (key === 'Enter' || key === ' ') {
                if (wasOpen) choose(item, item.index);
                return;
            }
            var indices = item.options.reduce(function (result, option, i) {
                if (available(option)) result.push(i);
                return result;
            }, []);
            var at = indices.indexOf(item.index);
            if (key === 'Home') at = 0;
            else if (key === 'End') at = indices.length - 1;
            else if (wasOpen) at = Math.max(0, Math.min(indices.length - 1, at + (key === 'ArrowDown' ? 1 : -1)));
            setActive(item, indices[at] === undefined ? -1 : indices[at]);
        } else if (key.length === 1 && !event.altKey) {
            event.preventDefault();
            if (active !== item) open(item);
            if (active !== item) return;
            var now = Date.now();
            item.buffer = now - item.typedAt < 700 ? item.buffer + key : key;
            item.typedAt = now;
            var search = item.buffer.toLocaleLowerCase();
            if (Array.from(search).every(function (letter) { return letter === search[0]; })) search = search[0];
            for (var n = 1; n <= item.options.length; n++) {
                var i = (item.index + n) % item.options.length;
                if (available(item.options[i]) && item.options[i].label.trim().toLocaleLowerCase().startsWith(search)) {
                    setActive(item, i); break;
                }
            }
        }
    }

    function enhanceOne(select) {
        var existing = instances.get(select);
        if (existing) {
            if (active === existing) close();
            sync(existing);
            return existing;
        }
        if (!select.id) select.id = uid();
        var triggerId = select.id === 'kb-store-select' ? 'kb-store' : select.id + '-trigger';
        var labels = Array.from(select.labels || []);
        // Convert wrapping labels to div containers with external labels, so the
        // hidden native select and visible button never share a wrapping label.
        labels = labels.map(function (label) {
            if (label.contains(select)) {
                var container = document.createElement('div');
                Array.from(label.attributes).forEach(function (attribute) {
                    if (attribute.name !== 'for' && attribute.name !== 'id') {
                        container.setAttribute(attribute.name, attribute.value);
                    }
                });
                var branch = Array.from(label.childNodes).find(function (child) {
                    return child === select || (child.contains && child.contains(select));
                });
                // Keep help/error siblings after the control rather than folding
                // them into its accessible label during wrapping-label conversion.
                var following = [];
                for (var sibling = branch.nextSibling; sibling; sibling = sibling.nextSibling) following.push(sibling);
                label.replaceWith(container);
                container.appendChild(label);
                container.appendChild(branch);
                following.forEach(function(node) { container.appendChild(node); });
                label.className = 'kb-select-label';
            }
            label.htmlFor = triggerId;
            if (!label.id) label.id = uid();
            return label;
        });
        var anchor = document.createElement('div');
        anchor.className = 'kb-select-anchor';
        if (select.id === 'kb-store-select') anchor.classList.add('kb-select-store');
        var surface = document.createElement('div');
        surface.className = 'kb-select-surface';
        var button = document.createElement('button');
        button.type = 'button';
        button.id = triggerId;
        button.className = 'kb-select-trigger';
        button.setAttribute('role', 'combobox');
        button.setAttribute('aria-haspopup', 'listbox');
        button.setAttribute('aria-expanded', 'false');
        var text = document.createElement('span');
        text.id = triggerId + '-value';
        text.className = 'kb-select-value';
        button.appendChild(text);
        var arrow = document.createElement('span');
        arrow.className = 'kb-select-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        button.appendChild(arrow);
        var list = document.createElement('ul');
        list.id = select.id === 'kb-store-select' ? 'kb-store-options' : select.id + '-options';
        list.className = 'kb-select-list';
        list.setAttribute('role', 'listbox');
        list.hidden = true;
        button.setAttribute('aria-controls', list.id);
        surface.append(button, list);
        anchor.appendChild(surface);
        select.after(anchor);
        var item = { select: select, anchor: anchor, surface: surface, button: button,
            text: text, list: list, labelIds: labels.map(function (label) { return label.id; }),
            options: [], rows: [], index: -1, buffer: '', typedAt: 0 };
        instances.set(select, item);
        select.hidden = true;
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');
        select.classList.add('kb-select-native');
        sync(item);
        button.addEventListener('click', function () {
            if (active === item) close(); else open(item);
        });
        button.addEventListener('keydown', function (event) { keydown(item, event); });
        button.addEventListener('focus', function () { if (!moving) sync(item); });
        // Keep DOM focus on the combobox; options use aria-activedescendant.
        list.addEventListener('pointerdown', function (event) {
            if (event.target.closest('[role="option"]')) event.preventDefault();
        });
        list.addEventListener('click', function (event) {
            var row = event.target.closest('[role="option"]');
            if (row) choose(item, Number(row.dataset.index));
        });
        select.addEventListener('invalid', function (event) {
            event.preventDefault();
            focusButton(item);
            button.setAttribute('aria-invalid', 'true');
        });
        return item;
    }

    function enhance(root) {
        root = root || document;
        var selects = Array.from(root.querySelectorAll('select'));
        if (root.matches && root.matches('select')) selects.unshift(root);
        return selects.map(function (select) { return enhanceOne(select).button; });
    }
    function focus(select) {
        if (!select || !select.matches || !select.matches('select')) return false;
        var item = enhanceOne(select);
        if (active && active !== item) close();
        focusButton(item);
        return document.activeElement === item.button;
    }
    ['input', 'change'].forEach(function (name) {
        document.addEventListener(name, function (event) {
            var item = instances.get(event.target);
            if (item) {
                sync(item);
                if (active === item) { buildOptions(item); setActive(item, item.select.selectedIndex); }
            }
        });
    });
    document.addEventListener('reset', function (event) {
        // The native reset default action runs after this event.
        setTimeout(function () { if (!event.defaultPrevented) enhance(event.target); }, 0);
    });
    document.addEventListener('pointerdown', function (event) {
        if (active && !active.surface.contains(event.target)) close();
    }, true);
    document.addEventListener('click', function (event) {
        if (active && !active.surface.contains(event.target)) close();
    }, true);
    document.addEventListener('focusin', function (event) {
        if (!moving && active && !active.surface.contains(event.target)) close(false);
    });
    document.addEventListener('focusout', function () {
        // Reparenting can emit focusout with a null relatedTarget.
        queueMicrotask(function () {
            if (!moving && active && !active.surface.contains(document.activeElement)) close(false);
        });
    });
    document.addEventListener('scroll', function (event) {
        if (!active || moving || active.list.contains(event.target)) return;
        var target = event.target;
        var previous = active.scrollPositions && active.scrollPositions.get(target);
        var left = target === document ? window.scrollX : target.scrollLeft;
        var top = target === document ? window.scrollY : target.scrollTop;
        // A queued scroll from focusing the trigger may arrive after opening.
        if (previous && previous[0] === left && previous[1] === top) return;
        if (target === document || (target.contains && target.contains(active.anchor))) close();
    }, true);
    document.addEventListener('close', function (event) {
        if (active && active.dialog === event.target) close(false);
    }, true);
    document.addEventListener('cancel', function (event) {
        if (active && active.dialog === event.target) { event.preventDefault(); close(); }
    }, true);
    window.addEventListener('resize', function () { close(); });
    window.addEventListener('blur', function () { close(); });
    if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', function () { close(); });
        window.visualViewport.addEventListener('scroll', function () { close(); });
    }
    window.KBSelect = { enhance: enhance, close: close, focus: focus };
})();

