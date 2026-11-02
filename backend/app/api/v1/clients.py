import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import client_ip, require
from app.db import get_db
from app.models import Client, User, Website
from app.schemas.websites import ClientCreate, ClientOut, ClientUpdate
from app.services.audit_log import record

router = APIRouter(prefix="/clients", tags=["clients"])


def _out(db: Session, c: Client) -> ClientOut:
    n = db.scalar(select(func.count()).select_from(Website).where(Website.client_id == c.id,
                                                                   Website.status != "archived"))
    return ClientOut.model_validate(c).model_copy(update={"website_count": n or 0})


def _get(db: Session, client_id: uuid.UUID) -> Client:
    c = db.get(Client, client_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return c


@router.get("", response_model=list[ClientOut])
def list_clients(db: Session = Depends(get_db), _: User = Depends(require("client.read"))):
    return [_out(db, c) for c in db.scalars(select(Client).order_by(Client.name))]


@router.post("", response_model=ClientOut, status_code=201)
def create_client(body: ClientCreate, request: Request, db: Session = Depends(get_db),
                  actor: User = Depends(require("client.manage"))):
    if db.scalar(select(Client).where(Client.slug == body.slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A client with this slug already exists.")
    c = Client(**body.model_dump())
    db.add(c)
    db.flush()
    record(db, actor.id, "client.create", "client", c.id, client_ip(request))
    db.commit()
    return _out(db, c)


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: uuid.UUID, db: Session = Depends(get_db), _: User = Depends(require("client.read"))):
    return _out(db, _get(db, client_id))


@router.patch("/{client_id}", response_model=ClientOut)
def update_client(client_id: uuid.UUID, body: ClientUpdate, request: Request, db: Session = Depends(get_db),
                  actor: User = Depends(require("client.manage"))):
    c = _get(db, client_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name", "") is None:
        raise HTTPException(422, "name cannot be empty.")
    for k, v in changes.items():
        setattr(c, k, v)
    record(db, actor.id, "client.update", "client", c.id, client_ip(request), fields=sorted(changes))
    db.commit()
    return _out(db, c)
