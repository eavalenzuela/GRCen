// Inline "Add Relationship" form on the asset detail page.
//
// Replaces a broken htmx form that (a) htmx-swapped the JSON /api/assets/search
// response into a div as raw text, with no way to pick a target, and (b) POSTed
// form-urlencoded to /api/relationships/, which expects JSON -> silent 422.
//
// This does a live JSON search, lets the user click a result to set the hidden
// target id, then POSTs JSON (like graph.js) and surfaces success/errors.
(function () {
    const form = document.getElementById('add-relationship-form');
    if (!form) return;

    const search = document.getElementById('rel-target-search');
    const results = document.getElementById('target-results');
    const targetId = document.getElementById('target_asset_id');
    const status = document.getElementById('rel-form-status');

    let timer = null;
    search.addEventListener('input', function () {
        targetId.value = '';  // typing invalidates any prior pick
        clearTimeout(timer);
        const q = search.value.trim();
        if (q.length < 2) { results.innerHTML = ''; return; }
        timer = setTimeout(function () {
            fetch('/api/assets/search?q=' + encodeURIComponent(q), { credentials: 'same-origin' })
                .then(function (r) { return r.ok ? r.json() : []; })
                .then(function (items) {
                    results.innerHTML = '';
                    items.slice(0, 8).forEach(function (a) {
                        const item = document.createElement('div');
                        item.className = 'autocomplete-item';
                        item.style.cssText = 'padding:0.35rem 0.5rem;cursor:pointer;';
                        item.textContent = a.name + ' (' + a.type + ')';
                        item.addEventListener('click', function () {
                            targetId.value = a.id;
                            search.value = a.name;
                            results.innerHTML = '';
                            status.textContent = '';
                        });
                        results.appendChild(item);
                    });
                })
                .catch(function () { results.innerHTML = ''; });
        }, 300);
    });

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        status.textContent = '';
        if (!targetId.value) {
            status.textContent = 'Pick a target asset from the search results first.';
            return;
        }
        const relType = form.querySelector('[name=relationship_type]').value.trim();
        if (!relType) { status.textContent = 'Enter a relationship type.'; return; }

        fetch('/api/relationships/', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source_asset_id: form.dataset.sourceId,
                target_asset_id: targetId.value,
                relationship_type: relType,
                description: form.querySelector('[name=description]').value,
            }),
        })
            .then(function (r) {
                if (!r.ok) {
                    return r.json().then(function (b) {
                        throw new Error(detailMessage(b) || ('HTTP ' + r.status));
                    });
                }
                return r.json();
            })
            .then(function () { location.reload(); })
            .catch(function (err) {
                status.textContent = 'Could not add relationship: ' + err.message;
            });
    });

    function detailMessage(body) {
        if (!body || !body.detail) return '';
        if (typeof body.detail === 'string') return body.detail;
        if (Array.isArray(body.detail) && body.detail[0]) return body.detail[0].msg || '';
        return '';
    }
})();
