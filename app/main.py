import calendar
import json
from pathlib import Path
from datetime import date, datetime
from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, cast, String, func
from sqlalchemy.orm import Session, joinedload

from .db import DB_PATH, DATA_DIR, MIGRATION_SOURCE, get_db
from .models import (
    HomologationDecision, CostRule, CostRuleOption,
    PaymentCycleConfig, PaymentPeriod, Employee, CalculationPolicy,
    Occurrence, PaymentCalculation, CalculationLine, BaseTariff, Holiday, HolidayRule, FinancialAdjustment,
    EmployeePolicyOverride, UserAccount, AuditLog, PointOfSale, PromoterRoute,
)
from .seed import seed, ensure_periods
from .guidance_data import DECISION_GUIDANCE
from .calculation_engine import process_period, canonical_city, norm_text
from .holiday_2026_data import UF_NAMES
from .auth import session_secret, hash_password, verify_password, current_user, require_role, audit

BASE = Path(__file__).resolve().parent
app = FastAPI(title="QS Ajuda de Custos V0.9.2")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


@app.on_event("startup")
def startup():
    seed()


PUBLIC_PATHS = {"/login", "/setup-admin"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/"):
        return await call_next(request)
    db = next(get_db())
    try:
        has_users = db.query(UserAccount).filter(UserAccount.active == True).count() > 0
        user = current_user(request, db)
        request.state.user = user
        if not has_users and path != "/setup-admin":
            return RedirectResponse(url="/setup-admin", status_code=303)
        if has_users and not user and path not in PUBLIC_PATHS:
            return RedirectResponse(url="/login", status_code=303)
        if has_users and user and path in {"/login", "/setup-admin"}:
            return RedirectResponse(url="/", status_code=303)
    finally:
        db.close()
    return await call_next(request)


# Registrado depois do middleware de autenticação para que a sessão seja criada antes dele na pilha ASGI.
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site="lax", https_only=False)


@app.get("/setup-admin", response_class=HTMLResponse)
def setup_admin(request: Request, db: Session = Depends(get_db)):
    if db.query(UserAccount).count() > 0:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("setup_admin.html", {"request": request})


@app.post("/setup-admin")
async def setup_admin_post(request: Request, db: Session = Depends(get_db)):
    if db.query(UserAccount).count() > 0:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    username = (form.get("username") or "").strip().lower()
    display_name = (form.get("display_name") or "").strip()
    password = str(form.get("password") or "")
    if len(username) < 3 or not display_name or len(password) < 8:
        return RedirectResponse(url="/setup-admin?erro=1", status_code=303)
    user = UserAccount(username=username, display_name=display_name, password_hash=hash_password(password), role="ADMIN", active=True)
    db.add(user); db.commit(); db.refresh(user)
    request.session["user_id"] = user.id
    audit(db, user, "CRIAR_ADMIN", "USUARIO", user.id, f"Administrador inicial criado: {display_name}")
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_post(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    username = (form.get("username") or "").strip().lower()
    password = str(form.get("password") or "")
    user = db.query(UserAccount).filter(UserAccount.username == username, UserAccount.active == True).first()
    if not user or not verify_password(password, user.password_hash):
        return RedirectResponse(url="/login?erro=1", status_code=303)
    request.session["user_id"] = user.id
    user.last_login_at = datetime.utcnow()
    audit(db, user, "LOGIN", "SESSAO", user.id, "Login realizado")
    db.commit()
    return RedirectResponse(url="/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


def _user(request: Request, db: Session):
    return current_user(request, db)


def _admin(request: Request, db: Session):
    return require_role(request, db, {"ADMIN"})


def _operator(request: Request, db: Session):
    return require_role(request, db, {"ADMIN", "BENEFICIOS"})


def brl(value):
    if value is None:
        return "—"
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def brdate(value):
    if not value:
        return "—"
    return value.strftime("%d/%m/%Y")


def brnum(value):
    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".replace(".", ",")


templates.env.filters["brl"] = brl
templates.env.filters["brdate"] = brdate
templates.env.filters["brnum"] = brnum


STATUS_LABELS = {
    "not_processed": "Não processada",
    "simulated": "Simulação",
    "processed": "Processada",
    "processed_with_issues": "Processada com pendências",
    "closed": "Fechada",
    "calculated": "Calculado",
    "simulation": "Simulação",
    "review": "Revisar",
    "excluded": "Fora do escopo",
}

WEEKDAYS = [
    (0, "Seg"), (1, "Ter"), (2, "Qua"), (3, "Qui"), (4, "Sex"), (5, "Sáb"), (6, "Dom")
]


def pending_query(db: Session):
    return db.query(HomologationDecision).filter(HomologationDecision.status != "approved")


def month_label(year: int, month: int) -> str:
    names = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    return f"{names[month]}/{year}"


def parse_year_month(value: str | None):
    today = date.today()
    if not value:
        return today.year, today.month
    try:
        y, m = [int(x) for x in value.split("-", 1)]
        if not (1 <= m <= 12):
            raise ValueError
        return y, m
    except Exception:
        return today.year, today.month


def _month_shift(year: int, month: int, delta: int):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def employee_calendar(db: Session, employee: Employee, year: int, month: int):
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    occs = db.query(Occurrence).filter(
        Occurrence.employee_id == employee.id, Occurrence.status == "ATIVA",
        Occurrence.start_date <= last,
        or_(Occurrence.end_date == None, Occurrence.end_date >= first),
    ).all()
    holidays = db.query(HolidayRule).filter(
        HolidayRule.active == True, HolidayRule.date >= first, HolidayRule.date <= last
    ).all()
    uf = norm_text(employee.work_state); city = canonical_city(employee.work_city)
    applicable_holidays = {}
    for h in holidays:
        applies = h.scope_type == "NATIONAL" or (h.scope_type == "STATE" and h.uf == uf) or (h.scope_type == "MUNICIPAL" and h.uf == uf and h.city_norm == city)
        if applies:
            applicable_holidays.setdefault(h.date, []).append(h)
    occ_by_day = {}
    for o in occs:
        start = max(o.start_date or first, first); end = min(o.end_date or o.start_date or start, last)
        current = start
        while current <= end:
            occ_by_day.setdefault(current, []).append(o)
            current = current.fromordinal(current.toordinal() + 1)
    weeks=[]
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        items=[]
        for d in week:
            items.append({
                "date": d, "in_month": d.month == month, "occurrences": occ_by_day.get(d, []),
                "holidays": applicable_holidays.get(d, []), "today": d == date.today(),
            })
        weeks.append(items)
    return weeks


def parse_form_date(raw):
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except Exception:
        return None


def parse_form_money(raw, default=None):
    text = str(raw or "").strip().replace("R$", "").replace(" ", "")
    if not text:
        return default
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def _signed_adjustment(row: FinancialAdjustment) -> float:
    amount = abs(float(row.amount or 0))
    return amount if row.kind in {"REEMBOLSO", "CREDITO"} else -amount


def period_stats(db: Session, period_id: int):
    rows = db.query(PaymentCalculation).filter(PaymentCalculation.period_id == period_id).all()
    counts = {"calculated": 0, "simulation": 0, "review": 0, "excluded": 0}
    base_total = 0.0
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        base_total += float(row.total_amount or 0)
    approved_adjustments = db.query(FinancialAdjustment).filter(
        FinancialAdjustment.payment_period_id == period_id,
        FinancialAdjustment.status == "APROVADO",
    ).all()
    reimbursements = sum(abs(float(a.amount or 0)) for a in approved_adjustments if a.kind == "REEMBOLSO")
    credits = sum(abs(float(a.amount or 0)) for a in approved_adjustments if a.kind == "CREDITO")
    discounts = sum(abs(float(a.amount or 0)) for a in approved_adjustments if a.kind in {"DESCONTO", "DEBITO"})
    adjustment_total = sum(_signed_adjustment(a) for a in approved_adjustments)
    counts["rows"] = len(rows)
    counts["base_total"] = round(base_total, 2)
    counts["reimbursements"] = round(reimbursements, 2)
    counts["credits"] = round(credits, 2)
    counts["discounts"] = round(discounts, 2)
    counts["adjustments"] = round(adjustment_total, 2)
    counts["total"] = round(base_total + adjustment_total, 2)
    counts["adjustment_count"] = len(approved_adjustments)
    counts["pending_adjustments"] = db.query(FinancialAdjustment).filter(
        FinancialAdjustment.payment_period_id == period_id, FinancialAdjustment.status == "PENDENTE"
    ).count()
    return counts


def reprocess_open_periods(db: Session, start: date | None = None, end: date | None = None):
    """Mantém o Total Conhecido coerente após alterações operacionais em períodos já simulados/processados."""
    query = db.query(PaymentPeriod).filter(
        PaymentPeriod.status.notin_(["not_processed", "closed"])
    )
    if start:
        query = query.filter(PaymentPeriod.end_date >= start)
    if end:
        query = query.filter(PaymentPeriod.start_date <= end)
    periods = query.order_by(PaymentPeriod.start_date).all()
    count = 0
    for period in periods:
        if db.query(PaymentCalculation).filter(PaymentCalculation.period_id == period.id).count() == 0:
            continue
        process_period(db, period)
        count += 1
    return count


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    pending = pending_query(db).count()
    rules = db.query(CostRule).filter(CostRule.active == True).count()
    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).count()
    out_scope = db.query(Employee).filter(Employee.active == True, Employee.in_scope == False).count()

    today = date.today()
    config = db.get(PaymentCycleConfig, 1)
    ensure_periods(db, today.year, today.month, config.first_half_end_day)
    db.commit()
    periods = db.query(PaymentPeriod).filter_by(year=today.year, month=today.month).order_by(PaymentPeriod.half).all()
    current_period = next((p for p in periods if p.start_date <= today <= p.end_date), periods[-1])
    stats = period_stats(db, current_period.id)
    active_occurrences = db.query(Occurrence).filter(
        Occurrence.status == "ATIVA", Occurrence.start_date <= current_period.end_date,
        or_(Occurrence.end_date == None, Occurrence.end_date >= current_period.start_date)
    ).count()
    pending_adjustments = db.query(FinancialAdjustment).filter(
        FinancialAdjustment.payment_period_id == current_period.id, FinancialAdjustment.status == "PENDENTE"
    ).count()
    sync_review = db.query(Employee).filter(
        Employee.active == True, Employee.in_scope == True, Employee.sync_status != "OK"
    ).count()
    attention = stats.get("review", 0) + pending_adjustments + sync_review
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "pending": pending, "rules": rules, "employees": employees,
        "out_scope": out_scope, "current_period": current_period, "stats": stats,
        "active_occurrences": active_occurrences, "pending_adjustments": pending_adjustments,
        "sync_review": sync_review, "attention": attention,
        "month_label": month_label(today.year, today.month), "status_labels": STATUS_LABELS,
    })


@app.get("/pessoas", response_class=HTMLResponse)
def people_hub(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("people_hub.html", {
        "request": request,
        "employees": db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).count(),
        "occurrences": db.query(Occurrence).filter(Occurrence.status == "ATIVA").count(),
        "extra_passages": db.query(Occurrence).filter(Occurrence.status == "ATIVA", Occurrence.impact_mode == "ADD_VT").count(),
        "nearby_store_rules": db.query(Occurrence).filter(Occurrence.status == "ATIVA", Occurrence.impact_mode == "REMOVE_VT").count(),
    })


@app.get("/regras-operacionais", response_class=HTMLResponse)
def operational_rules_hub(request: Request, db: Session = Depends(get_db)):
    year = 2026
    return templates.TemplateResponse("operational_rules_hub.html", {
        "request": request,
        "tariffs": db.query(BaseTariff).filter(BaseTariff.status.in_(["OK", "OK_AUTO", "OK_MANUAL"])).count(),
        "holidays": db.query(HolidayRule).filter(HolidayRule.active == True, HolidayRule.date >= date(year,1,1), HolidayRule.date <= date(year,12,31)).count(),
        "policies": db.query(CalculationPolicy).count(),
        "policies_approved": db.query(CalculationPolicy).filter(CalculationPolicy.approved == True).count(),
        "homologated": db.query(CostRule).filter(CostRule.active == True).count(),
        "pending": pending_query(db).count(),
        "pdvs": db.query(PointOfSale).count(),
    })


@app.get("/pdvs", response_class=HTMLResponse)
def list_pdvs(request: Request, q: str = "", regional: str = "", rede: str = "", page: int = 1, db: Session = Depends(get_db)):
    limit = 50
    page = max(1, page)
    offset = (page - 1) * limit
    query = db.query(PointOfSale)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(
            PointOfSale.nome_pdv.ilike(term),
            PointOfSale.bandeira.ilike(term),
            PointOfSale.rede.ilike(term),
            PointOfSale.endereco.ilike(term),
            cast(PointOfSale.codigo_pdv, String).ilike(term),
        ))
    if regional.strip():
        query = query.filter(PointOfSale.regional == regional.strip())
    if rede.strip():
        query = query.filter(PointOfSale.rede == rede.strip())

    total = query.count()
    rows = query.order_by(PointOfSale.codigo_pdv).offset(offset).limit(limit).all()
    total_pages = max(1, (total + limit - 1) // limit)

    regionais_raw = db.query(PointOfSale.regional).distinct().filter(PointOfSale.regional != None).all()
    regionais = sorted([r[0] for r in regionais_raw if r[0]])

    return templates.TemplateResponse("pdvs.html", {
        "request": request,
        "rows": rows,
        "q": q,
        "regional": regional,
        "regionais": regionais,
        "page": page,
        "total": total,
        "total_pages": total_pages,
    })


@app.get("/inteligencia", response_class=HTMLResponse)
def intelligence_dashboard(request: Request, db: Session = Depends(get_db)):
    total_pdvs = db.query(PointOfSale).count()
    total_colab = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).count()
    total_calculos = db.query(PaymentCalculation).count()
    total_rotas = db.query(PromoterRoute).count()

    # ── Períodos financeiros ────────────────────────────────────────────────
    periods_data = []
    for p in db.query(PaymentPeriod).order_by(PaymentPeriod.year, PaymentPeriod.month, PaymentPeriod.half).all():
        tot = db.query(func.sum(PaymentCalculation.total_amount)).filter_by(period_id=p.id).scalar() or 0
        cnt = db.query(PaymentCalculation).filter_by(period_id=p.id).count()
        if cnt > 0:
            periods_data.append({
                "id": p.id,
                "label": f"{p.year}-{p.month:02d} Q{p.half}",
                "total": round(float(tot), 2),
                "count": cnt,
                "avg": round(float(tot) / cnt, 2) if cnt else 0
            })

    latest_period_total = periods_data[-1]["total"] if periods_data else 0
    latest_period_avg = periods_data[-1]["avg"] if periods_data else 0
    avg_per_period = round(sum(p["total"] for p in periods_data) / len(periods_data), 2) if periods_data else 0

    # ── PDVs por região (com fallback quando regional é NULL) ──────────────
    SEM_REGIONAL_LABEL = "Sem regional definida"
    all_pdvs_for_group = db.query(PointOfSale).all()
    from collections import defaultdict
    region_group: dict = defaultdict(list)
    for p in all_pdvs_for_group:
        key = (p.regional or SEM_REGIONAL_LABEL).strip()
        region_group[key].append(p)

    region_counts = sorted([(k, len(v)) for k, v in region_group.items()], key=lambda x: -x[1])
    pdvs_by_reg = region_counts[:10]
    reg_labels = [r[0] for r in pdvs_by_reg]
    reg_values = [r[1] for r in pdvs_by_reg]

    # ── PDVs detalhados por região (Top 10 para a lista simples) ───────────
    reg_pdvs_detail = []
    for regional, cnt in region_counts[:12]:
        pdvs_raw = sorted(region_group[regional], key=lambda x: x.nome_pdv or "")
        reg_pdvs_detail.append({
            "regional": regional,
            "total": cnt,
            "pdvs": [{
                "codigo": p.codigo_pdv,
                "nome": p.nome_pdv or "—",
                "rede": p.rede or "—",
                "endereco": p.endereco or "—"
            } for p in pdvs_raw]
        })

    # ── Densidade por UF ───────────────────────────────────────────────────
    latest_pid = periods_data[-1]["id"] if periods_data else 1
    pdv_by_uf = dict(db.query(PointOfSale.regional, func.count(PointOfSale.id)).group_by(PointOfSale.regional).all())
    emp_by_uf = dict(db.query(Employee.work_state, func.count(Employee.id)).filter(Employee.active == True, Employee.in_scope == True).group_by(Employee.work_state).all())
    latest_cost_by_uf = dict(
        db.query(Employee.work_state, func.sum(PaymentCalculation.total_amount))\
        .join(PaymentCalculation, PaymentCalculation.employee_id == Employee.id)\
        .filter(PaymentCalculation.period_id == latest_pid)\
        .group_by(Employee.work_state).all()
    )
    all_ufs = sorted(set(list(pdv_by_uf.keys()) + list(emp_by_uf.keys())), key=lambda x: pdv_by_uf.get(x, 0), reverse=True)
    state_metrics = []
    for uf in all_ufs:
        if not uf or len(uf) > 2:
            continue
        p_cnt = pdv_by_uf.get(uf, 0)
        e_cnt = emp_by_uf.get(uf, 0)
        c_val = latest_cost_by_uf.get(uf, 0) or 0
        ratio = round(p_cnt / e_cnt, 1) if e_cnt > 0 else 0
        state_metrics.append({
            "uf": uf,
            "pdvs": p_cnt,
            "promoters": e_cnt,
            "ratio": ratio,
            "cost": round(float(c_val), 2),
            "avg_cost_promoter": round(float(c_val) / e_cnt, 2) if e_cnt > 0 else 0
        })

    # ── PONTO 4 — Visitas por dia da semana ────────────────────────────────
    day_cols = [
        ("Segunda", PromoterRoute.monday),
        ("Terça",   PromoterRoute.tuesday),
        ("Quarta",  PromoterRoute.wednesday),
        ("Quinta",  PromoterRoute.thursday),
        ("Sexta",   PromoterRoute.friday),
        ("Sábado",  PromoterRoute.saturday),
        ("Domingo", PromoterRoute.sunday),
    ]
    visitas_labels = [d[0] for d in day_cols]
    visitas_values = [
        db.query(PromoterRoute).filter(d[1] != None, d[1] != "").count()
        for d in day_cols
    ]

    # Média de PDVs por dia por promotor (contando dias ativos por registro)
    all_routes = db.query(PromoterRoute).filter(PromoterRoute.employee_id != None).all()
    promoter_day_count: dict = {}
    promoter_pdv_count: dict = {}
    for r in all_routes:
        dias = sum(1 for col in [r.monday, r.tuesday, r.wednesday, r.thursday, r.friday, r.saturday, r.sunday] if col)
        promoter_day_count[r.promoter_name] = promoter_day_count.get(r.promoter_name, 0) + dias
        promoter_pdv_count[r.promoter_name] = promoter_pdv_count.get(r.promoter_name, 0) + 1
    avg_pdvs_per_day_list = []
    for name, total_dias in promoter_day_count.items():
        pdvs = promoter_pdv_count.get(name, 0)
        if total_dias > 0:
            avg_pdvs_per_day_list.append(round(pdvs / total_dias, 2))
    avg_pdvs_per_day = round(sum(avg_pdvs_per_day_list) / len(avg_pdvs_per_day_list), 2) if avg_pdvs_per_day_list else 0

    # ── Supervisores por volume de visitas em campo (com UF/região) ────────
    # Primeiro, descobrimos a UF mais frequente de cada supervisor (através dos colaboradores)
    sup_uf_raw = db.query(Employee.supervisor, Employee.work_state, func.count(Employee.id))\
        .filter(Employee.supervisor != None, Employee.active == True, Employee.in_scope == True)\
        .group_by(Employee.supervisor, Employee.work_state).all()
    supervisor_uf: dict = {}
    from collections import defaultdict
    _sup_aux: dict = defaultdict(list)
    for sup_name, uf, cnt in sup_uf_raw:
        _sup_aux[sup_name].append((uf or "—", cnt))
    for sup_name, items in _sup_aux.items():
        items.sort(key=lambda x: -x[1])
        supervisor_uf[sup_name] = items[0][0]

    # Agora supervisores + total de roteiros + quantidade de promotores
    sup_visitas_raw = db.query(
        Employee.supervisor,
        func.count(PromoterRoute.id),
        func.count(func.distinct(Employee.id))
    )\
        .join(PromoterRoute, PromoterRoute.employee_id == Employee.id)\
        .filter(Employee.supervisor != None)\
        .group_by(Employee.supervisor)\
        .order_by(func.count(PromoterRoute.id).desc()).limit(8).all()
    sup_visitas_labels = []
    for s in sup_visitas_raw:
        short = s[0].split()[0] + " " + s[0].split()[-1] if len(s[0].split()) > 1 else s[0]
        uf = supervisor_uf.get(s[0], "—")
        sup_visitas_labels.append(f"{short} ({uf})")
    sup_visitas_values = [s[1] for s in sup_visitas_raw]
    sup_visitas_full = [{
        "name": s[0],
        "rotas": s[1],
        "promoters": s[2],
        "uf": supervisor_uf.get(s[0], "—")
    } for s in sup_visitas_raw]

    # ── Custo por visita (VT / visitas semanais do promotor) ───────────────
    emp_routes_count = dict(
        db.query(PromoterRoute.employee_id, func.count(PromoterRoute.id))\
        .filter(PromoterRoute.employee_id != None)\
        .group_by(PromoterRoute.employee_id).all()
    )
    calcs_last = db.query(PaymentCalculation)\
        .filter(PaymentCalculation.period_id == latest_pid).all()
    custo_visita_list = []
    for calc in calcs_last:
        rotas = emp_routes_count.get(calc.employee_id, 0)
        if rotas > 0 and calc.total_amount and float(calc.total_amount) > 0:
            custo_visita_list.append({
                "employee_id": calc.employee_id,
                "total": round(float(calc.total_amount), 2),
                "rotas": rotas,
                "custo_por_rota": round(float(calc.total_amount) / rotas, 2)
            })
    custo_visita_list.sort(key=lambda x: x["custo_por_rota"], reverse=True)
    top_custo_visita = custo_visita_list[:10]
    # Enriquecer com nome + UF do colaborador + nome curto do supervisor
    emp_full = db.query(Employee.id, Employee.name, Employee.work_state, Employee.supervisor).all()
    emp_detail: dict = {}
    for e_id, e_name, e_uf, e_sup in emp_full:
        sup_short = ""
        if e_sup:
            sup_short = e_sup.split()[0] + " " + e_sup.split()[-1] if len(e_sup.split()) > 1 else e_sup
        emp_detail[e_id] = (e_name, e_uf or "—", sup_short)
    for item in top_custo_visita:
        det = emp_detail.get(item["employee_id"], (f"ID {item['employee_id']}", "—", ""))
        item["name"] = det[0]
        item["uf"] = det[1]
        item["supervisor_short"] = det[2]
    avg_custo_por_visita = round(sum(x["custo_por_rota"] for x in custo_visita_list) / len(custo_visita_list), 2) if custo_visita_list else 0
    custo_visita_chart_labels = json.dumps([x["name"].split()[0] + " " + x["name"].split()[-1] if len(x["name"].split()) > 1 else x["name"] for x in top_custo_visita])
    custo_visita_chart_values = json.dumps([x["custo_por_rota"] for x in top_custo_visita])

    # ── Modal Ônibus x Intensidade de rota ────────────────────────────────
    onibus_alta_rota = []
    for emp in db.query(Employee).filter(
        Employee.active == True,
        Employee.in_scope == True,
        Employee.mobility_mode == "ONIBUS"
    ).all():
        rotas = emp_routes_count.get(emp.id, 0)
        if rotas >= 5:
            sup_short = ""
            if emp.supervisor:
                parts = emp.supervisor.split()
                sup_short = parts[0] + " " + parts[-1] if len(parts) > 1 else emp.supervisor
            onibus_alta_rota.append({
                "name": emp.name,
                "rotas": rotas,
                "city": emp.work_city or "",
                "uf": emp.work_state or "—",
                "supervisor_short": sup_short,
            })
    onibus_alta_rota.sort(key=lambda x: x["rotas"], reverse=True)
    total_onibus_alta = len(onibus_alta_rota)

    # ── PONTO 7 — Regra 750m: PDVs na mesma cidade no mesmo dia ───────────
    # Agrupa por promotor+dia e conta quantos PDVs visitam na mesma cidade
    candidatos_750m = []
    from collections import defaultdict
    promoter_day_pdvs: dict = defaultdict(list)
    for r in all_routes:
        days_map = [
            ("Segunda", r.monday), ("Terça", r.tuesday), ("Quarta", r.wednesday),
            ("Quinta", r.thursday), ("Sexta", r.friday), ("Sábado", r.saturday),
        ]
        for day_name, day_val in days_map:
            if day_val:
                key = (r.promoter_name, day_name, r.city or "?")
                promoter_day_pdvs[key].append(r.pdv_name)
    for (promotor, dia, cidade), pdvs in promoter_day_pdvs.items():
        if len(pdvs) >= 2:
            candidatos_750m.append({
                "promotor": promotor,
                "dia": dia,
                "cidade": cidade,
                "pdvs": len(pdvs),
                "lista": "; ".join(pdvs[:4]) + (" ..." if len(pdvs) > 4 else ""),
            })
    candidatos_750m.sort(key=lambda x: x["pdvs"], reverse=True)
    total_candidatos_750m = len(candidatos_750m)

    return templates.TemplateResponse("intelligence.html", {
        "request": request,
        "total_pdvs": total_pdvs,
        "total_colab": total_colab,
        "total_calculos": total_calculos,
        "total_rotas": total_rotas,
        "latest_period_total": latest_period_total,
        "latest_period_avg": latest_period_avg,
        "avg_per_period": avg_per_period,
        "periods_data": periods_data,
        "reg_labels": json.dumps(reg_labels),
        "reg_values": json.dumps(reg_values),
        "reg_pdvs_detail": reg_pdvs_detail,
        "period_labels": json.dumps([p["label"] for p in periods_data]),
        "period_totals": json.dumps([p["total"] for p in periods_data]),
        "state_metrics": state_metrics[:12],
        # Ponto 4
        "visitas_labels": json.dumps(visitas_labels),
        "visitas_values": json.dumps(visitas_values),
        "avg_pdvs_per_day": avg_pdvs_per_day,
        # Ponto 6
        "sup_visitas_labels": json.dumps(sup_visitas_labels),
        "sup_visitas_values": json.dumps(sup_visitas_values),
        "sup_visitas_full": sup_visitas_full,
        # Ponto 1
        "avg_custo_por_visita": avg_custo_por_visita,
        "top_custo_visita": top_custo_visita,
        "custo_visita_chart_labels": custo_visita_chart_labels,
        "custo_visita_chart_values": custo_visita_chart_values,
        # Ponto 2
        "onibus_alta_rota": onibus_alta_rota[:20],
        "total_onibus_alta": total_onibus_alta,
        # Ponto 7
        "candidatos_750m": candidatos_750m[:30],
        "total_candidatos_750m": total_candidatos_750m,
    })




# ── UPLOAD DE ROTEIROS (Puzzle) ─────────────────────────────────────────────

@app.get("/admin/importar-roteiros", response_class=HTMLResponse)
def import_routes_page(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    total_atual = db.query(PromoterRoute).count()
    return templates.TemplateResponse("admin_import_routes.html", {
        "request": request,
        "total_atual": total_atual,
        "resultado": None,
    })


@app.post("/admin/importar-roteiros", response_class=HTMLResponse)
async def import_routes_upload(request: Request, db: Session = Depends(get_db)):
    import io, unicodedata
    from fastapi import UploadFile
    import openpyxl

    _admin(request, db)

    form = await request.form()
    arquivo = form.get("arquivo")

    if not arquivo or not arquivo.filename:
        return templates.TemplateResponse("admin_import_routes.html", {
            "request": request,
            "total_atual": db.query(PromoterRoute).count(),
            "resultado": {"ok": False, "msg": "Nenhum arquivo enviado."},
        })

    conteudo = await arquivo.read()

    def norm(s):
        if not s:
            return ""
        return unicodedata.normalize("NFC", str(s).strip().upper())

    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception as e:
        return templates.TemplateResponse("admin_import_routes.html", {
            "request": request,
            "total_atual": db.query(PromoterRoute).count(),
            "resultado": {"ok": False, "msg": f"Erro ao ler o arquivo Excel: {e}"},
        })

    # Mapa de colaboradores para vincular
    emp_map = {}
    for emp in db.query(Employee).filter(Employee.active == True).all():
        emp_map[norm(emp.name)] = emp.id

    # Limpar tabela atual
    db.query(PromoterRoute).delete()
    db.commit()

    inseridos = 0
    vinculados = 0
    erros = 0

    # Colunas: 0=PDV, 1=Endereço, 2=Cidade, 3=UF, 4=Freq, 5=Marcas,
    #          6=Seg, 7=Ter, 8=Qua, 9=Qui, 10=Sex, 11=Sab, 12=Dom, 13=Promotores
    for i, row in enumerate(rows[1:], start=2):  # pular cabeçalho
        try:
            pdv_name = str(row[0]).strip() if row[0] else ""
            if not pdv_name or pdv_name.lower() == "none":
                continue
            address = str(row[1]).strip() if row[1] else None
            city = str(row[2]).strip() if row[2] else None
            uf = str(row[3]).strip()[:2].upper() if row[3] else None
            freq_raw = row[4]
            frequency = int(freq_raw) if freq_raw and str(freq_raw).isdigit() else 1
            monday   = str(row[6]).strip() if row[6] else None
            tuesday  = str(row[7]).strip() if row[7] else None
            wednesday= str(row[8]).strip() if row[8] else None
            thursday = str(row[9]).strip() if row[9] else None
            friday   = str(row[10]).strip() if row[10] else None
            saturday = str(row[11]).strip() if row[11] else None
            sunday   = str(row[12]).strip() if row[12] else None
            promotores_raw = str(row[13]).strip() if len(row) > 13 and row[13] else ""
            promotores = [p.strip() for p in promotores_raw.split("|") if p.strip()]

            if not promotores:
                promotores = ["SEM PROMOTOR"]

            for promo_name in promotores:
                emp_id = emp_map.get(norm(promo_name))
                rota = PromoterRoute(
                    employee_id=emp_id,
                    promoter_name=promo_name,
                    pdv_name=pdv_name,
                    address=address,
                    city=city,
                    uf=uf,
                    frequency=frequency,
                    monday=monday,
                    tuesday=tuesday,
                    wednesday=wednesday,
                    thursday=thursday,
                    friday=friday,
                    saturday=saturday,
                    sunday=sunday,
                )
                db.add(rota)
                inseridos += 1
                if emp_id:
                    vinculados += 1
        except Exception:
            erros += 1
            continue

    db.commit()

    resultado = {
        "ok": True,
        "msg": f"Importação concluída! {inseridos} roteiros carregados ({vinculados} vinculados a colaboradores, {erros} linhas com erro).",
        "inseridos": inseridos,
        "vinculados": vinculados,
        "erros": erros,
    }
    return templates.TemplateResponse("admin_import_routes.html", {
        "request": request,
        "total_atual": db.query(PromoterRoute).count(),
        "resultado": resultado,
    })


@app.get("/colaboradores", response_class=HTMLResponse)
def employees(request: Request, q: str = "", grupo: str = "", fora_escopo: int = 0, db: Session = Depends(get_db)):
    user = _user(request, db)
    show_out = bool(fora_escopo and user and user.role == "ADMIN")
    query = db.query(Employee).filter(Employee.active == True)
    query = query.filter(Employee.in_scope == (False if show_out else True))
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(Employee.name.ilike(term), Employee.work_city.ilike(term), Employee.supervisor.ilike(term)))
    if grupo:
        query = query.filter(Employee.benefit_group == grupo)
    rows = query.order_by(Employee.name).all()
    group_counts = {
        key: db.query(Employee).filter(Employee.active == True, Employee.in_scope == True, Employee.benefit_group == key).count()
        for key in ["PROMOTOR_VT", "LIDERANCA_KM"]
    }
    return templates.TemplateResponse("employees.html", {
        "request": request, "rows": rows, "q": q, "grupo": grupo, "group_counts": group_counts,
        "outside_count": db.query(Employee).filter(Employee.active == True, Employee.in_scope == False).count(),
        "show_out": show_out,
    })


@app.get("/colaboradores/{employee_id}", response_class=HTMLResponse)
def employee_detail(employee_id: int, request: Request, mes: str | None = None, db: Session = Depends(get_db)):
    employee = db.get(Employee, employee_id)
    if not employee:
        return RedirectResponse(url="/colaboradores", status_code=303)
    policy = db.query(CalculationPolicy).filter(
        CalculationPolicy.workload_key == employee.workload_key,
        CalculationPolicy.eligible_group == employee.benefit_group,
    ).first()
    override = db.query(EmployeePolicyOverride).filter(
        EmployeePolicyOverride.employee_id == employee.id, EmployeePolicyOverride.active == True
    ).order_by(EmployeePolicyOverride.id.desc()).first()
    occurrences = db.query(Occurrence).filter(Occurrence.employee_id == employee.id).order_by(Occurrence.start_date.desc()).limit(30).all()
    today = date.today()
    current_events = db.query(Occurrence).filter(
        Occurrence.employee_id == employee.id, Occurrence.status == "ATIVA",
        Occurrence.start_date <= today, or_(Occurrence.end_date == None, Occurrence.end_date >= today)
    ).order_by(Occurrence.id.desc()).all()
    calculations = db.query(PaymentCalculation).options(joinedload(PaymentCalculation.period)).filter(
        PaymentCalculation.employee_id == employee.id
    ).order_by(PaymentCalculation.id.desc()).limit(12).all()
    y,m = parse_year_month(mes)
    weeks = employee_calendar(db, employee, y, m)
    py,pm = _month_shift(y,m,-1); ny,nm = _month_shift(y,m,1)
    return templates.TemplateResponse("employee_detail.html", {
        "request": request, "employee": employee, "policy": policy, "policy_override": override,
        "occurrences": occurrences, "current_events": current_events, "calculations": calculations, "status_labels": STATUS_LABELS,
        "calendar_weeks": weeks, "calendar_month": month_label(y,m), "calendar_value": f"{y:04d}-{m:02d}",
        "prev_month": f"{py:04d}-{pm:02d}", "next_month": f"{ny:04d}-{nm:02d}", "weekdays": WEEKDAYS,
    })


@app.post("/colaboradores/{employee_id}/politica-individual")
async def employee_policy_override_save(employee_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    employee = db.get(Employee, employee_id)
    if not employee:
        return RedirectResponse(url="/colaboradores", status_code=303)
    form = await request.form()
    action = (form.get("action") or "save").strip()
    current = db.query(EmployeePolicyOverride).filter(EmployeePolicyOverride.employee_id == employee.id, EmployeePolicyOverride.active == True).all()
    if action == "remove":
        for row in current: row.active = False
        audit(db, user, "REMOVER_POLITICA_INDIVIDUAL", "COLABORADOR", employee.id, f"Ajuste individual removido de {employee.name}")
        db.commit(); reprocess_open_periods(db)
        return RedirectResponse(url=f"/colaboradores/{employee.id}?policy_removed=1", status_code=303)
    weekdays=[]
    for raw in form.getlist("weekdays"):
        try:
            val=int(raw)
            if 0 <= val <= 6: weekdays.append(val)
        except Exception: pass
    vt = parse_form_money(form.get("vt_per_day"), None)
    reason = (form.get("reason") or "").strip()
    if not reason or (not weekdays and vt is None):
        return RedirectResponse(url=f"/colaboradores/{employee.id}?policy_error=1", status_code=303)
    for row in current: row.active = False
    row=EmployeePolicyOverride(
        employee_id=employee.id, weekdays=','.join(str(x) for x in sorted(set(weekdays))) or None,
        vt_per_day=vt, valid_from=parse_form_date(form.get("valid_from")), valid_to=parse_form_date(form.get("valid_to")),
        reason=reason, notes=(form.get("notes") or "").strip() or None, active=True
    )
    db.add(row); db.flush()
    audit(db, user, "SALVAR_POLITICA_INDIVIDUAL", "COLABORADOR", employee.id, f"Ajuste individual salvo para {employee.name}", after_json=json.dumps({"weekdays": row.weekdays, "vt_per_day": row.vt_per_day, "reason": row.reason}, ensure_ascii=False))
    db.commit(); reprocess_open_periods(db)
    return RedirectResponse(url=f"/colaboradores/{employee.id}?policy_saved=1", status_code=303)


@app.get("/politicas", response_class=HTMLResponse)
def policies(request: Request, db: Session = Depends(get_db)):
    rows = db.query(CalculationPolicy).order_by(CalculationPolicy.label).all()
    workload_counts = {p.workload_key: db.query(Employee).filter(
        Employee.active == True, Employee.benefit_group == p.eligible_group,
        Employee.workload_key == p.workload_key,
    ).count() for p in rows}
    return templates.TemplateResponse("policies.html", {
        "request": request, "rows": rows, "weekdays": WEEKDAYS, "workload_counts": workload_counts,
    })


@app.post("/politicas/{policy_id}")
async def save_policy(policy_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    row = db.get(CalculationPolicy, policy_id)
    if not row:
        return RedirectResponse(url="/politicas", status_code=303)
    form = await request.form()
    selected = []
    for raw in form.getlist("weekdays"):
        try:
            value = int(raw)
            if 0 <= value <= 6:
                selected.append(value)
        except ValueError:
            pass
    row.weekdays = ",".join(str(x) for x in sorted(set(selected)))
    try:
        raw_vt = str(form.get("vt_per_day") or "0").replace(",", ".")
        row.vt_per_day = max(0.0, float(raw_vt))
    except ValueError:
        pass
    row.notes = (form.get("notes") or "").strip() or None
    row.approved = (form.get("action") == "approve")
    db.commit()
    return RedirectResponse(url="/politicas?saved=1", status_code=303)


@app.get("/ocorrencias", response_class=HTMLResponse)
def occurrences(request: Request, q: str = "", tipo: str = "", db: Session = Depends(get_db)):
    query = db.query(Occurrence).options(joinedload(Occurrence.employee))
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(Occurrence.employee_name_text.ilike(term), Occurrence.notes.ilike(term)))
    if tipo:
        query = query.filter(Occurrence.kind == tipo)
    rows = query.order_by(Occurrence.id.desc()).limit(500).all()
    types = [x[0] for x in db.query(Occurrence.kind).distinct().order_by(Occurrence.kind).all()]
    return templates.TemplateResponse("occurrences.html", {
        "request": request, "rows": rows, "q": q, "tipo": tipo, "types": types,
    })


@app.get("/ocorrencias/nova", response_class=HTMLResponse)
def occurrence_new(request: Request, employee_id: int | None = None, db: Session = Depends(get_db)):
    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).order_by(Employee.name).all()
    return templates.TemplateResponse("occurrence_new.html", {
        "request": request, "employees": employees, "selected_employee_id": employee_id,
    })


@app.post("/ocorrencias/nova")
async def occurrence_create(request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    form = await request.form()
    try:
        employee_id = int(form.get("employee_id"))
    except Exception:
        return RedirectResponse(url="/ocorrencias/nova?erro=colaborador", status_code=303)
    employee = db.get(Employee, employee_id)
    if not employee:
        return RedirectResponse(url="/ocorrencias/nova?erro=colaborador", status_code=303)

    def parse_date(raw):
        try:
            return date.fromisoformat(str(raw)) if raw else None
        except Exception:
            return None

    start = parse_date(form.get("start_date"))
    end = parse_date(form.get("end_date")) or start
    impact = (form.get("impact_mode") or "INFO").strip()
    qty = None
    if impact in {"ADD_VT", "REMOVE_VT"}:
        try:
            qty = float(str(form.get("quantity_delta") or "0").replace(",", "."))
        except ValueError:
            qty = 0.0
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    occurrence = Occurrence(
        source_key=f"MANUAL:{employee.id}:{stamp}", employee_id=employee.id,
        employee_name_text=employee.name, kind=(form.get("kind") or "OUTRO").strip(),
        start_date=start, end_date=end, impact_mode=impact, quantity_delta=qty,
        notes=(form.get("notes") or "").strip() or None, source="MANUAL", status="ATIVA",
    )
    db.add(occurrence); db.flush()
    audit(db, user, "CRIAR_OCORRENCIA", "OCORRENCIA", occurrence.id, f"{occurrence.kind} registrada para {employee.name}")
    db.commit()
    if start:
        reprocess_open_periods(db, start, end or start)
    return RedirectResponse(url=f"/colaboradores/{employee.id}?occurrence_saved=1", status_code=303)


@app.post("/ocorrencias/{occurrence_id}/excluir")
def occurrence_delete(occurrence_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    row = db.get(Occurrence, occurrence_id)
    if row and row.source == "MANUAL":
        start, end = row.start_date, row.end_date or row.start_date
        audit(db, user, "EXCLUIR_OCORRENCIA", "OCORRENCIA", row.id, f"{row.kind} removida de {row.employee_name_text or 'colaborador'}")
        db.delete(row)
        db.commit()
        if start:
            reprocess_open_periods(db, start, end)
    return RedirectResponse(url="/ocorrencias", status_code=303)


@app.get("/tarifas", response_class=HTMLResponse)
def tariffs(request: Request, uf: str = "", q: str = "", db: Session = Depends(get_db)):
    query = db.query(BaseTariff)
    if uf:
        query = query.filter(BaseTariff.uf == uf.upper())
    if q.strip():
        query = query.filter(BaseTariff.city.ilike(f"%{q.strip()}%"))
    rows = query.order_by(BaseTariff.uf, BaseTariff.city, BaseTariff.valid_from.desc()).all()
    return templates.TemplateResponse("tariffs.html", {
        "request": request, "rows": rows, "uf": uf.upper(), "q": q, "ufs": UF_NAMES,
    })


@app.get("/tarifas/nova", response_class=HTMLResponse)
def tariff_new(request: Request):
    return templates.TemplateResponse("tariff_new.html", {"request": request, "ufs": UF_NAMES})


@app.post("/tarifas/nova")
async def tariff_create(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    city = (form.get("city") or "").strip()
    uf = (form.get("uf") or "").strip().upper()
    amount = parse_form_money(form.get("amount"))
    if not city or uf not in UF_NAMES or amount is None or amount < 0:
        return RedirectResponse(url="/tarifas/nova?erro=dados", status_code=303)
    max_legacy = db.query(BaseTariff.legacy_id).order_by(BaseTariff.legacy_id.desc()).first()
    legacy_id = (max_legacy[0] if max_legacy else 0) + 1
    tariff = BaseTariff(
        legacy_id=legacy_id, city=city, city_norm=canonical_city(city), uf=uf,
        modal=(form.get("modal") or "ONIBUS").strip().upper() or "ONIBUS", amount=amount,
        valid_from=parse_form_date(form.get("valid_from")), notes=(form.get("notes") or "").strip() or None,
        original_value=str(form.get("amount") or ""), source_sheet="QS", source_row=None,
        status="OK_MANUAL", review_reason=None,
    )
    db.add(tariff); db.flush()
    audit(db, user, "CRIAR_TARIFA", "TARIFA", tariff.id, f"Tarifa {city}/{uf} criada em {brl(amount)}")
    db.commit()
    reprocess_open_periods(db)
    return RedirectResponse(url=f"/tarifas?uf={uf}&saved=1", status_code=303)


@app.post("/tarifas/{tariff_id}/desativar")
def tariff_deactivate(tariff_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    row = db.get(BaseTariff, tariff_id)
    if row and row.source_sheet == "QS":
        row.status = "INATIVA"
        audit(db, user, "DESATIVAR_TARIFA", "TARIFA", row.id, f"Tarifa {row.city}/{row.uf} desativada")
        db.commit()
        reprocess_open_periods(db)
    return RedirectResponse(url="/tarifas", status_code=303)


@app.get("/feriados", response_class=HTMLResponse)
def holidays(request: Request, ano: int = 2026, uf: str = "", escopo: str = "", db: Session = Depends(get_db)):
    ano = max(2025, min(ano, 2035))
    query = db.query(HolidayRule).filter(
        HolidayRule.date >= date(ano, 1, 1), HolidayRule.date <= date(ano, 12, 31), HolidayRule.active == True
    )
    if uf:
        u = uf.upper()
        query = query.filter(or_(HolidayRule.scope_type == "NATIONAL", HolidayRule.uf == u))
    if escopo:
        query = query.filter(HolidayRule.scope_type == escopo)
    rows = query.order_by(HolidayRule.date, HolidayRule.scope_type, HolidayRule.uf, HolidayRule.city).all()
    state_coverage = []
    for code, name in UF_NAMES.items():
        state_count = db.query(HolidayRule).filter(
            HolidayRule.active == True, HolidayRule.scope_type == "STATE", HolidayRule.uf == code,
            HolidayRule.date >= date(ano,1,1), HolidayRule.date <= date(ano,12,31)
        ).count()
        state_coverage.append((code, name, state_count))
    return templates.TemplateResponse("holidays.html", {
        "request": request, "rows": rows, "ano": ano, "uf": uf.upper(), "escopo": escopo,
        "ufs": UF_NAMES, "state_coverage": state_coverage,
        "national_count": db.query(HolidayRule).filter(HolidayRule.active == True, HolidayRule.scope_type == "NATIONAL", HolidayRule.category == "FERIADO", HolidayRule.date >= date(ano,1,1), HolidayRule.date <= date(ano,12,31)).count(),
    })


@app.get("/feriados/novo", response_class=HTMLResponse)
def holiday_new(request: Request):
    return templates.TemplateResponse("holiday_new.html", {"request": request, "ufs": UF_NAMES})


@app.post("/feriados/novo")
async def holiday_create(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    hdate = parse_form_date(form.get("date"))
    name = (form.get("name") or "").strip()
    scope = (form.get("scope_type") or "MUNICIPAL").strip().upper()
    uf = (form.get("uf") or "").strip().upper() or None
    city = (form.get("city") or "").strip() or None
    category = (form.get("category") or "FERIADO").strip().upper()
    if not hdate or not name or scope not in {"NATIONAL", "STATE", "MUNICIPAL"}:
        return RedirectResponse(url="/feriados/novo?erro=dados", status_code=303)
    if scope in {"STATE", "MUNICIPAL"} and uf not in UF_NAMES:
        return RedirectResponse(url="/feriados/novo?erro=uf", status_code=303)
    if scope == "MUNICIPAL" and not city:
        return RedirectResponse(url="/feriados/novo?erro=cidade", status_code=303)
    if scope == "NATIONAL":
        uf = None; city = None
    elif scope == "STATE":
        city = None
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    db.add(HolidayRule(
        source_key=f"MANUAL_QS:{stamp}", date=hdate, name=name, scope_type=scope, uf=uf, city=city,
        city_norm=canonical_city(city) if city else None, category=category,
        affects_calculation=(form.get("affects_calculation") == "on"), source="MANUAL_QS",
        source_ref="Cadastrado no QS", notes=(form.get("notes") or "").strip() or None, active=True,
    ))
    db.commit()
    reprocess_open_periods(db, hdate, hdate)
    return RedirectResponse(url=f"/feriados?ano={hdate.year}&uf={uf or ''}&saved=1", status_code=303)


@app.post("/feriados/{holiday_id}/impacto")
def holiday_toggle_impact(holiday_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    row = db.get(HolidayRule, holiday_id)
    if row:
        row.affects_calculation = not row.affects_calculation
        db.commit()
        reprocess_open_periods(db, row.date, row.date)
    return RedirectResponse(url=f"/feriados?ano={row.date.year if row else 2026}&uf={row.uf if row and row.uf else ''}", status_code=303)


@app.post("/feriados/{holiday_id}/desativar")
def holiday_deactivate(holiday_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    row = db.get(HolidayRule, holiday_id)
    if row and row.source == "MANUAL_QS":
        hdate = row.date
        row.active = False
        db.commit()
        reprocess_open_periods(db, hdate, hdate)
    return RedirectResponse(url="/feriados", status_code=303)


@app.get("/quinzenas", response_class=HTMLResponse)
def payment_periods(request: Request, competencia: str | None = None, db: Session = Depends(get_db)):
    year, month = parse_year_month(competencia)
    config = db.get(PaymentCycleConfig, 1)
    ensure_periods(db, year, month, config.first_half_end_day)
    db.commit()
    periods = db.query(PaymentPeriod).filter_by(year=year, month=month).order_by(PaymentPeriod.half).all()
    cards = [(p, period_stats(db, p.id)) for p in periods]
    return templates.TemplateResponse("payment_periods.html", {
        "request": request, "cards": cards, "competencia": f"{year:04d}-{month:02d}",
        "month_label": month_label(year, month), "config": config, "status_labels": STATUS_LABELS,
    })


@app.post("/quinzenas/{period_id}/processar")
def process_payment_period(period_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    period = db.get(PaymentPeriod, period_id)
    if not period:
        return RedirectResponse(url="/quinzenas", status_code=303)
    try:
        result = process_period(db, period)
        audit(db, user, "PROCESSAR_QUINZENA", "QUINZENA", period.id, f"{period.half}ª quinzena {period.month:02d}/{period.year} processada")
        db.commit()
    except ValueError:
        return RedirectResponse(url=f"/quinzenas/{period_id}?erro=fechada", status_code=303)
    return RedirectResponse(url=f"/quinzenas/{period_id}?processed=1", status_code=303)


@app.get("/quinzenas/{period_id}", response_class=HTMLResponse)
def period_results(period_id: int, request: Request, status: str = "", q: str = "", db: Session = Depends(get_db)):
    period = db.get(PaymentPeriod, period_id)
    if not period:
        return RedirectResponse(url="/quinzenas", status_code=303)
    query = db.query(PaymentCalculation).join(Employee).options(joinedload(PaymentCalculation.employee)).filter(
        PaymentCalculation.period_id == period.id
    )
    if status:
        query = query.filter(PaymentCalculation.status == status)
    if q.strip():
        query = query.filter(or_(Employee.name.ilike(f"%{q.strip()}%"), Employee.work_city.ilike(f"%{q.strip()}%")))
    rows = query.order_by(Employee.name).all()
    stats = period_stats(db, period.id)
    return templates.TemplateResponse("period_results.html", {
        "request": request, "period": period, "rows": rows, "stats": stats,
        "status": status, "q": q, "status_labels": STATUS_LABELS,
    })


@app.get("/quinzenas/{period_id}/ajustes", response_class=HTMLResponse)
def period_adjustments(period_id: int, request: Request, db: Session = Depends(get_db)):
    period = db.get(PaymentPeriod, period_id)
    if not period:
        return RedirectResponse(url="/quinzenas", status_code=303)
    rows = db.query(FinancialAdjustment).options(joinedload(FinancialAdjustment.employee)).filter(
        FinancialAdjustment.payment_period_id == period_id
    ).order_by(FinancialAdjustment.id.desc()).all()
    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).order_by(Employee.name).all()
    return templates.TemplateResponse("adjustments.html", {
        "request": request, "period": period, "rows": rows, "employees": employees,
        "stats": period_stats(db, period_id), "status_labels": STATUS_LABELS,
    })


@app.post("/quinzenas/{period_id}/ajustes/novo")
async def adjustment_create(period_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    period = db.get(PaymentPeriod, period_id)
    if not period:
        return RedirectResponse(url="/quinzenas", status_code=303)
    form = await request.form()
    try:
        employee_id = int(form.get("employee_id"))
    except Exception:
        return RedirectResponse(url=f"/quinzenas/{period_id}/ajustes?erro=colaborador", status_code=303)
    employee = db.get(Employee, employee_id)
    amount = parse_form_money(form.get("amount"))
    kind = (form.get("kind") or "REEMBOLSO").strip().upper()
    reason = (form.get("reason") or "").strip()
    if not employee or amount is None or amount <= 0 or kind not in {"REEMBOLSO", "DESCONTO", "CREDITO", "DEBITO"} or not reason:
        return RedirectResponse(url=f"/quinzenas/{period_id}/ajustes?erro=dados", status_code=303)
    def maybe_int(value):
        try: return int(value) if value not in (None, "") else None
        except Exception: return None
    adjustment = FinancialAdjustment(
        employee_id=employee.id, kind=kind, benefit_type=(form.get("benefit_type") or "VT").strip().upper(),
        amount=abs(amount), reference_year=maybe_int(form.get("reference_year")),
        reference_month=maybe_int(form.get("reference_month")), reference_half=maybe_int(form.get("reference_half")),
        payment_period_id=period.id, reason=reason, notes=(form.get("notes") or "").strip() or None,
        status="PENDENTE", automatic=False, source="MANUAL_QS",
    )
    db.add(adjustment); db.flush()
    audit(db, user, "CRIAR_AJUSTE", "AJUSTE", adjustment.id, f"{kind} de {brl(abs(amount))} para {employee.name}")
    db.commit()
    return RedirectResponse(url=f"/quinzenas/{period_id}/ajustes?saved=1", status_code=303)


@app.post("/ajustes/{adjustment_id}/aprovar")
def adjustment_approve(adjustment_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    row = db.get(FinancialAdjustment, adjustment_id)
    if not row:
        return RedirectResponse(url="/quinzenas", status_code=303)
    row.status = "APROVADO"; row.approved_at = datetime.utcnow(); audit(db, user, "APROVAR_AJUSTE", "AJUSTE", row.id, f"{row.kind} de {brl(row.amount)} aprovado"); db.commit()
    return RedirectResponse(url=f"/quinzenas/{row.payment_period_id}/ajustes", status_code=303)


@app.post("/ajustes/{adjustment_id}/cancelar")
def adjustment_cancel(adjustment_id: int, request: Request, db: Session = Depends(get_db)):
    user = _operator(request, db)
    row = db.get(FinancialAdjustment, adjustment_id)
    if not row:
        return RedirectResponse(url="/quinzenas", status_code=303)
    row.status = "CANCELADO"; audit(db, user, "CANCELAR_AJUSTE", "AJUSTE", row.id, f"{row.kind} de {brl(row.amount)} cancelado"); db.commit()
    return RedirectResponse(url=f"/quinzenas/{row.payment_period_id}/ajustes", status_code=303)


@app.get("/calculos/{calc_id}", response_class=HTMLResponse)
def calculation_detail(calc_id: int, request: Request, db: Session = Depends(get_db)):
    calc = db.query(PaymentCalculation).options(
        joinedload(PaymentCalculation.employee), joinedload(PaymentCalculation.period),
        joinedload(PaymentCalculation.policy), joinedload(PaymentCalculation.lines),
    ).filter(PaymentCalculation.id == calc_id).first()
    if not calc:
        return RedirectResponse(url="/quinzenas", status_code=303)
    return templates.TemplateResponse("calculation_detail.html", {
        "request": request, "calc": calc, "status_labels": STATUS_LABELS,
    })


@app.get("/configuracoes", response_class=HTMLResponse)
def settings(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    config = db.get(PaymentCycleConfig, 1)
    return templates.TemplateResponse("settings.html", {
        "request": request, "config": config, "db_path": str(DB_PATH), "data_dir": str(DATA_DIR),
        "migration_source": MIGRATION_SOURCE,
        "employee_count": db.query(Employee).filter(Employee.in_scope == True).count(), "tariff_count": db.query(BaseTariff).count(),
        "holiday_count": db.query(HolidayRule).filter(HolidayRule.active == True).count(), "occurrence_count": db.query(Occurrence).count(),
    })


@app.post("/configuracoes/ciclo")
async def save_cycle_settings(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    try:
        cutoff = int(form.get("first_half_end_day") or 15)
    except ValueError:
        cutoff = 15
    cutoff = max(5, min(cutoff, 25))
    config = db.get(PaymentCycleConfig, 1)
    before_cutoff = config.first_half_end_day
    config.first_half_end_day = cutoff

    periods = db.query(PaymentPeriod).filter(PaymentPeriod.status == "not_processed").all()
    for period in periods:
        last_day = calendar.monthrange(period.year, period.month)[1]
        effective_cutoff = min(cutoff, last_day - 1)
        if period.half == 1:
            period.start_date = date(period.year, period.month, 1)
            period.end_date = date(period.year, period.month, effective_cutoff)
        else:
            period.start_date = date(period.year, period.month, effective_cutoff + 1)
            period.end_date = date(period.year, period.month, last_day)
    audit(db, user, "ALTERAR_CORTE_QUINZENAL", "CONFIGURACAO", 1, f"Corte da 1ª quinzena alterado de {before_cutoff} para {cutoff}")
    db.commit()
    return RedirectResponse(url="/configuracoes?saved=1", status_code=303)


@app.get("/dados/backup")
def download_backup(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return FileResponse(path=DB_PATH, filename=f"qs_ajuda_custos_backup_{stamp}.db", media_type="application/octet-stream")


@app.get("/homologacoes", response_class=HTMLResponse)
def homologations(request: Request, status: str = "pending", db: Session = Depends(get_db)):
    q = db.query(HomologationDecision)
    if status == "pending":
        q = q.filter(HomologationDecision.status != "approved")
    elif status == "approved":
        q = q.filter(HomologationDecision.status == "approved")
    rows = q.order_by(HomologationDecision.id).all()
    total = db.query(HomologationDecision).count()
    pending = pending_query(db).count()
    approved = total - pending
    return templates.TemplateResponse("homologations.html", {
        "request": request, "rows": rows, "filter_status": status,
        "total": total, "pending": pending, "approved": approved,
    })


@app.get("/homologacoes/{decision_id}", response_class=HTMLResponse)
def homologation_detail(decision_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(HomologationDecision, decision_id)
    if not item:
        return RedirectResponse(url="/homologacoes", status_code=303)
    rule = db.query(CostRule).options(joinedload(CostRule.options)).filter(CostRule.source_decision_id == decision_id).first()
    guidance = DECISION_GUIDANCE.get(decision_id, {})

    if rule:
        form_rule_kind = rule.rule_kind
        form_scope = rule.scope or ""
        form_notes = rule.notes or ""
        form_options = [{
            "label": o.label or "", "value": "" if o.amount is None else f"{o.amount:.2f}".replace(".", ","),
            "modal": o.modal or "", "origin": o.origin or "", "destination": o.destination or "",
            "condition": o.condition or "", "valid_from": o.valid_from or "",
        } for o in rule.options]
    else:
        form_rule_kind = guidance.get("rule_kind") or ("TARIFA" if item.source_table == "TARIFAS" else "EXCECAO_VT")
        form_scope = guidance.get("scope", "")
        form_notes = guidance.get("notes", "")
        form_options = guidance.get("options", [])

    while len(form_options) < 2:
        form_options.append({})

    return templates.TemplateResponse("homologation_detail.html", {
        "request": request, "item": item, "rule": rule, "guidance": guidance,
        "form_rule_kind": form_rule_kind, "form_scope": form_scope,
        "form_notes": form_notes, "form_options": form_options,
    })


@app.post("/homologacoes/{decision_id}/salvar")
async def save_homologation(decision_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    item = db.get(HomologationDecision, decision_id)
    if not item:
        return RedirectResponse(url="/homologacoes", status_code=303)

    form = await request.form()
    raw_action = (form.get("action") or "draft").strip()
    approve = raw_action in {"approve", "approve_next"}
    go_next = raw_action == "approve_next"
    rule_kind = (form.get("rule_kind") or "").strip()
    scope = (form.get("scope") or "").strip() or None
    notes = (form.get("notes") or "").strip() or None

    labels = form.getlist("option_label")
    values = form.getlist("option_value")
    modals = form.getlist("option_modal")
    origins = form.getlist("option_origin")
    destinations = form.getlist("option_destination")
    conditions = form.getlist("option_condition")
    valid_froms = form.getlist("option_valid_from")

    def at(seq, i):
        return (seq[i] if i < len(seq) else "").strip()

    def parse_money(text):
        text = (text or "").strip().replace("R$", "").replace(" ", "")
        if not text:
            return None
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text)

    rule = db.query(CostRule).options(joinedload(CostRule.options)).filter(CostRule.source_decision_id == decision_id).first()
    if not rule:
        rule = CostRule(source_decision_id=decision_id, source_sheet=item.source_sheet, source_row=item.source_row,
                        location=item.location, rule_kind=rule_kind or "REGRA", active=False)
        db.add(rule)
        db.flush()
    else:
        rule.options.clear()

    rule.rule_kind = rule_kind or "REGRA"
    rule.location = item.location
    rule.scope = scope
    rule.notes = notes
    rule.active = approve

    max_len = max(len(labels), len(values), len(modals), len(origins), len(destinations), len(conditions), len(valid_froms), 0)
    for i in range(max_len):
        label = at(labels, i); value_text = at(values, i); modal = at(modals, i); origin = at(origins, i)
        destination = at(destinations, i); condition = at(conditions, i); valid_from = at(valid_froms, i)
        if not any([label, value_text, modal, origin, destination, condition, valid_from]):
            continue
        db.add(CostRuleOption(rule_id=rule.id, sequence=i + 1, label=label or None, amount=parse_money(value_text),
                              modal=modal or None, origin=origin or None, destination=destination or None,
                              condition=condition or None, valid_from=valid_from or None))

    item.rule_kind = rule.rule_kind; item.scope = scope; item.notes = notes
    if approve:
        item.status = "approved"; item.decided_at = datetime.utcnow()
    else:
        item.status = "draft"; item.decided_at = None
    db.commit()

    if approve and go_next:
        nxt = pending_query(db).filter(HomologationDecision.id > decision_id).order_by(HomologationDecision.id).first()
        if not nxt:
            nxt = pending_query(db).order_by(HomologationDecision.id).first()
        if nxt:
            return RedirectResponse(url=f"/homologacoes/{nxt.id}", status_code=303)
        return RedirectResponse(url="/regras", status_code=303)
    return RedirectResponse(url=f"/homologacoes/{decision_id}", status_code=303)


@app.post("/homologacoes/{decision_id}/reabrir")
def reopen_homologation(decision_id: int, request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    item = db.get(HomologationDecision, decision_id)
    if item:
        item.status = "draft"; item.decided_at = None
        rule = db.query(CostRule).filter(CostRule.source_decision_id == decision_id).first()
        if rule:
            rule.active = False
        db.commit()
    return RedirectResponse(url=f"/homologacoes/{decision_id}", status_code=303)


@app.get("/regras", response_class=HTMLResponse)
def rules(request: Request, db: Session = Depends(get_db)):
    rows = db.query(CostRule).options(joinedload(CostRule.options)).filter(CostRule.active == True).order_by(CostRule.id.desc()).all()
    return templates.TemplateResponse("rules.html", {"request": request, "rows": rows})


@app.get("/admin/usuarios", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    rows = db.query(UserAccount).order_by(UserAccount.display_name).all()
    return templates.TemplateResponse("admin_users.html", {"request": request, "rows": rows})


@app.post("/admin/usuarios/novo")
async def admin_user_create(request: Request, db: Session = Depends(get_db)):
    user = _admin(request, db)
    form = await request.form()
    username=(form.get("username") or "").strip().lower(); display=(form.get("display_name") or "").strip()
    password=str(form.get("password") or ""); role=(form.get("role") or "CONSULTA").strip().upper()
    if len(username)<3 or not display or len(password)<8 or role not in {"ADMIN","BENEFICIOS","CONSULTA"}:
        return RedirectResponse(url="/admin/usuarios?erro=1", status_code=303)
    if db.query(UserAccount).filter(UserAccount.username==username).first():
        return RedirectResponse(url="/admin/usuarios?erro=duplicado", status_code=303)
    row=UserAccount(username=username, display_name=display, password_hash=hash_password(password), role=role, active=True)
    db.add(row); db.flush(); audit(db,user,"CRIAR_USUARIO","USUARIO",row.id,f"Usuário {display} criado com perfil {role}"); db.commit()
    return RedirectResponse(url="/admin/usuarios?saved=1", status_code=303)


@app.post("/admin/usuarios/{user_id}/toggle")
def admin_user_toggle(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor=_admin(request,db); row=db.get(UserAccount,user_id)
    if row and row.id != actor.id:
        row.active=not row.active; audit(db,actor,"ALTERAR_USUARIO","USUARIO",row.id,f"Usuário {row.display_name}: {'ativado' if row.active else 'desativado'}"); db.commit()
    return RedirectResponse(url="/admin/usuarios",status_code=303)


@app.get("/admin/auditoria", response_class=HTMLResponse)
def admin_audit(request: Request, q: str = "", db: Session = Depends(get_db)):
    _admin(request, db)
    query=db.query(AuditLog)
    if q.strip():
        term=f"%{q.strip()}%"; query=query.filter(or_(AuditLog.username.ilike(term),AuditLog.summary.ilike(term),AuditLog.action.ilike(term)))
    rows=query.order_by(AuditLog.id.desc()).limit(500).all()
    return templates.TemplateResponse("audit.html", {"request":request,"rows":rows,"q":q})


@app.get("/sobre", response_class=HTMLResponse)
def about(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("about.html", {
        "request": request, "db_path": str(DB_PATH), "migration_source": MIGRATION_SOURCE,
        "employees": db.query(Employee).count(), "tariffs": db.query(BaseTariff).count(),
        "holidays": db.query(HolidayRule).filter(HolidayRule.active == True).count(),
    })


# ═══════════════════════════════════════════════════════════════════════════
# TIMELINE DE ROTEIRO DO PROMOTOR (visualização semanal)
# ═══════════════════════════════════════════════════════════════════════════
DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

@app.get("/timeline-roteiro", response_class=HTMLResponse)
def timeline_roteiro(request: Request, q: str = "", id: int = 0, semanas: int = 4, db: Session = Depends(get_db)):
    user = current_user(request, required=False)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # 1. Encontrar o promotor (por ID ou por busca por nome)
    emp = None
    if id:
        emp = db.query(Employee).filter(Employee.id == int(id)).first()
    if not emp and q.strip():
        qnorm = f"%{q.strip()}%"
        emp = db.query(Employee).filter(Employee.name.ilike(qnorm)).first()

    # 2. Montar opções (lista de todos os promotores com rota para o select)
    promotores_com_rota = db.query(
        PromoterRoute.promoter_name,
        func.count(PromoterRoute.id).label("cnt")
    ).group_by(PromoterRoute.promoter_name).order_by(PromoterRoute.promoter_name).all()
    opcoes_promotores = [(p[0], p[1]) for p in promotores_com_rota]

    if not emp:
        return templates.TemplateResponse("timeline.html", {
            "request": request,
            "promotor": None,
            "q": q,
            "semanas": semanas,
            "opcoes_promotores": opcoes_promotores,
            "semanas_visiveis": [],
            "rotas_por_dia": {d: [] for d in DIAS_SEMANA},
            "total_pdvs": 0,
            "total_visitas_semana": 0,
        })

    # 3. Pegar as rotas do promotor
    nome_promotor = emp.name
    rotas = db.query(PromoterRoute).filter(
        or_(PromoterRoute.employee_id == emp.id, PromoterRoute.promoter_name == nome_promotor)
    ).all()

    # 4. Agrupar rotas por dia da semana
    dias_fields_map = {
        "Segunda": "monday", "Terça": "tuesday", "Quarta": "wednesday",
        "Quinta": "thursday", "Sexta": "friday", "Sábado": "saturday", "Domingo": "sunday"
    }
    rotas_por_dia = {d: [] for d in DIAS_SEMANA}
    total_visitas_semana = 0

    for r in rotas:
        for dia_label, field_name in dias_fields_map.items():
            dia_conteudo = getattr(r, field_name, None)
            if dia_conteudo and str(dia_conteudo).strip():
                rotas_por_dia[dia_label].append({
                    "id": r.id,
                    "pdv": r.pdv_name,
                    "cidade": r.city or "",
                    "uf": r.uf or "",
                    "endereco": r.address or "",
                    "frequencia": r.frequency or 1,
                    "industrias": [m.strip() for m in str(dia_conteudo).split("|") if m.strip()]
                })
                total_visitas_semana += 1

    total_pdvs = len(rotas)

    # 5. Montar as semanas visíveis no calendário (semanas passadas + futuras)
    hoje = date.today()
    # Ajustar para começar no domingo anterior? NÃO → começamos na Segunda da semana corrente
    dias_para_segunda = (hoje.weekday() - 0)  # segunda é 0
    inicio_semana_atual = hoje.fromordinal(hoje.toordinal() - dias_para_segunda)

    semanas = max(2, min(int(semanas), 8))
    # Metade antes, metade depois (se for par, inclui semana atual no meio)
    semanas_antes = semanas // 2
    semanas_visiveis = []
    for s in range(-semanas_antes, semanas - semanas_antes):
        data_inicio = date.fromordinal(inicio_semana_atual.toordinal() + (s * 7))
        semana = {
            "idx": s,
            "label": f"Semana {data_inicio.strftime('%d/%m')}",
            "inicio": data_inicio,
            "dias": []
        }
        for i, dia_label in enumerate(DIAS_SEMANA[:-1]):  # não mostramos Domingo por enquanto
            d = date.fromordinal(data_inicio.toordinal() + i)
            semana["dias"].append({
                "label": dia_label,
                "data": d,
                "data_str": d.strftime("%d/%m"),
                "data_iso": d.isoformat(),
                "dia_num": d.day,
                "eh_hoje": (d == hoje),
                "passado": (d < hoje),
                "futuro": (d > hoje),
            })
        semanas_visiveis.append(semana)

    # 6. Dados adicionais do colaborador
    supervisor_short = ""
    if emp.supervisor:
        sp = emp.supervisor.split()
        supervisor_short = sp[0] + " " + sp[-1] if len(sp) > 1 else emp.supervisor

    return templates.TemplateResponse("timeline.html", {
        "request": request,
        "promotor": emp,
        "supervisor_short": supervisor_short,
        "q": q,
        "semanas": semanas,
        "opcoes_promotores": opcoes_promotores,
        "semanas_visiveis": semanas_visiveis,
        "rotas_por_dia": rotas_por_dia,
        "total_pdvs": total_pdvs,
        "total_visitas_semana": total_visitas_semana,
        "media_visitas_por_dia": round(total_visitas_semana / 6, 1) if total_visitas_semana else 0,
        "dias_labels": DIAS_SEMANA[:-1],
    })
