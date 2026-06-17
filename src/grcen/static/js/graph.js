const TYPE_COLORS = {
    person: '#3b82f6',
    policy: '#8b5cf6',
    product: '#ec4899',
    system: '#f59e0b',
    device: '#10b981',
    data_category: '#06b6d4',
    audit: '#ef4444',
    requirement: '#84cc16',
    process: '#f97316',
    intellectual_property: '#6366f1',
    risk: '#dc2626',
    organizational_unit: '#14b8a6',
    vendor: '#0ea5e9',
    control: '#22c55e',
    incident: '#f43f5e',
    framework: '#a855f7',
};

let cyInstance = null;
let linkMode = false;
let cleanupLinkMode = null;
// The render currently on screen, so drag-to-link can refresh after creating
// an edge regardless of which view (per-asset or whole-org) we're in.
let lastRenderFn = null;
let centerId = null;
// Asset types currently hidden via the legend filter (applies across renders).
const hiddenTypes = new Set();

// Per-asset N-hop subgraph centered on one node.
function initGraph(assetId, depth) {
    renderGraph(`/api/graph/${assetId}?depth=${depth}`, assetId);
}

// Whole-organization graph (no center node), capped at `limit` nodes.
function initOrgGraph(limit) {
    renderGraph(`/api/graph?limit=${limit}`, null);
}

function renderGraph(url, focusId) {
    lastRenderFn = () => renderGraph(url, focusId);
    centerId = focusId;
    if (cyInstance) { cyInstance.destroy(); cyInstance = null; }
    fetch(url)
        .then(r => r.json())
        .then(data => {
            const elements = [];

            data.nodes.forEach(n => {
                elements.push({
                    data: {
                        id: n.id,
                        label: n.label,
                        type: n.type,
                    }
                });
            });

            data.edges.forEach(e => {
                elements.push({
                    data: {
                        id: e.id,
                        source: e.source,
                        target: e.target,
                        label: e.label,
                    }
                });
            });

            cyInstance = cytoscape({
                container: document.getElementById('cy'),
                elements: elements,
                style: [
                    {
                        selector: 'node',
                        style: {
                            'label': 'data(label)',
                            'text-valign': 'bottom',
                            'text-margin-y': 8,
                            'font-size': '12px',
                            'width': 40,
                            'height': 40,
                            'background-color': function(ele) {
                                return TYPE_COLORS[ele.data('type')] || '#94a3b8';
                            },
                            'border-width': function(ele) {
                                return ele.data('id') === centerId ? 3 : 1;
                            },
                            'border-color': '#1e293b',
                        }
                    },
                    // A node whose neighbours have been pulled in (expand-in-place).
                    {
                        selector: 'node.expanded',
                        style: {
                            'border-width': 3,
                            'border-color': '#16a34a',
                        }
                    },
                    // Target-hover highlight while dragging a new link.
                    {
                        selector: 'node.link-target',
                        style: {
                            'border-width': 4,
                            'border-color': '#16a34a',
                        }
                    },
                    // Invisible ghost node that follows the cursor during a drag.
                    {
                        selector: 'node.ghost-node',
                        style: {
                            'width': 1,
                            'height': 1,
                            'background-opacity': 0,
                            'border-opacity': 0,
                            'label': '',
                            'events': 'no',  // don't fire hover events on the ghost
                        }
                    },
                    {
                        selector: 'edge',
                        style: {
                            'label': 'data(label)',
                            'font-size': '10px',
                            'text-rotation': 'autorotate',
                            'curve-style': 'bezier',
                            'target-arrow-shape': 'triangle',
                            'line-color': '#94a3b8',
                            'target-arrow-color': '#94a3b8',
                            'width': 1.5,
                        }
                    },
                    {
                        selector: 'edge.ghost-edge',
                        style: {
                            'line-color': '#2563eb',
                            'line-style': 'dashed',
                            'target-arrow-color': '#2563eb',
                            'width': 2,
                            'label': '',
                            'events': 'no',
                        }
                    }
                ],
                layout: {
                    name: 'cose',
                    animate: true,
                    animationDuration: 500,
                    nodeRepulsion: 8000,
                    idealEdgeLength: 120,
                }
            });

            // Tap a node to select it (panel offers Expand / Open). Double-tap
            // expands its neighbours in place. Navigation moved to the panel so
            // walking the graph no longer reloads the page and loses position.
            cyInstance.on('tap', 'node', function(evt) {
                if (linkMode) return;
                const node = evt.target;
                renderNodePanel(node.data('id'), node.data('label'));
            });
            cyInstance.on('dbltap', 'node', function(evt) {
                if (linkMode) return;
                expandNode(evt.target.data('id'));
            });
            // Tap blank canvas clears the selection panel.
            cyInstance.on('tap', function(evt) {
                if (evt.target === cyInstance) hideNodePanel();
            });

            applyTypeFilter();

            if (linkMode) {
                cleanupLinkMode = enableDragLink(cyInstance);
            }
        });
}

// Pull a node's direct neighbours into the current graph without reloading.
function expandNode(nodeId) {
    fetch(`/api/graph/${nodeId}?depth=1`)
        .then(r => r.json())
        .then(data => {
            if (!cyInstance) return;
            const src = cyInstance.getElementById(nodeId);
            const base = (src && src.position) ? src.position() : { x: 0, y: 0 };
            let added = 0;
            data.nodes.forEach(n => {
                if (cyInstance.getElementById(n.id).empty()) {
                    cyInstance.add({
                        group: 'nodes',
                        data: { id: n.id, label: n.label, type: n.type },
                        position: {
                            x: base.x + (Math.random() * 160 - 80),
                            y: base.y + (Math.random() * 160 - 80),
                        },
                    });
                    added++;
                }
            });
            data.edges.forEach(e => {
                if (cyInstance.getElementById(e.id).empty()) {
                    cyInstance.add({
                        group: 'edges',
                        data: { id: e.id, source: e.source, target: e.target, label: e.label },
                    });
                }
            });
            if (src) src.addClass('expanded');
            applyTypeFilter();
            if (added > 0) {
                // randomize:false + fit:false keeps existing nodes roughly in
                // place so the user doesn't lose their bearings.
                cyInstance.layout({
                    name: 'cose', animate: true, animationDuration: 400,
                    randomize: false, fit: false, nodeRepulsion: 8000, idealEdgeLength: 120,
                }).run();
            }
            updateStatus(added > 0 ? `Expanded ${added} neighbour(s).` : 'No new neighbours.');
        })
        .catch(() => updateStatus('Failed to expand node.'));
}

// Floating panel for the selected node: name + Expand + Open actions.
function renderNodePanel(id, label) {
    const panel = document.getElementById('graph-node-panel');
    if (!panel) return;
    panel.innerHTML = '';
    const name = document.createElement('span');
    name.className = 'gnp-name';
    name.textContent = label;            // textContent: node labels are user data
    const expand = document.createElement('button');
    expand.type = 'button';
    expand.className = 'btn btn-small';
    expand.textContent = 'Expand';
    expand.addEventListener('click', () => expandNode(id));
    const open = document.createElement('a');
    open.className = 'btn btn-small';
    open.href = '/assets/' + id;
    open.textContent = 'Open ↗';
    panel.appendChild(name);
    panel.appendChild(expand);
    panel.appendChild(open);
    panel.style.display = 'flex';
}

function hideNodePanel() {
    const panel = document.getElementById('graph-node-panel');
    if (panel) panel.style.display = 'none';
}

// Show/hide nodes (and their edges) per the legend's hiddenTypes set.
function applyTypeFilter() {
    if (!cyInstance) return;
    cyInstance.batch(function() {
        cyInstance.nodes().forEach(function(n) {
            n.style('display', hiddenTypes.has(n.data('type')) ? 'none' : 'element');
        });
        cyInstance.edges().forEach(function(e) {
            const hide = hiddenTypes.has(e.source().data('type'))
                      || hiddenTypes.has(e.target().data('type'));
            e.style('display', hide ? 'none' : 'element');
        });
    });
}

// Populate a legend (#graph-legend) with the asset-type colour key. Each entry
// is a toggle that shows/hides that type in the graph (type filter).
function renderLegend(containerId) {
    const el = document.getElementById(containerId || 'graph-legend');
    if (!el) return;
    el.innerHTML = '';
    Object.keys(TYPE_COLORS).forEach(type => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'legend-item' + (hiddenTypes.has(type) ? ' legend-off' : '');
        item.title = 'Toggle ' + type.replace(/_/g, ' ');
        const swatch = document.createElement('span');
        swatch.className = 'legend-swatch';
        swatch.style.backgroundColor = TYPE_COLORS[type];
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(type.replace(/_/g, ' ')));
        item.addEventListener('click', () => {
            if (hiddenTypes.has(type)) {
                hiddenTypes.delete(type);
                item.classList.remove('legend-off');
            } else {
                hiddenTypes.add(type);
                item.classList.add('legend-off');
            }
            applyTypeFilter();
        });
        el.appendChild(item);
    });
}

function toggleLinkMode() {
    linkMode = !linkMode;
    const btn = document.getElementById('link-mode-btn');
    if (linkMode) {
        btn.textContent = 'Cancel Link Mode';
        btn.classList.add('btn-primary');
        updateStatus('Drag from a source node onto a target node to create a relationship.');
        if (cyInstance) cleanupLinkMode = enableDragLink(cyInstance);
    } else {
        btn.textContent = 'Link Mode';
        btn.classList.remove('btn-primary');
        updateStatus('');
        hideLinkForm();
        if (cleanupLinkMode) { cleanupLinkMode(); cleanupLinkMode = null; }
    }
}

// ---------------------------------------------------------------------------
// Drag-to-link
// ---------------------------------------------------------------------------

function enableDragLink(cy) {
    let source = null;
    let ghostNode = null;
    let ghostEdge = null;
    let hoverTarget = null;

    cy.nodes().ungrabify();

    function onDown(evt) {
        if (evt.target === cy) return;
        source = evt.target;
        const pos = evt.position;
        ghostNode = cy.add({
            group: 'nodes',
            data: { id: '__ghost__' },
            position: { x: pos.x, y: pos.y },
            classes: 'ghost-node',
        });
        ghostEdge = cy.add({
            group: 'edges',
            data: { id: '__ghost_edge__', source: source.id(), target: '__ghost__' },
            classes: 'ghost-edge',
        });
    }

    function onMove(evt) {
        if (!ghostNode) return;
        ghostNode.position(evt.position);
    }

    function onOver(evt) {
        if (!source) return;
        const n = evt.target;
        if (n.id() === source.id() || n.id() === '__ghost__') return;
        if (hoverTarget) hoverTarget.removeClass('link-target');
        hoverTarget = n;
        n.addClass('link-target');
    }

    function onOut(evt) {
        if (hoverTarget && hoverTarget.id() === evt.target.id()) {
            hoverTarget.removeClass('link-target');
            hoverTarget = null;
        }
    }

    function cleanupGhosts() {
        if (ghostEdge) { ghostEdge.remove(); ghostEdge = null; }
        if (ghostNode) { ghostNode.remove(); ghostNode = null; }
        if (hoverTarget) { hoverTarget.removeClass('link-target'); hoverTarget = null; }
    }

    function onUp() {
        if (!source) return;
        const target = hoverTarget;
        const src = source;
        cleanupGhosts();
        source = null;

        if (!target || target.id() === src.id()) {
            updateStatus('Drag from a source node onto a target node to create a relationship.');
            return;
        }
        showLinkForm(src.id(), target.id());
    }

    cy.on('mousedown', 'node', onDown);
    cy.on('mousemove', onMove);
    cy.on('mouseover', 'node', onOver);
    cy.on('mouseout', 'node', onOut);
    cy.on('mouseup', onUp);

    return function cleanup() {
        cleanupGhosts();
        cy.off('mousedown', 'node', onDown);
        cy.off('mousemove', onMove);
        cy.off('mouseover', 'node', onOver);
        cy.off('mouseout', 'node', onOut);
        cy.off('mouseup', onUp);
        cy.nodes().grabify();
    };
}

function updateStatus(msg) {
    const status = document.getElementById('graph-status');
    if (status) status.textContent = msg;
}

// Load the suggested relationship-type vocabulary once into the datalist that
// the link form's type input references (canonical types ∪ types already in use).
let relTypesLoaded = false;
function ensureRelTypeSuggestions() {
    if (relTypesLoaded) return;
    relTypesLoaded = true;
    fetch('/api/relationships/types')
        .then(r => r.json())
        .then(types => {
            const dl = document.getElementById('rel-type-suggestions');
            if (!dl) return;
            dl.innerHTML = '';
            types.forEach(t => {
                const o = document.createElement('option');
                o.value = t;
                dl.appendChild(o);
            });
        })
        .catch(() => {});
}

// Inline form to create a relationship after a drag-link — replaces the old
// raw prompt() dialogs with a typed input backed by the vocabulary datalist.
function showLinkForm(sourceId, targetId) {
    const form = document.getElementById('graph-link-form');
    if (!form) return;
    ensureRelTypeSuggestions();
    const sName = cyInstance.getElementById(sourceId).data('label') || 'source';
    const tName = cyInstance.getElementById(targetId).data('label') || 'target';

    form.innerHTML = '';
    const label = document.createElement('span');
    label.className = 'glf-label';
    label.append('Link ');
    const s = document.createElement('b'); s.textContent = sName; label.append(s);
    label.append(' → ');
    const t = document.createElement('b'); t.textContent = tName; label.append(t);

    const type = document.createElement('input');
    type.id = 'glf-type';
    type.setAttribute('list', 'rel-type-suggestions');
    type.placeholder = 'Relationship type (e.g. depends_on)';
    type.autocomplete = 'off';

    const desc = document.createElement('input');
    desc.id = 'glf-desc';
    desc.placeholder = 'Description (optional)';

    const create = document.createElement('button');
    create.type = 'button';
    create.className = 'btn btn-small btn-primary';
    create.textContent = 'Create';
    create.addEventListener('click', () => {
        const rt = type.value.trim();
        if (!rt) { type.focus(); return; }
        submitLink(sourceId, targetId, rt, desc.value.trim());
    });

    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'btn btn-small';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', () => { hideLinkForm(); updateStatus('Cancelled.'); });

    form.append(label, type, desc, create, cancel);
    form.style.display = 'flex';
    type.focus();
}

function hideLinkForm() {
    const form = document.getElementById('graph-link-form');
    if (form) form.style.display = 'none';
}

function submitLink(sourceId, targetId, relType, description) {
    fetch('/api/relationships/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            source_asset_id: sourceId,
            target_asset_id: targetId,
            relationship_type: relType,
            description: description,
        }),
    })
    .then(r => {
        if (!r.ok) throw new Error('Failed to create relationship');
        return r.json();
    })
    .then(() => {
        hideLinkForm();
        updateStatus('Created. Drag another pair or click Cancel Link Mode to navigate.');
        // Re-render the current view to show the new edge. The mode stays on so
        // the user can keep building relationships without a second click.
        if (lastRenderFn) lastRenderFn();
    })
    .catch(err => {
        updateStatus('Error: ' + err.message);
    });
}
