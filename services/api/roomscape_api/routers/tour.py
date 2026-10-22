"""Tour editing: room labels, nodes (registered viewpoints / panoramas), edges and hotspots.

Nodes are created only from real reconstruction output (registered photos or
uploaded panoramas). Edges and hotspots are created by the owner; nothing is
inferred from missing data.
"""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import current_user, load_project
from ..errors import APIError, not_found
from ..models import Hotspot, RoomLabel, TourEdge, TourNode, User
from ..signing import file_url

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["tour"])


def tour_payload(db: Session, project_id: uuid.UUID, share_id=None) -> dict:
    rooms = db.scalars(select(RoomLabel).where(RoomLabel.project_id == project_id).order_by(RoomLabel.sort, RoomLabel.name)).all()
    nodes = db.scalars(select(TourNode).where(TourNode.project_id == project_id).order_by(TourNode.sort, TourNode.ref)).all()
    edges = db.scalars(select(TourEdge).where(TourEdge.project_id == project_id)).all()
    hs = db.scalars(select(Hotspot).where(Hotspot.project_id == project_id)).all()
    return {
        "rooms": [{"id": str(r.id), "name": r.name, "description": r.description, "sort": r.sort,
                   "cover_node_id": str(r.cover_node_id) if r.cover_node_id else None,
                   "dimensions_note": r.dimensions_note} for r in rooms],
        "nodes": [{"id": str(n.id), "kind": n.kind, "ref": n.ref, "label": n.label, "sort": n.sort, "source": n.source,
                   "room_id": str(n.room_id) if n.room_id else None, "job_id": str(n.job_id) if n.job_id else None,
                   "upload_id": str(n.upload_id) if n.upload_id else None,
                   "image_url": file_url("upload", n.upload_id, "web", share_id) if n.upload_id else None,
                   "thumb_url": file_url("upload", n.upload_id, "thumb", share_id) if n.upload_id else None} for n in nodes],
        "edges": [{"id": str(e.id), "from": str(e.from_node_id), "to": str(e.to_node_id), "label": e.label,
                   "yaw": e.yaw, "pitch": e.pitch} for e in edges],
        "hotspots": [{"id": str(h.id), "node_id": str(h.node_id) if h.node_id else None,
                      "room_id": str(h.room_id) if h.room_id else None, "kind": h.kind, "title": h.title,
                      "description": h.description, "position": h.position} for h in hs],
    }


@router.get("/tour")
def get_tour(project_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user)
    return tour_payload(db, p.id)


class RoomIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    sort: int = 0
    dimensions_note: str = Field(default="", max_length=120)


@router.post("/rooms", status_code=201)
def create_room(project_id: uuid.UUID, body: RoomIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    r = RoomLabel(project_id=p.id, **body.model_dump())
    db.add(r)
    db.commit()
    return {"id": str(r.id)}


@router.patch("/rooms/{room_id}")
def update_room(project_id: uuid.UUID, room_id: uuid.UUID, body: RoomIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    r = db.get(RoomLabel, room_id)
    if not r or r.project_id != p.id:
        raise not_found("Room")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    db.commit()
    return {"ok": True}


@router.delete("/rooms/{room_id}")
def delete_room(project_id: uuid.UUID, room_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    r = db.get(RoomLabel, room_id)
    if not r or r.project_id != p.id:
        raise not_found("Room")
    for n in db.scalars(select(TourNode).where(TourNode.room_id == r.id)):
        n.room_id = None
    db.delete(r)
    db.commit()
    return {"ok": True}


class NodePatch(BaseModel):
    room_id: uuid.UUID | None = None
    label: str | None = Field(default=None, max_length=120)
    sort: int | None = None


@router.patch("/nodes/{node_id}")
def update_node(project_id: uuid.UUID, node_id: uuid.UUID, body: NodePatch, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    n = db.get(TourNode, node_id)
    if not n or n.project_id != p.id:
        raise not_found("Viewpoint")
    data = body.model_dump(exclude_unset=True)
    if data.get("room_id"):
        r = db.get(RoomLabel, data["room_id"])
        if not r or r.project_id != p.id:
            raise APIError(422, "INVALID_ROOM", "Room does not belong to this project.")
    for k, v in data.items():
        setattr(n, k, v)
    db.commit()
    return {"ok": True}


class EdgeIn(BaseModel):
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    label: str = Field(default="", max_length=80)
    yaw: float | None = Field(default=None, ge=-math.pi * 2, le=math.pi * 2)
    pitch: float | None = Field(default=None, ge=-math.pi / 2, le=math.pi / 2)


def validate_edge(db: Session, project_id: uuid.UUID, body: EdgeIn) -> None:
    if body.from_node_id == body.to_node_id:
        raise APIError(422, "INVALID_EDGE", "A connection must join two different viewpoints.")
    for nid in (body.from_node_id, body.to_node_id):
        n = db.get(TourNode, nid)
        if not n or n.project_id != project_id:
            raise APIError(422, "INVALID_EDGE", "Both viewpoints must belong to this project.")


@router.post("/edges", status_code=201)
def create_edge(project_id: uuid.UUID, body: EdgeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    validate_edge(db, p.id, body)
    if db.scalar(select(TourEdge).where(TourEdge.from_node_id == body.from_node_id, TourEdge.to_node_id == body.to_node_id)):
        raise APIError(409, "EDGE_EXISTS", "These viewpoints are already connected.")
    e = TourEdge(project_id=p.id, created_by=user.id, **body.model_dump())
    db.add(e)
    db.commit()
    return {"id": str(e.id)}


@router.delete("/edges/{edge_id}")
def delete_edge(project_id: uuid.UUID, edge_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    e = db.get(TourEdge, edge_id)
    if not e or e.project_id != p.id:
        raise not_found("Connection")
    db.delete(e)
    db.commit()
    return {"ok": True}


class HotspotIn(BaseModel):
    node_id: uuid.UUID | None = None
    room_id: uuid.UUID | None = None
    kind: str = Field(default="feature", pattern="^(feature|appliance|window|storage|outdoor|note|navigation)$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    position: dict

    @field_validator("position")
    @classmethod
    def _pos(cls, v: dict) -> dict:
        if "xyz" in v:
            xyz = v["xyz"]
            if not (isinstance(xyz, list) and len(xyz) == 3 and all(isinstance(c, (int, float)) and math.isfinite(c) and abs(c) < 1e6 for c in xyz)):
                raise ValueError("xyz must be three finite numbers")
            return {"xyz": [float(c) for c in xyz]}
        if "yaw" in v and "pitch" in v:
            yaw, pitch = float(v["yaw"]), float(v["pitch"])
            if not (-math.pi * 2 <= yaw <= math.pi * 2 and -math.pi / 2 <= pitch <= math.pi / 2):
                raise ValueError("yaw/pitch out of range")
            return {"yaw": yaw, "pitch": pitch}
        if "u" in v and "v" in v:  # normalised position on a registered photo
            u, vv = float(v["u"]), float(v["v"])
            if not (0 <= u <= 1 and 0 <= vv <= 1):
                raise ValueError("u/v must be within 0..1")
            return {"u": u, "v": vv}
        raise ValueError("position needs xyz, yaw/pitch or u/v")


@router.post("/hotspots", status_code=201)
def create_hotspot(project_id: uuid.UUID, body: HotspotIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    if body.node_id:
        n = db.get(TourNode, body.node_id)
        if not n or n.project_id != p.id:
            raise APIError(422, "INVALID_NODE", "Viewpoint does not belong to this project.")
    if body.room_id:
        r = db.get(RoomLabel, body.room_id)
        if not r or r.project_id != p.id:
            raise APIError(422, "INVALID_ROOM", "Room does not belong to this project.")
    h = Hotspot(project_id=p.id, created_by=user.id, **body.model_dump())
    db.add(h)
    audit.record(db, "hotspot.created", actor=user.id, org_id=p.org_id, target_type="hotspot", target_id=h.id)
    db.commit()
    return {"id": str(h.id)}


@router.delete("/hotspots/{hotspot_id}")
def delete_hotspot(project_id: uuid.UUID, hotspot_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = load_project(db, project_id, user, "EDITOR")
    h = db.get(Hotspot, hotspot_id)
    if not h or h.project_id != p.id:
        raise not_found("Hotspot")
    db.delete(h)
    db.commit()
    return {"ok": True}
