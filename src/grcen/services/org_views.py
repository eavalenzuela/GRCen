"""Service functions for Org Views: org chart, business structure, product view."""

from uuid import UUID

import asyncpg

from grcen.schemas.graph import GraphEdge, GraphNode, GraphResponse


async def get_org_chart(pool: asyncpg.Pool) -> GraphResponse:
    """Build an org chart from Person assets linked by 'manages' relationships.

    Nodes are Person assets. Edges are 'manages' relationships between persons.
    The owner_id field on a Person (pointing to another Person) is also treated
    as a 'managed by' edge.
    """
    nodes_rows = await pool.fetch(
        """
        SELECT a.id, a.name, a.type::text AS asset_type,
               a.owner_id,
               COALESCE(a.metadata->>'title', a.description, '') AS subtitle
        FROM assets a
        WHERE a.type = 'person' AND a.status = 'active'
        ORDER BY a.name
        """
    )

    edges_rows = await pool.fetch(
        """
        SELECT r.id, r.source_asset_id, r.target_asset_id, r.relationship_type
        FROM relationships r
        JOIN assets src ON src.id = r.source_asset_id AND src.type = 'person'
        JOIN assets tgt ON tgt.id = r.target_asset_id AND tgt.type = 'person'
        WHERE r.relationship_type = 'manages'
        """
    )

    person_ids = {r["id"] for r in nodes_rows}

    nodes = [
        GraphNode(
            id=str(r["id"]),
            label=r["name"],
            type=r["asset_type"],
            subtitle=r["subtitle"],
        )
        for r in nodes_rows
    ]

    edges = [
        GraphEdge(
            id=str(r["id"]),
            source=str(r["source_asset_id"]),
            target=str(r["target_asset_id"]),
            label=r["relationship_type"],
        )
        for r in edges_rows
    ]

    # Also treat owner_id as a manages edge (owner manages this person)
    seen_edges = {(str(e.source), str(e.target)) for e in edges}
    for r in nodes_rows:
        if r["owner_id"] and r["owner_id"] in person_ids:
            pair = (str(r["owner_id"]), str(r["id"]))
            if pair not in seen_edges:
                edges.append(
                    GraphEdge(
                        id=f"owner-{r['id']}",
                        source=str(r["owner_id"]),
                        target=str(r["id"]),
                        label="manages",
                    )
                )
                seen_edges.add(pair)

    return GraphResponse(nodes=nodes, edges=edges)


async def get_business_structure(pool: asyncpg.Pool) -> GraphResponse:
    """Build OU hierarchy from Organizational Unit assets.

    Uses 'parent_of' relationships and owner_id links between OUs.
    Owner Person assets are included as leaf annotations.
    """
    ou_rows = await pool.fetch(
        """
        SELECT a.id, a.name, a.type::text AS asset_type,
               a.owner_id,
               owner.name AS owner_name,
               owner.type::text AS owner_type
        FROM assets a
        LEFT JOIN assets owner ON owner.id = a.owner_id
        WHERE a.type = 'organizational_unit' AND a.status = 'active'
        ORDER BY a.name
        """
    )

    edges_rows = await pool.fetch(
        """
        SELECT r.id, r.source_asset_id, r.target_asset_id, r.relationship_type
        FROM relationships r
        JOIN assets src ON src.id = r.source_asset_id AND src.type = 'organizational_unit'
        JOIN assets tgt ON tgt.id = r.target_asset_id AND tgt.type = 'organizational_unit'
        WHERE r.relationship_type IN ('parent_of', 'owns', 'manages')
        """
    )

    ou_ids = {r["id"] for r in ou_rows}

    nodes = [
        GraphNode(
            id=str(r["id"]),
            label=r["name"],
            type=r["asset_type"],
            subtitle=r["owner_name"] or "",
        )
        for r in ou_rows
    ]

    edges = [
        GraphEdge(
            id=str(r["id"]),
            source=str(r["source_asset_id"]),
            target=str(r["target_asset_id"]),
            label=r["relationship_type"],
        )
        for r in edges_rows
    ]

    # owner_id between OUs as a parent edge
    seen_edges = {(str(e.source), str(e.target)) for e in edges}
    for r in ou_rows:
        if r["owner_id"] and r["owner_id"] in ou_ids:
            pair = (str(r["owner_id"]), str(r["id"]))
            if pair not in seen_edges:
                edges.append(
                    GraphEdge(
                        id=f"owner-{r['id']}",
                        source=str(r["owner_id"]),
                        target=str(r["id"]),
                        label="parent_of",
                    )
                )
                seen_edges.add(pair)

    return GraphResponse(nodes=nodes, edges=edges)


async def get_product_view(pool: asyncpg.Pool, product_id: UUID) -> GraphResponse:
    """Build a product-centric tree.

    Above the product: Owner (Person) and Owner's OU.
    Below the product: related teams, vendors, systems, etc.
    """
    # Get the product itself
    product = await pool.fetchrow(
        """
        SELECT a.id, a.name, a.type::text AS asset_type, a.owner_id
        FROM assets a
        WHERE a.id = $1 AND a.type = 'product'
        """,
        product_id,
    )
    if not product:
        return GraphResponse(nodes=[], edges=[])

    nodes_map: dict[str, GraphNode] = {}
    edges_list: list[GraphEdge] = []

    # Product node
    nodes_map[str(product["id"])] = GraphNode(
        id=str(product["id"]),
        label=product["name"],
        type=product["asset_type"],
    )

    # Owner chain (above product)
    if product["owner_id"]:
        owner = await pool.fetchrow(
            """
            SELECT a.id, a.name, a.type::text AS asset_type, a.owner_id
            FROM assets a WHERE a.id = $1
            """,
            product["owner_id"],
        )
        if owner:
            owner_id_str = str(owner["id"])
            nodes_map[owner_id_str] = GraphNode(
                id=owner_id_str,
                label=owner["name"],
                type=owner["asset_type"],
            )
            edges_list.append(
                GraphEdge(
                    id=f"owns-{product['id']}",
                    source=owner_id_str,
                    target=str(product["id"]),
                    label="owns",
                )
            )
            # Owner's OU (via relationships or owner_id)
            ou_row = await pool.fetchrow(
                """
                SELECT a.id, a.name, a.type::text AS asset_type
                FROM assets a
                JOIN relationships r ON (
                    (r.source_asset_id = $1 AND r.target_asset_id = a.id
                     AND r.relationship_type = 'member_of')
                    OR
                    (r.target_asset_id = $1 AND r.source_asset_id = a.id
                     AND r.relationship_type IN ('manages', 'owns', 'parent_of'))
                )
                WHERE a.type = 'organizational_unit'
                LIMIT 1
                """,
                owner["id"],
            )
            if not ou_row and owner["owner_id"]:
                ou_row = await pool.fetchrow(
                    """
                    SELECT a.id, a.name, a.type::text AS asset_type
                    FROM assets a WHERE a.id = $1 AND a.type = 'organizational_unit'
                    """,
                    owner["owner_id"],
                )
            if ou_row:
                ou_id_str = str(ou_row["id"])
                nodes_map[ou_id_str] = GraphNode(
                    id=ou_id_str,
                    label=ou_row["name"],
                    type=ou_row["asset_type"],
                )
                edges_list.append(
                    GraphEdge(
                        id=f"ou-{owner['id']}",
                        source=ou_id_str,
                        target=owner_id_str,
                        label="has member",
                    )
                )

    # Related assets below product (via relationships)
    related_rows = await pool.fetch(
        """
        SELECT a.id, a.name, a.type::text AS asset_type,
               r.id AS rel_id, r.relationship_type,
               r.source_asset_id, r.target_asset_id
        FROM relationships r
        JOIN assets a ON a.id = CASE
            WHEN r.source_asset_id = $1 THEN r.target_asset_id
            ELSE r.source_asset_id
        END
        WHERE (r.source_asset_id = $1 OR r.target_asset_id = $1)
          AND a.status = 'active'
        """,
        product_id,
    )

    for r in related_rows:
        rid = str(r["id"])
        if rid not in nodes_map:
            nodes_map[rid] = GraphNode(
                id=rid,
                label=r["name"],
                type=r["asset_type"],
            )
            edges_list.append(
                GraphEdge(
                    id=str(r["rel_id"]),
                    source=str(product["id"]),
                    target=rid,
                    label=r["relationship_type"],
                )
            )

    return GraphResponse(
        nodes=list(nodes_map.values()),
        edges=edges_list,
    )


async def list_products(pool: asyncpg.Pool) -> list[dict]:
    """Return all active products for the dropdown selector."""
    rows = await pool.fetch(
        """
        SELECT id, name FROM assets
        WHERE type = 'product' AND status = 'active'
        ORDER BY name
        """
    )
    return [{"id": str(r["id"]), "name": r["name"]} for r in rows]
