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

function initGraph(assetId, depth) {
    fetch(`/api/graph/${assetId}?depth=${depth}`)
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
                                return ele.data('id') === assetId ? 3 : 1;
                            },
                            'border-color': '#1e293b',
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

            // Click a node to navigate — unless we're in link mode.
            cyInstance.on('tap', 'node', function(evt) {
                if (linkMode) return;
                const nodeId = evt.target.data('id');
                if (nodeId !== assetId) {
                    window.location.href = `/assets/${nodeId}`;
                }
            });

            if (linkMode) {
                cleanupLinkMode = enableDragLink(cyInstance, assetId, depth);
            }
        });
}

function toggleLinkMode() {
    linkMode = !linkMode;
    const btn = document.getElementById('link-mode-btn');
    const status = document.getElementById('link-status');
    if (linkMode) {
        btn.textContent = 'Cancel Link Mode';
        btn.classList.add('btn-primary');
        status.textContent = 'Drag from a source node onto a target node to create a relationship.';
        status.style.display = 'inline';
        if (cyInstance) cleanupLinkMode = enableDragLink(cyInstance, currentAssetId(), currentDepth());
    } else {
        btn.textContent = 'Link Mode';
        btn.classList.remove('btn-primary');
        status.style.display = 'none';
        if (cleanupLinkMode) { cleanupLinkMode(); cleanupLinkMode = null; }
    }
}

function currentAssetId() {
    // Pulled off the body for simplicity — set by the template.
    return document.body.dataset.assetId;
}

function currentDepth() {
    const sel = document.getElementById('depth-select');
    return sel ? parseInt(sel.value) : 1;
}

// ---------------------------------------------------------------------------
// Drag-to-link
// ---------------------------------------------------------------------------

function enableDragLink(cy, assetId, depth) {
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
        createLink(src.id(), target.id(), assetId, depth);
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
    const status = document.getElementById('link-status');
    if (status) status.textContent = msg;
}

function createLink(sourceId, targetId, assetId, depth) {
    const relType = prompt('Relationship type:');
    if (!relType) {
        updateStatus('Cancelled.');
        return;
    }
    const description = prompt('Description (optional):') || '';
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
        updateStatus('Created. Drag another pair or click Cancel Link Mode to navigate.');
        // Re-render to show the new edge. The mode stays on so the user can
        // keep building relationships without a second click.
        initGraph(assetId, depth);
    })
    .catch(err => {
        updateStatus('Error: ' + err.message);
    });
}
