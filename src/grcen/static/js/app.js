/* GRCen page behaviour.
 *
 * The Content-Security-Policy is `script-src 'self' 'nonce-…'`, so the browser
 * refuses inline `on*=` attributes and htmx's `hx-on::` (both evaluate inline
 * code). Everything that used to live inline is attached here through
 * delegated listeners keyed on data-* attributes, which keeps templates
 * declarative and CSP-safe:
 *
 *   data-confirm="…"               on a <form>: confirm before submit;
 *                                  on a button/link: confirm before the click proceeds
 *   data-toggle="#id"              show/hide a region (keeps aria-expanded in sync)
 *   data-select-all=".cls"         checkbox that (un)checks every matching box
 *   data-copy="#id"                copy that element's text/value to the clipboard
 *   data-tag-chip="name" data-target="#input"   append a tag to a comma list
 *   data-select-on-focus           select the input's text on click/focus
 *   data-call="fn"                 call window[fn](el) on click
 *   data-call-change="fn"          call window[fn](el) on change
 *   .owner-picker + [data-pick-owner]   server-rendered autocomplete result buttons
 *   [data-asset-picker]            generic asset search (text input + hidden id)
 *   data-action="save-search|delete-search"     saved searches
 *   data-reload-on-success         htmx element: reload the page after a 2xx
 */
(function () {
  'use strict';

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // ---- flash / toast ------------------------------------------------------
  function notify(level, text) {
    var region = $('#flash-region');
    if (!region) { window.alert(text); return; }
    var div = document.createElement('div');
    var cls = level === 'error' ? 'alert-error' : level === 'warning' ? 'alert-warning' : 'alert-success';
    div.className = 'alert ' + cls;
    div.setAttribute('role', level === 'error' ? 'alert' : 'status');
    div.textContent = text;
    region.appendChild(div);
    region.hidden = false;
  }
  window.grcenNotify = notify;

  // ---- htmx configuration ---------------------------------------------------
  if (window.htmx) {
    // Delete / mark-read endpoints answer 204. htmx 2 does not swap on 204 by
    // default, which left the removed row on screen until a reload.
    htmx.config.responseHandling = [
      { code: '204', swap: true },
      { code: '[23]..', swap: true },
      { code: '[45]..', swap: false, error: true },
    ];
    // Minimal json-enc extension: lets a plain <form hx-post hx-ext="json-enc">
    // talk to the JSON-only REST endpoints.
    htmx.defineExtension('json-enc', {
      onEvent: function (name, evt) {
        if (name === 'htmx:configRequest') {
          evt.detail.headers['Content-Type'] = 'application/json';
        }
      },
      encodeParameters: function (xhr, parameters) {
        xhr.overrideMimeType('text/json');
        var obj = {};
        parameters.forEach(function (value, key) {
          if (Object.prototype.hasOwnProperty.call(obj, key)) {
            if (!Array.isArray(obj[key])) obj[key] = [obj[key]];
            obj[key].push(value);
          } else {
            obj[key] = value;
          }
        });
        return JSON.stringify(obj);
      },
    });
    document.addEventListener('htmx:afterRequest', function (evt) {
      var elt = evt.detail.elt;
      if (evt.detail.successful && elt && elt.hasAttribute('data-reload-on-success')) {
        location.reload();
      }
    });
    document.addEventListener('htmx:responseError', function (evt) {
      var xhr = evt.detail.xhr;
      if (xhr.status === 401) {
        location.href = '/login?expired=1&next=' + encodeURIComponent(location.pathname + location.search);
        return;
      }
      var msg = 'Request failed (' + xhr.status + ')';
      try {
        var body = JSON.parse(xhr.responseText);
        if (body && body.detail) {
          msg = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch (e) { /* not JSON */ }
      notify('error', msg);
    });
    document.addEventListener('htmx:sendError', function () {
      notify('error', 'Network error: the request did not reach the server.');
    });
  }

  // ---- confirmations ----------------------------------------------------------
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (form && form.matches && form.matches('form[data-confirm]')) {
      if (!window.confirm(form.getAttribute('data-confirm'))) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    }
  }, true);

  document.addEventListener('click', function (e) {
    var el = e.target.closest(
      '[data-confirm]:not(form), [data-toggle], [data-copy], [data-tag-chip], [data-call], ' +
      '[data-pick-owner], [data-pick-asset], [data-action], [data-select-on-focus]'
    );
    if (!el) return;
    if (el.hasAttribute('data-confirm') && !window.confirm(el.getAttribute('data-confirm'))) {
      e.preventDefault();
      e.stopImmediatePropagation();
      return;
    }
    if (el.hasAttribute('data-toggle')) { e.preventDefault(); toggle(el); }
    if (el.hasAttribute('data-copy')) { e.preventDefault(); copy(el); }
    if (el.hasAttribute('data-tag-chip')) { e.preventDefault(); addTag(el); }
    if (el.hasAttribute('data-call')) { e.preventDefault(); callFn(el.getAttribute('data-call'), el); }
    if (el.hasAttribute('data-pick-owner')) { e.preventDefault(); pickOwner(el); }
    if (el.hasAttribute('data-pick-asset')) { e.preventDefault(); pickAsset(el); }
    if (el.hasAttribute('data-select-on-focus') && el.select) { el.select(); }
    if (el.hasAttribute('data-action')) { action(el, e); }
  }, true);

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || !el.hasAttribute) return;
    if (el.hasAttribute('data-select-all')) {
      $all(el.getAttribute('data-select-all')).forEach(function (c) { c.checked = el.checked; });
    }
    if (el.hasAttribute('data-call-change')) {
      callFn(el.getAttribute('data-call-change'), el);
    }
  });

  function toggle(el) {
    var target = $(el.getAttribute('data-toggle'));
    if (!target) return;
    var hidden = getComputedStyle(target).display === 'none';
    // data-display names the display value to restore when a stylesheet
    // rule (e.g. the mobile nav) hides the element by default.
    target.style.display = hidden ? (target.getAttribute('data-display') || '') : 'none';
    if (hidden && getComputedStyle(target).display === 'none') target.style.display = 'block';
    el.setAttribute('aria-expanded', hidden ? 'true' : 'false');
  }

  // ---- navigation menus (<details class="nav-menu">) ----------------------------
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d.matches || !d.matches('details.nav-menu') || !d.open) return;
    $all('details.nav-menu[open]').forEach(function (other) { if (other !== d) other.open = false; });
  }, true);
  document.addEventListener('click', function (e) {
    if (e.target.closest('details.nav-menu')) return;
    $all('details.nav-menu[open]').forEach(function (d) { d.open = false; });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    $all('details.nav-menu[open]').forEach(function (d) { d.open = false; var s = d.querySelector('summary'); if (s) s.focus(); });
  });

  function copy(el) {
    var src = $(el.getAttribute('data-copy'));
    if (!src) return;
    var text = ('value' in src && src.value) ? src.value : src.textContent;
    text = (text || '').trim();
    var done = function () {
      var label = el.textContent;
      el.textContent = 'Copied';
      setTimeout(function () { el.textContent = label; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else {
      fallbackCopy(text); done();
    }
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'absolute'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(ta);
  }

  function addTag(el) {
    var input = $(el.getAttribute('data-target') || '#tags');
    if (!input) return;
    var value = el.getAttribute('data-tag-chip');
    var names = input.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (names.indexOf(value) === -1) names.push(value);
    input.value = names.join(', ');
  }

  function callFn(name, el) {
    var fn = window[name];
    if (typeof fn === 'function') fn(el);
  }

  // ---- pickers ------------------------------------------------------------------
  function pickOwner(btn) {
    var box = btn.closest('.owner-picker');
    if (!box) return;
    var text = box.querySelector('input[type=text]');
    var hidden = box.querySelector('input[name=owner_id]');
    var results = box.querySelector('.autocomplete-results');
    if (text) text.value = btn.getAttribute('data-name') || '';
    if (hidden) hidden.value = btn.getAttribute('data-id') || '';
    if (results) results.innerHTML = '';
  }

  function pickAsset(btn) {
    var box = btn.closest('[data-asset-picker]');
    if (!box) return;
    var text = box.querySelector('input[type=text]');
    var hidden = box.querySelector('input[type=hidden]');
    var results = box.querySelector('.autocomplete-results');
    if (text) text.value = btn.getAttribute('data-name') || '';
    if (hidden) hidden.value = btn.getAttribute('data-id') || '';
    if (results) results.innerHTML = '';
  }

  document.addEventListener('input', function (e) {
    var input = e.target;
    if (!input || !input.closest || input.type !== 'text') return;
    var box = input.closest('[data-asset-picker]');
    if (!box) return;
    var results = box.querySelector('.autocomplete-results');
    var hidden = box.querySelector('input[type=hidden]');
    if (hidden) hidden.value = '';
    var q = input.value.trim();
    clearTimeout(box._timer);
    if (q.length < 2) { if (results) results.innerHTML = ''; return; }
    box._timer = setTimeout(function () {
      var url = '/api/assets/search?q=' + encodeURIComponent(q);
      if (box.getAttribute('data-types')) url += '&types=' + encodeURIComponent(box.getAttribute('data-types'));
      fetch(url, { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (items) {
          if (!results) return;
          results.innerHTML = '';
          if (!items.length) {
            var empty = document.createElement('div');
            empty.className = 'autocomplete-item autocomplete-empty';
            empty.textContent = 'No matching assets';
            results.appendChild(empty);
            return;
          }
          items.slice(0, 10).forEach(function (a) {
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'autocomplete-item';
            b.setAttribute('data-pick-asset', '');
            b.setAttribute('data-id', a.id);
            b.setAttribute('data-name', a.name);
            b.textContent = a.name + ' (' + String(a.type || '').replace(/_/g, ' ') + ')';
            results.appendChild(b);
          });
        })
        .catch(function () { /* network error: leave the list empty */ });
    }, 250);
  });

  // ---- saved searches -----------------------------------------------------------
  function action(el, e) {
    var kind = el.getAttribute('data-action');
    if (kind === 'save-search') { e.preventDefault(); saveSearch(el); }
    else if (kind === 'delete-search') { e.preventDefault(); deleteSearch(el); }
  }

  function saveSearch(el) {
    var name = window.prompt('Name this search');
    if (!name) return;
    var shared = window.confirm('Make this search visible to everyone?\nOK = shared, Cancel = just for me');
    fetch('/api/saved-searches/', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: name, path: el.getAttribute('data-path'),
        query_string: el.getAttribute('data-query') || '', shared: shared,
      }),
    }).then(function (r) {
      if (r.ok) location.reload(); else notify('error', 'Could not save the search (' + r.status + ').');
    }).catch(function () { notify('error', 'Network error while saving the search.'); });
  }

  function deleteSearch(el) {
    if (!window.confirm('Delete saved search?')) return;
    fetch('/api/saved-searches/' + el.getAttribute('data-id'), { method: 'DELETE', credentials: 'same-origin' })
      .then(function (r) {
        if (r.ok) { var li = el.closest('li'); if (li) li.remove(); }
        else notify('error', 'Could not delete the saved search (' + r.status + ').');
      }).catch(function () { notify('error', 'Network error while deleting the saved search.'); });
  }
})();
