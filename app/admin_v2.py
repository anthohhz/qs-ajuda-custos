from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from .auth import audit, hash_password, require_role
from .db import get_db
from .models import Company, Department, UserAccount


BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
router = APIRouter()

POSITIONS = [
    ("LIDER", "Líder"),
    ("ADJUNTO", "Adjunto"),
    ("OPERADOR", "Operador"),
    ("CONSULTA", "Consulta"),
]

ROLES = [
    ("ADMIN", "Administrador"),
    ("BENEFICIOS", "Operação de benefícios"),
    ("CONSULTA", "Consulta"),
]


def _admin(request: Request, db: Session):
    return require_role(request, db, {"ADMIN"})


def _department_from_form(db: Session, raw):
    try:
        department_id = int(raw)
    except Exception:
        return None
    return db.query(Department).filter(Department.id == department_id, Department.active == True).first()


def _clean_code(raw: str | None, max_len: int = 30) -> str:
    value = re.sub(r"[^A-Z0-9_]+", "_", (raw or "").strip().upper()).strip("_")
    return value[:max_len]


@router.get("/admin/usuarios", response_class=HTMLResponse)
def admin_users_v2(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    rows = db.query(UserAccount).options(joinedload(UserAccount.department)).order_by(UserAccount.display_name).all()
    departments = db.query(Department).filter(Department.active == True).order_by(Department.name).all()
    return templates.TemplateResponse("admin_users_v2.html", {
        "request": request,
        "rows": rows,
        "departments": departments,
        "positions": POSITIONS,
        "roles": ROLES,
    })


@router.post("/admin/usuarios/novo")
async def admin_user_create_v2(request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    form = await request.form()
    username = (form.get("username") or "").strip().lower()
    display = (form.get("display_name") or "").strip()
    password = str(form.get("password") or "")
    role = (form.get("role") or "CONSULTA").strip().upper()
    position = (form.get("position") or "OPERADOR").strip().upper()
    department = _department_from_form(db, form.get("department_id"))

    if len(username) < 3 or not display or len(password) < 8 or role not in {x[0] for x in ROLES} or position not in {x[0] for x in POSITIONS}:
        return RedirectResponse(url="/admin/usuarios?erro=dados", status_code=303)
    if db.query(UserAccount).filter(UserAccount.username == username).first():
        return RedirectResponse(url="/admin/usuarios?erro=duplicado", status_code=303)

    row = UserAccount(
        username=username,
        display_name=display,
        password_hash=hash_password(password),
        role=role,
        department_id=department.id if department else None,
        position=position,
        active=True,
    )
    db.add(row); db.flush()
    audit(db, actor, "CRIAR_USUARIO", "USUARIO", row.id, f"Usuário {display} criado · setor {department.name if department else 'não definido'} · função {position}")
    db.commit()
    return RedirectResponse(url="/admin/usuarios?saved=1", status_code=303)


@router.post("/admin/usuarios/{user_id}/estrutura")
async def admin_user_structure(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    row = db.get(UserAccount, user_id)
    if not row:
        return RedirectResponse(url="/admin/usuarios?erro=usuario", status_code=303)
    form = await request.form()
    role = (form.get("role") or row.role).strip().upper()
    position = (form.get("position") or "CONSULTA").strip().upper()
    department = _department_from_form(db, form.get("department_id"))
    if role not in {x[0] for x in ROLES} or position not in {x[0] for x in POSITIONS}:
        return RedirectResponse(url="/admin/usuarios?erro=dados", status_code=303)

    row.role = role
    row.position = position
    row.department_id = department.id if department else None
    audit(db, actor, "ALTERAR_ESTRUTURA_USUARIO", "USUARIO", row.id, f"{row.display_name} · {department.name if department else 'sem setor'} · {position} · {role}")
    db.commit()
    return RedirectResponse(url="/admin/usuarios?updated=1", status_code=303)


@router.post("/admin/usuarios/{user_id}/toggle")
def admin_user_toggle_v2(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    row = db.get(UserAccount, user_id)
    if row and row.id != actor.id:
        row.active = not row.active
        audit(db, actor, "ALTERAR_USUARIO", "USUARIO", row.id, f"Usuário {row.display_name}: {'ativado' if row.active else 'desativado'}")
        db.commit()
    return RedirectResponse(url="/admin/usuarios", status_code=303)


@router.get("/admin/empresas", response_class=HTMLResponse)
def admin_companies(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    rows = db.query(Company).order_by(Company.name).all()
    return templates.TemplateResponse("admin_companies_v2.html", {"request": request, "rows": rows})


@router.post("/admin/empresas/nova")
async def admin_company_create(request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    form = await request.form()
    code = _clean_code(form.get("code"), 30)
    name = (form.get("name") or "").strip()
    cnpj = re.sub(r"\D", "", str(form.get("cnpj") or "")) or None
    notes = (form.get("notes") or "").strip() or None
    if not code or not name or (cnpj and len(cnpj) != 14):
        return RedirectResponse(url="/admin/empresas?erro=dados", status_code=303)
    if db.query(Company).filter(Company.code == code).first():
        return RedirectResponse(url="/admin/empresas?erro=duplicado", status_code=303)
    row = Company(code=code, name=name, cnpj=cnpj, notes=notes, active=True)
    db.add(row); db.flush()
    audit(db, actor, "CRIAR_EMPRESA", "EMPRESA", row.id, f"Empresa {name} ({code}) criada")
    db.commit()
    return RedirectResponse(url="/admin/empresas?saved=1", status_code=303)


@router.post("/admin/empresas/{company_id}/toggle")
def admin_company_toggle(company_id: int, request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    row = db.get(Company, company_id)
    if row:
        row.active = not row.active
        audit(db, actor, "ALTERAR_EMPRESA", "EMPRESA", row.id, f"Empresa {row.name}: {'ativada' if row.active else 'inativada'}")
        db.commit()
    return RedirectResponse(url="/admin/empresas", status_code=303)


@router.get("/admin/setores", response_class=HTMLResponse)
def admin_departments(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    rows = db.query(Department).order_by(Department.name).all()
    return templates.TemplateResponse("admin_departments_v2.html", {"request": request, "rows": rows})


@router.post("/admin/setores/novo")
async def admin_department_create(request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    form = await request.form()
    code = _clean_code(form.get("code"), 40)
    name = (form.get("name") or "").strip()
    if not code or not name:
        return RedirectResponse(url="/admin/setores?erro=dados", status_code=303)
    if db.query(Department).filter(Department.code == code).first():
        return RedirectResponse(url="/admin/setores?erro=duplicado", status_code=303)
    row = Department(code=code, name=name, active=True)
    db.add(row); db.flush()
    audit(db, actor, "CRIAR_SETOR", "SETOR", row.id, f"Setor {name} criado")
    db.commit()
    return RedirectResponse(url="/admin/setores?saved=1", status_code=303)


def install_admin_v2() -> None:
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "admin_v2_installed", False):
        return
    legacy_paths = {
        "/admin/usuarios",
        "/admin/usuarios/novo",
        "/admin/usuarios/{user_id}/toggle",
    }
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) not in legacy_paths]
    app.include_router(router)
    app.state.admin_v2_installed = True
