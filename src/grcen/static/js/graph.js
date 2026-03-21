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
};

let linkMode = false;
let linkSource = null;
let cyInstance = null;

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
                    {
                        selector: 'node.link-selected',
                        style: {
                            'border-width': 4,
                            'border-color': '#2563eb',
                            'border-style': 'dashed',
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

            cyInstance.on('tap', 'node', function(evt) {
                const nodeId = evt.target.data('id');
                if (linkMode) {
                    handleLinkClick(evt.target, assetId, depth);
                } else if (nodeId !== assetId) {
                    window.location.href = `/assets/${nodeId}`;
                }
            });
        });
}

function toggleLinkMode() {
    linkMode = !linkMode;
    linkSource = null;
    const btn = document.getElementById('link-mode-btn');
    const status = document.getElementById('link-status');
    if (linkMode) {
        btn.textContent = 'Cancel Link Mode';
        btn.classList.add('btn-primary');
        status.textContent = 'Click the source node...';
        status.style.display = 'inline';
        if (cyInstance) cyInstance.nodes().removeClass('link-selected');
    } else {
        btn.textContent = 'Link Mode';
        btn.classList.remove('btn-primary');
        status.style.display = 'none';
        if (cyInstance) cyInstance.nodes().removeClass('link-selected');
    }
}

function handleLinkClick(node, assetId, depth) {
    const status = document.getElementById('link-status');
    if (!linkSource) {
        linkSource = node;
        node.addClass('link-selected');
        status.textContent = 'Now click the target node...';
    } else {
        const sourceId = linkSource.data('id');
        const targetId = node.data('id');
        if (sourceId === targetId) {
            status.textContent = 'Cannot link a node to itself. Click a different target...';
            return;
        }
        const relType = prompt('Relationship type:');
        if (!relType) {
            linkSource.removeClass('link-selected');
            linkSource = null;
            status.textContent = 'Cancelled. Click the source node...';
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
            toggleLinkMode();
            initGraph(assetId, depth);
        })
        .catch(err => {
            status.textContent = 'Error: ' + err.message;
            linkSource.removeClass('link-selected');
            linkSource = null;
        });
    }
}
