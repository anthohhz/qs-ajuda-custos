from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, cast, or_
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import audit, current_user, require_role
from .db import Base, get_db
from .models import Employee, PointOfSale


BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
router = APIRouter()

WEEKDAYS = [
    (0, "Segunda"), (1, "Terça"), (2, "Quarta"), (3, "Quinta"),
    (4, "Sexta"), (5, "Sábado"), (6, "Domingo"),
]


class EmployeeRoute(Base):
    __tablename__ = "employee_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="ATIVO", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(40), default="QS", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class RouteStop(Base):
    __tablename__ = "route_stops"
    __table_args__ = (UniqueConstraint("route_id", "weekday", "sequence", name="uq_route_day_sequence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("employee_routes.id"), nullable=False, index=True)
    pdv_id: Mapped[int] = mapped_column(ForeignKey("pdvs.id"), nullable=False, index=True)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


def _operator(request: Request, db: Session):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    department = getattr(getattr(user, "department", None), "code", "") or ""
    if user.role in {"ADMIN", "BENEFICIOS"} or department.upper() in {"SISTEMAS", "AJUDA_CUSTOS", "COMERCIAL"}:
        return user
    raise HTTPException(status_code=403, detail="Sem permissão para alterar roteiros.")


def _viewer(request: Request, db: Session):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    return user


def _parse_date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except Exception:
        return None


def _route_overlaps(db: Session, employee_id: int, start: date, end: date | None, ignore_id: int | None = None) -> bool:
    query = db.query(EmployeeRoute).filter(
        EmployeeRoute.employee_id == employee_id,
        EmployeeRoute.status != "CANCELADO",
    )
    if ignore_id:
        query = query.filter(EmployeeRoute.id != ignore_id)
    for row in query.all():
        row_end = row.valid_to or date.max
        new_end = end or date.max
        if start <= row_end and row.valid_from <= new_end:
            return True
    return False


def _route_context(db: Session, route: EmployeeRoute):
    employee = db.get(Employee, route.employee_id)
    stops = db.query(RouteStop).filter(RouteStop.route_id == route.id).order_by(RouteStop.weekday, RouteStop.sequence).all()
    pdv_ids = {s.pdv_id for s in stops}
    pdvs = {p.id: p for p in db.query(PointOfSale).filter(PointOfSale.id.in_(pdv_ids)).all()} if pdv_ids else {}
    by_day = {idx: [] for idx, _ in WEEKDAYS}
    for stop in stops:
        by_day.setdefault(stop.weekday, []).append((stop, pdvs.get(stop.pdv_id)))
    return employee, stops, by_day


@router.get("/roteiros", response_class=HTMLResponse)
def routes_index(request: Request, q: str = "", db: Session = Depends(get_db)):
    _viewer(request, db)
    query = db.query(EmployeeRoute)
    employee_ids = None
    if q.strip():
        matches = db.query(Employee.id).filter(Employee.name.ilike(f"%{q.strip()}%")).all()
        employee_ids = [x[0] for x in matches]
        query = query.filter(EmployeeRoute.employee_id.in_(employee_ids)) if employee_ids else query.filter(EmployeeRoute.id == -1)
    rows = query.order_by(EmployeeRoute.valid_from.desc(), EmployeeRoute.id.desc()).all()
    employees = {e.id: e for e in db.query(Employee).filter(Employee.id.in_({r.employee_id for r in rows})).all()} if rows else {}
    stop_counts = dict(db.query(RouteStop.route_id, __import__('sqlalchemy').func.count(RouteStop.id)).group_by(RouteStop.route_id).all())
    return templates.TemplateResponse("routes_v2.html", {
        "request": request, "rows": rows, "employees": employees, "stop_counts": stop_counts, "q": q,
    })


@router.get("/roteiros/novo", response_class=HTMLResponse)
def route_new(request: Request, employee_id: int | None = None, db: Session = Depends(get_db)):
    _operator(request, db)
    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).order_by(Employee.name).all()
    return templates.TemplateResponse("route_new_v2.html", {
        "request": request, "employees": employees, "selected_employee_id": employee_id,
    })


@router.post("/roteiros/novo")
async def route_create(request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    form = await request.form()
    try:
        employee_id = int(form.get("employee_id"))
    except Exception:
        return RedirectResponse(url="/roteiros/novo?erro=colaborador", status_code=303)
    employee = db.get(Employee, employee_id)
    start = _parse_date(form.get("valid_from"))
    end = _parse_date(form.get("valid_to"))
    if not employee or not start or (end and end < start):
        return RedirectResponse(url="/roteiros/novo?erro=dados", status_code=303)
    if _route_overlaps(db, employee.id, start, end):
        return RedirectResponse(url=f"/roteiros/novo?employee_id={employee.id}&erro=sobreposicao", status_code=303)

    row = EmployeeRoute(
        employee_id=employee.id,
        valid_from=start,
        valid_to=end,
        status="ATIVO",
        source="QS",
        notes=(form.get("notes") or "").strip() or None,
        created_by_user_id=user.id,
    )
    db.add(row); db.flush()
    audit(db, user, "CRIAR_ROTEIRO", "ROTEIRO", row.id, f"Roteiro criado para {employee.name} com vigência a partir de {start.strftime('%d/%m/%Y')}")
    db.commit()
    return RedirectResponse(url=f"/roteiros/{row.id}?created=1", status_code=303)


@router.get("/roteiros/{route_id}", response_class=HTMLResponse)
def route_detail(route_id: int, request: Request, db: Session = Depends(get_db)):
    _viewer(request, db)
    route = db.get(EmployeeRoute, route_id)
    if not route:
        return RedirectResponse(url="/roteiros", status_code=303)
    employee, stops, by_day = _route_context(db, route)
    return templates.TemplateResponse("route_detail_v2.html", {
        "request": request, "route": route, "employee": employee, "stops": stops,
        "by_day": by_day, "weekdays": WEEKDAYS,
    })


@router.get("/api/roteiros/pdvs")
def search_pdvs(request: Request, q: str = "", db: Session = Depends(get_db)):
    _viewer(request, db)
    text = q.strip()
    if len(text) < 2:
        return []
    term = f"%{text}%"
    rows = db.query(PointOfSale).filter(or_(
        PointOfSale.nome_pdv.ilike(term),
        PointOfSale.bandeira.ilike(term),
        PointOfSale.rede.ilike(term),
        PointOfSale.endereco.ilike(term),
        cast(PointOfSale.codigo_pdv, String).ilike(term),
    )).order_by(PointOfSale.nome_pdv).limit(30).all()
    return [{
        "id": p.id,
        "codigo": p.codigo_pdv,
        "nome": p.nome_pdv,
        "rede": p.rede or "",
        "endereco": p.endereco or "",
    } for p in rows]


@router.post("/roteiros/{route_id}/paradas")
async def route_add_stop(route_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    route = db.get(EmployeeRoute, route_id)
    if not route or route.status != "ATIVO":
        return RedirectResponse(url="/roteiros?erro=roteiro", status_code=303)
    form = await request.form()
    try:
        pdv_id = int(form.get("pdv_id")); weekday = int(form.get("weekday")); sequence = int(form.get("sequence") or 1)
    except Exception:
        return RedirectResponse(url=f"/roteiros/{route_id}?erro=dados", status_code=303)
    pdv = db.get(PointOfSale, pdv_id)
    if not pdv or weekday not in range(7) or sequence < 1:
        return RedirectResponse(url=f"/roteiros/{route_id}?erro=dados", status_code=303)
    if db.query(RouteStop).filter(RouteStop.route_id == route.id, RouteStop.weekday == weekday, RouteStop.sequence == sequence).first():
        return RedirectResponse(url=f"/roteiros/{route_id}?erro=ordem", status_code=303)
    if db.query(RouteStop).filter(RouteStop.route_id == route.id, RouteStop.weekday == weekday, RouteStop.pdv_id == pdv.id).first():
        return RedirectResponse(url=f"/roteiros/{route_id}?erro=duplicado", status_code=303)

    stop = RouteStop(route_id=route.id, pdv_id=pdv.id, weekday=weekday, sequence=sequence, notes=(form.get("notes") or "").strip() or None)
    db.add(stop); db.flush()
    audit(db, user, "ADICIONAR_PDV_ROTEIRO", "ROTEIRO", route.id, f"{pdv.nome_pdv} adicionado ao roteiro na posição {sequence}")
    db.commit()
    return RedirectResponse(url=f"/roteiros/{route.id}?saved=1", status_code=303)


@router.post("/roteiros/{route_id}/paradas/{stop_id}/remover")
def route_remove_stop(route_id: int, stop_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    route = db.get(EmployeeRoute, route_id)
    stop = db.query(RouteStop).filter(RouteStop.id == stop_id, RouteStop.route_id == route_id).first()
    if route and stop and route.status == "ATIVO":
        pdv = db.get(PointOfSale, stop.pdv_id)
        audit(db, user, "REMOVER_PDV_ROTEIRO", "ROTEIRO", route.id, f"{pdv.nome_pdv if pdv else 'PDV'} removido do roteiro")
        db.delete(stop); db.commit()
    return RedirectResponse(url=f"/roteiros/{route_id}", status_code=303)


@router.post("/roteiros/{route_id}/encerrar")
async def route_close(route_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    route = db.get(EmployeeRoute, route_id)
    if not route or route.status != "ATIVO":
        return RedirectResponse(url=f"/roteiros/{route_id}", status_code=303)
    form = await request.form()
    valid_to = _parse_date(form.get("valid_to"))
    if not valid_to or valid_to < route.valid_from:
        return RedirectResponse(url=f"/roteiros/{route_id}?erro=vigencia", status_code=303)
    route.valid_to = valid_to
    route.status = "ENCERRADO"
    audit(db, user, "ENCERRAR_ROTEIRO", "ROTEIRO", route.id, f"Roteiro encerrado em {valid_to.strftime('%d/%m/%Y')}")
    db.commit()
    return RedirectResponse(url=f"/roteiros/{route_id}?closed=1", status_code=303)


def install_route_v2() -> None:
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "route_v2_installed", False):
        return
    app.include_router(router)
    app.state.route_v2_installed = True
