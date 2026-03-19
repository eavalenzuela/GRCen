from uuid import UUID

import asyncpg

from grcen.schemas.graph import GraphEdge, GraphNode, GraphResponse


async def get_asset_graph(
    pool: asyncpg.Pool, asset_id: UUID, depth: int = 1
) -> GraphResponse:
    depth = min(depth, 3)

    nodes_rows = await pool.fetch(
        """
        WITH RECURSIVE graph AS (
            SELECT a.id AS asset_id, a.name, a.type::text AS asset_type, 0 AS lvl
            FROM assets a WHERE a.id = $1

            UNION

            SELECT
                CASE
                    WHEN r.source_asset_id = g.asset_id THEN r.target_asset_id
                    ELSE r.source_asset_id
                END AS asset_id,
                a2.name,
                a2.type::text AS asset_type,
                g.lvl + 1 AS lvl
            FROM graph g
            JOIN relationships r ON r.source_asset_id = g.asset_id
                                 OR r.target_asset_id = g.asset_id
            JOIN assets a2 ON a2.id = CASE
                WHEN r.source_asset_id = g.asset_id THEN r.target_asset_id
                ELSE r.source_asset_id
            END
            WHERE g.lvl < $2
        )
        SELECT DISTINCT asset_id, name, asset_type FROM graph
        """,
        asset_id,
        depth,
    )

    edges_rows = await pool.fetch(
        """
        WITH RECURSIVE graph AS (
            SELECT a.id AS asset_id, 0 AS lvl
            FROM assets a WHERE a.id = $1

            UNION

            SELECT
                CASE
                    WHEN r.source_asset_id = g.asset_id THEN r.target_asset_id
                    ELSE r.source_asset_id
                END AS asset_id,
                g.lvl + 1 AS lvl
            FROM graph g
            JOIN relationships r ON r.source_asset_id = g.asset_id
                                 OR r.target_asset_id = g.asset_id
            JOIN assets a2 ON a2.id = CASE
                WHEN r.source_asset_id = g.asset_id THEN r.target_asset_id
                ELSE r.source_asset_id
            END
            WHERE g.lvl < $2
        ),
        node_ids AS (SELECT DISTINCT asset_id FROM graph)
        SELECT r.id, r.source_asset_id, r.target_asset_id, r.relationship_type
        FROM relationships r
        WHERE r.source_asset_id IN (SELECT asset_id FROM node_ids)
          AND r.target_asset_id IN (SELECT asset_id FROM node_ids)
        """,
        asset_id,
        depth,
    )

    nodes = [
        GraphNode(id=str(r["asset_id"]), label=r["name"], type=r["asset_type"])
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
    return GraphResponse(nodes=nodes, edges=edges)
