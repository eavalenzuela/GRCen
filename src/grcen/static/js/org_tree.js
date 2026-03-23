/**
 * org_tree.js — Canvas-based tree renderer with orthogonal (right-angle) connectors.
 *
 * Expects graph data in {nodes: [{id, label, type, subtitle}], edges: [{source, target}]}
 * where source is the parent and target is the child.
 */

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
    vendor: '#a855f7',
    control: '#0ea5e9',
    incident: '#e11d48',
    framework: '#65a30d',
};

const NODE_W = 160;
const NODE_H = 56;
const H_GAP = 32;
const V_GAP = 60;
const PADDING = 40;

/* ── Tree layout engine ─────────────────────────────────────────────── */

function buildTree(data) {
    const nodeMap = {};
    data.nodes.forEach(n => {
        nodeMap[n.id] = { ...n, children: [], x: 0, y: 0, width: 0 };
    });

    const childIds = new Set();
    data.edges.forEach(e => {
        if (nodeMap[e.source] && nodeMap[e.target]) {
            nodeMap[e.source].children.push(nodeMap[e.target]);
            childIds.add(e.target);
        }
    });

    // Roots are nodes that are never a target
    const roots = data.nodes
        .filter(n => !childIds.has(n.id))
        .map(n => nodeMap[n.id]);

    if (roots.length === 0 && data.nodes.length > 0) {
        // Fallback: if cyclic, just pick first node
        roots.push(nodeMap[data.nodes[0].id]);
    }

    return roots;
}

/**
 * First pass: compute subtree widths bottom-up.
 */
function computeWidths(node) {
    if (node.children.length === 0) {
        node.width = NODE_W;
        return;
    }
    node.children.forEach(computeWidths);
    const childrenWidth = node.children.reduce((sum, c) => sum + c.width, 0)
        + H_GAP * (node.children.length - 1);
    node.width = Math.max(NODE_W, childrenWidth);
}

/**
 * Second pass: assign x, y positions top-down.
 */
function assignPositions(node, x, y) {
    node.x = x + node.width / 2 - NODE_W / 2;
    node.y = y;

    if (node.children.length === 0) return;

    const childrenWidth = node.children.reduce((sum, c) => sum + c.width, 0)
        + H_GAP * (node.children.length - 1);
    let cx = x + (node.width - childrenWidth) / 2;
    const cy = y + NODE_H + V_GAP;

    node.children.forEach(child => {
        assignPositions(child, cx, cy);
        cx += child.width + H_GAP;
    });
}

/* ── Canvas rendering ────────────────────────────────────────────────── */

function collectAll(roots) {
    const all = [];
    function walk(node) {
        all.push(node);
        node.children.forEach(walk);
    }
    roots.forEach(walk);
    return all;
}

function drawOrthogonalEdge(ctx, parent, child) {
    const px = parent.x + NODE_W / 2;
    const py = parent.y + NODE_H;
    const cx = child.x + NODE_W / 2;
    const cy = child.y;
    const midY = py + V_GAP / 2;

    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(px, midY);
    ctx.lineTo(cx, midY);
    ctx.lineTo(cx, cy);
    ctx.stroke();
}

function drawNode(ctx, node, dpr) {
    const x = node.x;
    const y = node.y;
    const color = TYPE_COLORS[node.type] || '#94a3b8';

    // Card shadow
    ctx.fillStyle = 'rgba(0,0,0,0.06)';
    roundRect(ctx, x + 2, y + 2, NODE_W, NODE_H, 6);
    ctx.fill();

    // Card background
    ctx.fillStyle = '#ffffff';
    roundRect(ctx, x, y, NODE_W, NODE_H, 6);
    ctx.fill();

    // Left color stripe
    ctx.fillStyle = color;
    roundRectLeft(ctx, x, y, 5, NODE_H, 6);
    ctx.fill();

    // Card border
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 1;
    roundRect(ctx, x, y, NODE_W, NODE_H, 6);
    ctx.stroke();

    // Label text
    ctx.fillStyle = '#1e293b';
    ctx.font = `600 ${12}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    ctx.textBaseline = 'top';
    const label = truncate(ctx, node.label, NODE_W - 20);
    ctx.fillText(label, x + 14, y + 10);

    // Subtitle
    if (node.subtitle) {
        ctx.fillStyle = '#64748b';
        ctx.font = `${11}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
        const sub = truncate(ctx, node.subtitle, NODE_W - 20);
        ctx.fillText(sub, x + 14, y + 28);
    }

    // Type badge
    ctx.fillStyle = color;
    ctx.font = `500 ${9}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    const typeLabel = node.type.replace(/_/g, ' ');
    ctx.fillText(typeLabel, x + 14, y + NODE_H - 14);
}

function truncate(ctx, text, maxW) {
    if (ctx.measureText(text).width <= maxW) return text;
    let t = text;
    while (t.length > 0 && ctx.measureText(t + '…').width > maxW) {
        t = t.slice(0, -1);
    }
    return t + '…';
}

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r);
    ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h);
    ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
    ctx.closePath();
}

function roundRectLeft(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w, y);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r);
    ctx.arcTo(x, y, x + r, y, r);
    ctx.closePath();
}

/* ── Pan & zoom ──────────────────────────────────────────────────────── */

function setupInteraction(canvas, ctx, roots, allNodes, dpr) {
    let offsetX = 0, offsetY = 0;
    let scale = 1;
    let dragging = false;
    let lastX, lastY;

    function draw() {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
        ctx.save();
        ctx.translate(offsetX, offsetY);
        ctx.scale(scale, scale);

        // Draw edges
        ctx.strokeStyle = '#94a3b8';
        ctx.lineWidth = 1.5;
        function drawEdges(node) {
            node.children.forEach(child => {
                drawOrthogonalEdge(ctx, node, child);
                drawEdges(child);
            });
        }
        roots.forEach(drawEdges);

        // Draw nodes
        allNodes.forEach(n => drawNode(ctx, n, dpr));

        ctx.restore();
    }

    canvas.addEventListener('mousedown', e => {
        dragging = true;
        lastX = e.clientX;
        lastY = e.clientY;
        canvas.style.cursor = 'grabbing';
    });

    canvas.addEventListener('mousemove', e => {
        if (!dragging) return;
        offsetX += e.clientX - lastX;
        offsetY += e.clientY - lastY;
        lastX = e.clientX;
        lastY = e.clientY;
        draw();
    });

    canvas.addEventListener('mouseup', () => {
        dragging = false;
        canvas.style.cursor = 'grab';
    });

    canvas.addEventListener('mouseleave', () => {
        dragging = false;
        canvas.style.cursor = 'grab';
    });

    canvas.addEventListener('wheel', e => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        const zoom = e.deltaY < 0 ? 1.1 : 0.9;
        const newScale = Math.max(0.1, Math.min(3, scale * zoom));

        // Zoom toward cursor
        offsetX = mx - (mx - offsetX) * (newScale / scale);
        offsetY = my - (my - offsetY) * (newScale / scale);
        scale = newScale;
        draw();
    }, { passive: false });

    // Click to navigate to asset
    canvas.addEventListener('click', e => {
        if (dragging) return;
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left - offsetX) / scale;
        const my = (e.clientY - rect.top - offsetY) / scale;

        for (const node of allNodes) {
            if (mx >= node.x && mx <= node.x + NODE_W &&
                my >= node.y && my <= node.y + NODE_H) {
                window.location.href = `/assets/${node.id}`;
                return;
            }
        }
    });

    // Show pointer on hoverable nodes
    canvas.addEventListener('mousemove', e => {
        if (dragging) return;
        const rect = canvas.getBoundingClientRect();
        const mx = (e.clientX - rect.left - offsetX) / scale;
        const my = (e.clientY - rect.top - offsetY) / scale;
        let over = false;
        for (const node of allNodes) {
            if (mx >= node.x && mx <= node.x + NODE_W &&
                my >= node.y && my <= node.y + NODE_H) {
                over = true;
                break;
            }
        }
        canvas.style.cursor = over ? 'pointer' : 'grab';
    });

    canvas.style.cursor = 'grab';

    // Center the tree initially
    if (allNodes.length > 0) {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        allNodes.forEach(n => {
            minX = Math.min(minX, n.x);
            minY = Math.min(minY, n.y);
            maxX = Math.max(maxX, n.x + NODE_W);
            maxY = Math.max(maxY, n.y + NODE_H);
        });
        const treeW = maxX - minX;
        const treeH = maxY - minY;
        const canvasW = canvas.width / dpr;
        const canvasH = canvas.height / dpr;

        // Fit to view — allow scaling up for small trees, cap at 1.5×
        scale = Math.min(1.5, canvasW / (treeW + PADDING * 2), canvasH / (treeH + PADDING * 2));
        offsetX = (canvasW - treeW * scale) / 2 - minX * scale;
        offsetY = PADDING - minY * scale;
    }

    draw();
    return draw;
}

/* ── Public API ──────────────────────────────────────────────────────── */

function renderOrgTree(canvasId, data) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const container = canvas.parentElement;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;

    // Size canvas to container — read computed style to get CSS-defined height
    const cs = window.getComputedStyle(container);
    const w = parseInt(cs.width, 10) || 900;
    const h = parseInt(cs.height, 10) || 600;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';

    if (!data.nodes || data.nodes.length === 0) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.fillStyle = '#64748b';
        ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No data to display.', canvas.width / dpr / 2, canvas.height / dpr / 2);
        return;
    }

    const roots = buildTree(data);

    // Layout each root tree side by side
    roots.forEach(r => computeWidths(r));
    let xOffset = PADDING;
    roots.forEach(root => {
        assignPositions(root, xOffset, PADDING);
        xOffset += root.width + H_GAP * 2;
    });

    const allNodes = collectAll(roots);
    setupInteraction(canvas, ctx, roots, allNodes, dpr);
}
