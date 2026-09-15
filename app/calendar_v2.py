from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from .auth import current_user
from .calculation_engine import canonical_city, norm_text
from .db import get_db
from .models import (
    CalculationLine,
    Employee,
    FinancialAdjustment,
    HolidayRule,
    Occurrence,
    PaymentCalculation,
    PaymentPeriod,
    PointOfSale,
    PromoterRoute,
)
from .occurrence_v2 import OccurrenceDocument
from .route_v2 import EmployeeRoute, RouteStop


BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
router = APIRouter()

WEEKDAYS = [(0, "Seg"), (1, "Ter"), (2, "Qua"), (3, "Qui"), (4, "Sex"), (5, "Sáb"), (6, "Dom")]
WEEKDAY_FIELDS = {
    0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday",
    4: "friday", 5: "saturday", 6: "sunday",
}
MONTH_NAMES = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def _month_shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _parse_year_month(raw: str | None) -> tuple[int, int]:
    today = date.today()
    if not raw:
        return today.year, today.month
    try:
        year, month = [int(x) for x in raw.split("-", 1)]
        if not 1 <= month <= 12:
            raise ValueError
        return year, month
    except Exception:
        return today.year, today.month


def _can_register_occurrence(user) -> bool:
    department = (getattr(getattr(user, "department", None), "code", "") or "").upper()
    return user.role in {"ADMIN", "BENEFICIOS"} or department in {"SISTEMAS", "AJUDA_CUSTOS", "RH"}


def _can_edit_route(user) -> bool:
    department = (getattr(getattr(user, "department", None), "code", "") or "").upper()
    return user.role in {"ADMIN", "BENEFICIOS"} or department in {"SISTEMAS", "AJUDA_CUSTOS", "COMERCIAL"}


def _holiday_map(db: Session, employee: Employee, first: date, last: date):
    uf = norm_text(employee.work_state)
    city = canonical_city(employee.work_city)
    result: dict[date, list[HolidayRule]] = {}
    rows = db.query(HolidayRule).filter(
        HolidayRule.active == True,
        HolidayRule.date >= first,
        HolidayRule.date <= last,
    ).all()
    for row in rows:
        applies = (
            row.scope_type == "NATIONAL"
            or (row.scope_type == "STATE" and row.uf == uf)
            or (row.scope_type == "MUNICIPAL" and row.uf == uf and row.city_norm == city)
        )
        if applies:
            result.setdefault(row.date, []).append(row)
    return result


def _occurrence_map(db: Session, employee_id: int, first: date, last: date):
    rows = db.query(Occurrence).filter(
        Occurrence.employee_id == employee_id,
        Occurrence.status != "CANCELADA",
        Occurrence.start_date <= last,
        or_(Occurrence.end_date == None, Occurrence.end_date >= first),
    ).order_by(Occurrence.start_date, Occurrence.id).all()
    result: dict[date, list[Occurrence]] = {}
    for row in rows:
        if not row.start_date:
            continue
        start = max(row.start_date, first)
        end = min(row.end_date or row.start_date, last)
        current = start
        while current <= end:
            result.setdefault(current, []).append(row)
            current = date.fromordinal(current.toordinal() + 1)
    ids = [row.id for row in rows]
    docs: dict[int, list[OccurrenceDocument]] = {}
    if ids:
        for doc in db.query(OccurrenceDocument).filter(OccurrenceDocument.occurrence_id.in_(ids)).order_by(OccurrenceDocument.created_at).all():
            docs.setdefault(doc.occurrence_id, []).append(doc)
    return result, docs, rows


def _native_routes(db: Session, employee_id: int, first: date, last: date):
    routes = db.query(EmployeeRoute).filter(
        EmployeeRoute.employee_id == employee_id,
        EmployeeRoute.status != "CANCELADO",
        EmployeeRoute.valid_from <= last,
        or_(EmployeeRoute.valid_to == None, EmployeeRoute.valid_to >= first),
    ).order_by(EmployeeRoute.valid_from).all()
    route_ids = [row.id for row in routes]
    stops = db.query(RouteStop).filter(RouteStop.route_id.in_(route_ids)).order_by(RouteStop.route_id, RouteStop.weekday, RouteStop.sequence).all() if route_ids else []
    pdv_ids = {row.pdv_id for row in stops}
    pdvs = {row.id: row for row in db.query(PointOfSale).filter(PointOfSale.id.in_(pdv_ids)).all()} if pdv_ids else {}
    stops_by_route_day: dict[tuple[int, int], list[dict]] = {}
    for stop in stops:
        pdv = pdvs.get(stop.pdv_id)
        stops_by_route_day.setdefault((stop.route_id, stop.weekday), []).append({
            "stop": stop,
            "pdv": pdv,
            "name": pdv.nome_pdv if pdv else f"PDV #{stop.pdv_id}",
            "address": pdv.endereco if pdv else None,
            "source": "QS",
        })
    return routes, stops_by_route_day


def _legacy_route_rows(db: Session, employee: Employee):
    return db.query(PromoterRoute).filter(
        or_(PromoterRoute.employee_id == employee.id, PromoterRoute.promoter_name == employee.name)
    ).order_by(PromoterRoute.id).all()


def _route_for_day(day: date, native_routes, stops_by_route_day, legacy_rows):
    applicable = [
        route for route in native_routes
        if route.valid_from <= day and (route.valid_to is None or route.valid_to >= day)
    ]
    if applicable:
        route = sorted(applicable, key=lambda x: (x.valid_from, x.id), reverse=True)[0]
        return {
            "source": "QS",
            "route": route,
            "stops": stops_by_route_day.get((route.id, day.weekday()), []),
        }

    field = WEEKDAY_FIELDS[day.weekday()]
    stops = []
    for row in legacy_rows:
        raw = getattr(row, field, None)
        if raw and str(raw).strip():
            stops.append({
                "stop": row,
                "pdv": None,
                "name": row.pdv_name,
                "address": row.address,
                "city": row.city,
                "uf": row.uf,
                "source": "PUZZLE_LEGADO",
            })
    return {"source": "PUZZLE_LEGADO" if stops else None, "route": None, "stops": stops}


def _calculation_lines_map(db: Session, employee_id: int, first: date, last: date):
    calcs = db.query(PaymentCalculation).join(PaymentPeriod).filter(
        PaymentCalculation.employee_id == employee_id,
        PaymentPeriod.end_date >= first,
        PaymentPeriod.start_date <= last,
    ).all()
    calc_ids = [row.id for row in calcs]
    result: dict[date, list[CalculationLine]] = {}
    if calc_ids:
        rows = db.query(CalculationLine).filter(
            CalculationLine.calculation_id.in_(calc_ids),
            CalculationLine.line_date >= first,
            CalculationLine.line_date <= last,
        ).order_by(CalculationLine.line_date, CalculationLine.sequence).all()
        for row in rows:
            if row.line_date:
                result.setdefault(row.line_date, []).append(row)
    return result, calcs


def _period_adjustments(db: Session, employee_id: int, first: date, last: date):
    periods = db.query(PaymentPeriod).filter(
        PaymentPeriod.end_date >= first,
        PaymentPeriod.start_date <= last,
    ).order_by(PaymentPeriod.start_date).all()
    period_ids = [row.id for row in periods]
    rows = db.query(FinancialAdjustment).filter(
        FinancialAdjustment.employee_id == employee_id,
        FinancialAdjustment.payment_period_id.in_(period_ids),
    ).order_by(FinancialAdjustment.created_at.desc()).all() if period_ids else []
    return rows, {row.id: row for row in periods}


def _build_calendar(db: Session, employee: Employee, year: int, month: int):
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    holidays = _holiday_map(db, employee, first, last)
    occurrences, docs, occurrence_rows = _occurrence_map(db, employee.id, first, last)
    native_routes, native_stops = _native_routes(db, employee.id, first, last)
    legacy_routes = _legacy_route_rows(db, employee)
    calc_lines, calculations = _calculation_lines_map(db, employee.id, first, last)

    weeks = []
    day_lookup = {}
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        items = []
        for day in week:
            route = _route_for_day(day, native_routes, native_stops, legacy_routes) if day.month == month else {"source": None, "route": None, "stops": []}
            item = {
                "date": day,
                "in_month": day.month == month,
                "today": day == date.today(),
                "holidays": holidays.get(day, []),
                "occurrences": occurrences.get(day, []),
                "route_source": route["source"],
                "route": route["route"],
                "route_stops": route["stops"],
                "calculation_lines": calc_lines.get(day, []),
            }
            if day.month == month:
                day_lookup[day.isoformat()] = item
            items.append(item)
        weeks.append(items)
    return weeks, day_lookup, docs, occurrence_rows, calculations, native_routes


@router.get("/colaboradores/{employee_id}", response_class=HTMLResponse)
def employee_detail_v2(employee_id: int, request: Request, mes: str | None = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    employee = db.query(Employee).options(joinedload(Employee.company)).filter(Employee.id == employee_id).first()
    if not employee:
        return RedirectResponse(url="/colaboradores", status_code=303)

    year, month = _parse_year_month(mes)
    weeks, day_lookup, docs, occurrence_rows, calculations, native_routes = _build_calendar(db, employee, year, month)
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    adjustments, periods = _period_adjustments(db, employee.id, first, last)

    py, pm = _month_shift(year, month, -1)
    ny, nm = _month_shift(year, month, 1)
    current_events = [
        row for row in occurrence_rows
        if row.start_date and row.start_date <= date.today() <= (row.end_date or row.start_date)
    ]

    return templates.TemplateResponse("employee_detail_v2.html", {
        "request": request,
        "employee": employee,
        "calendar_weeks": weeks,
        "calendar_days": day_lookup,
        "calendar_month": f"{MONTH_NAMES[month]}/{year}",
        "calendar_value": f"{year:04d}-{month:02d}",
        "prev_month": f"{py:04d}-{pm:02d}",
        "next_month": f"{ny:04d}-{nm:02d}",
        "weekdays": WEEKDAYS,
        "occurrences": occurrence_rows,
        "occurrence_docs": docs,
        "calculations": sorted(calculations, key=lambda x: x.id, reverse=True),
        "adjustments": adjustments,
        "periods": periods,
        "current_events": current_events,
        "native_routes": native_routes,
        "can_register_occurrence": _can_register_occurrence(user),
        "can_edit_route": _can_edit_route(user),
    })


def install_calendar_v2() -> None:
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "calendar_v2_installed", False):
        return

    legacy_path = "/colaboradores/{employee_id}"
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) != legacy_path
    ]
    app.include_router(router)
    app.state.calendar_v2_installed = True
