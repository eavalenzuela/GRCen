// Single source of truth for asset-type node colours, shared by the Cytoscape
// graph (graph.js) and the Org Views tree renderer (org_tree.js) so the two
// visualisations always agree. Load this before either of those scripts.
window.TYPE_COLORS = {
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
    finding: '#fb923c',
};
