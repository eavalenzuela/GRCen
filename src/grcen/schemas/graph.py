from pydantic import BaseModel


class GraphNode(BaseModel):
    id: str
    label: str
    type: str
    subtitle: str = ""


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
