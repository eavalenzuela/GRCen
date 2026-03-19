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

            const cy = cytoscape({
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

            cy.on('tap', 'node', function(evt) {
                const nodeId = evt.target.data('id');
                if (nodeId !== assetId) {
                    window.location.href = `/assets/${nodeId}`;
                }
            });
        });
}
