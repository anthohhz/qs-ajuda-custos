from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import audit, current_user, require_role
from .calculation_engine import canonical_city, process_period
from .db import Base, get_db
from .holiday_2026_data import UF_NAMES
from .models import BaseTariff, HolidayRule, PaymentCalculation, PaymentPeriod


BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
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


def _admin(request: Request, db: Session):
    return require_role(request, db, {"ADMIN"})


def _parse_date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except Exception:
        return None


def _parse_money(raw, default=None):
    text = str(raw or "").strip().replace("R$", "").replace(" ", "")
    if not text:
        return default
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def _reprocess_open_periods(db: Session, start: date | None = None, end: date | None = None) -> int:
    query = db.query(PaymentPeriod).filter(PaymentPeriod.status.notin_(["not_processed", "closed"]))
    if start:
        query = query.filter(PaymentPeriod.end_date >= start)
    if end:
        query = query.filter(PaymentPeriod.start_date <= end)
    count = 0
    for period in query.order_by(PaymentPeriod.start_date).all():
        if db.query(PaymentCalculation).filter(PaymentCalculation.period_id == period.id).count() == 0:
            continue
        process_period(db, period)
        count += 1
    return count


def _city_from_form(db: Session, city_id_raw, uf: str) -> City | None:
    try:
        city_id = int(city_id_raw)
    except Exception:
        return None
    return db.query(City).filter(
        City.id == city_id,
        City.uf == uf,
        City.active == True,
    ).first()


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


@router.get("/tarifas/nova", response_class=HTMLResponse)
def tariff_new_v2(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    return templates.TemplateResponse("tariff_new_v2.html", {"request": request, "ufs": UF_NAMES})


@router.post("/tarifas/nova")
async def tariff_create_v2(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    uf = (form.get("uf") or "").strip().upper()
    amount = _parse_money(form.get("amount"))
    if uf not in UF_NAMES or amount is None or amount < 0:
        return RedirectResponse(url="/tarifas/nova?erro=dados", status_code=303)
    city = _city_from_form(db, form.get("city_id"), uf)
    if not city:
        return RedirectResponse(url="/tarifas/nova?erro=cidade", status_code=303)

    max_legacy = db.query(BaseTariff.legacy_id).order_by(BaseTariff.legacy_id.desc()).first()
    legacy_id = (max_legacy[0] if max_legacy else 0) + 1
    valid_from = _parse_date(form.get("valid_from"))
    tariff = BaseTariff(
        legacy_id=legacy_id,
        city=city.name,
        city_norm=canonical_city(city.name),
        uf=uf,
        modal=(form.get("modal") or "ONIBUS").strip().upper() or "ONIBUS",
        amount=amount,
        valid_from=valid_from,
        notes=(form.get("notes") or "").strip() or None,
        original_value=str(form.get("amount") or ""),
        source_sheet="QS",
        source_row=None,
        status="OK_MANUAL",
        review_reason=None,
    )
    db.add(tariff)
    db.flush()
    audit(db, user, "CRIAR_TARIFA", "TARIFA", tariff.id, f"Tarifa {city.name}/{uf} criada")
    db.commit()
    _reprocess_open_periods(db, valid_from, None)
    return RedirectResponse(url=f"/tarifas?uf={uf}&saved=1", status_code=303)


@router.get("/feriados/novo", response_class=HTMLResponse)
def holiday_new_v2(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    return templates.TemplateResponse("holiday_new_v2.html", {"request": request, "ufs": UF_NAMES})


@router.post("/feriados/novo")
async def holiday_create_v2(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    hdate = _parse_date(form.get("date"))
    name = (form.get("name") or "").strip()
    scope = (form.get("scope_type") or "MUNICIPAL").strip().upper()
    category = (form.get("category") or "FERIADO").strip().upper()
    uf = (form.get("uf") or "").strip().upper() or None
    city = None

    if not hdate or not name or scope not in {"NATIONAL", "STATE", "MUNICIPAL"}:
        return RedirectResponse(url="/feriados/novo?erro=dados", status_code=303)
    if category not in {"FERIADO", "PONTO_FACULTATIVO"}:
        return RedirectResponse(url="/feriados/novo?erro=dados", status_code=303)

    if scope == "NATIONAL":
        uf = None
    elif scope == "STATE":
        if uf not in UF_NAMES:
            return RedirectResponse(url="/feriados/novo?erro=uf", status_code=303)
    else:
        if uf not in UF_NAMES:
            return RedirectResponse(url="/feriados/novo?erro=uf", status_code=303)
        city = _city_from_form(db, form.get("city_id"), uf)
        if not city:
            return RedirectResponse(url="/feriados/novo?erro=cidade", status_code=303)

    city_norm = canonical_city(city.name) if city else None
    duplicate_query = db.query(HolidayRule).filter(
        HolidayRule.active == True,
        HolidayRule.date == hdate,
        HolidayRule.scope_type == scope,
        HolidayRule.category == category,
    )
    if uf is None:
        duplicate_query = duplicate_query.filter(HolidayRule.uf == None)
    else:
        duplicate_query = duplicate_query.filter(HolidayRule.uf == uf)
    if city_norm is None:
        duplicate_query = duplicate_query.filter(HolidayRule.city_norm == None)
    else:
        duplicate_query = duplicate_query.filter(HolidayRule.city_norm == city_norm)
    if duplicate_query.first():
        return RedirectResponse(url="/feriados/novo?erro=duplicado", status_code=303)

    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    holiday = HolidayRule(
        source_key=f"MANUAL_QS:{stamp}",
        date=hdate,
        name=name,
        scope_type=scope,
        uf=uf,
        city=city.name if city else None,
        city_norm=city_norm,
        category=category,
        affects_calculation=(form.get("affects_calculation") == "on"),
        source="MANUAL_QS",
        source_ref="Cadastrado no QS",
        notes=(form.get("notes") or "").strip() or None,
        active=True,
    )
    db.add(holiday)
    db.flush()
    audit(db, user, "CRIAR_FERIADO", "FERIADO", holiday.id, f"{name} cadastrado para {scope}")
    db.commit()
    _reprocess_open_periods(db, hdate, hdate)
    return RedirectResponse(url=f"/feriados?ano={hdate.year}&uf={uf or ''}&saved=1", status_code=303)


def install_locality_v2() -> None:
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "locality_v2_installed", False):
        return
    legacy_paths = {"/tarifas/nova", "/feriados/novo"}
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) not in legacy_paths
    ]
    app.include_router(router)
    app.state.locality_v2_installed = True
