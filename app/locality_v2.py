from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import current_user
from .db import Base, get_db


router = APIRouter()


def normalize_city(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\s*/\s*[A-Za-z]{2}$", "", text)
    text = re.sub(r"\s+", " ", text).strip().upper()
    aliases = {
        "SAO LUIZ": "SAO LUIS",
        "ARCO VERDE": "ARCOVERDE",
        "ARCO-VERDE": "ARCOVERDE",
        "CABO SANTO AGOSTINHO": "CABO DE SANTO AGOSTINHO",
        "ALAGOINHA": "ALAGOINHAS",
        "PALMEIRAS DOS INDIOS": "PALMEIRA DOS INDIOS",
        "JUAZEIRO DA BAHIA": "JUAZEIRO",
    }
    return aliases.get(text, text)


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (UniqueConstraint("uf", "name_norm", name="uq_city_uf_norm"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    name_norm: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(40), default="OPERACAO_QS", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


@router.get("/api/localidades/cidades")
def list_cities(request: Request, uf: str, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    code = (uf or "").strip().upper()
    if len(code) != 2:
        return []
    rows = db.query(City).filter(City.uf == code, City.active == True).order_by(City.name).all()
    return [{"id": row.id, "name": row.name, "uf": row.uf} for row in rows]


def install_locality_v2() -> None:
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "locality_v2_installed", False):
        return
    app.include_router(router)
    app.state.locality_v2_installed = True
