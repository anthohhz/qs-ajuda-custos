from __future__ import annotations

import mimetypes
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, or_
from sqlalchemy.orm import Mapped, Session, joinedload, mapped_column

from .auth import audit, current_user
from .calculation_engine import process_period
from .db import Base, DATA_DIR, get_db
from .models import Employee, Occurrence, PaymentCalculation, PaymentPeriod, UserAccount


BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE / "templates")
router = APIRouter()

DOCUMENT_ROOT = DATA_DIR / "uploads" / "ocorrencias"
DOCUMENT_ROOT.mkdir(parents=True, exist_ok=True)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


class OccurrenceDocument(Base):
    __tablename__ = "occurrence_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurrence_id: Mapped[int] = mapped_column(ForeignKey("occurrences.id"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RECEBIDO", index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


EVENT_CONFIG = {
    "FÉRIAS": {"label": "Férias", "icon": "☀", "impact": "REMOVE_DAY", "help": "Retira VT e alimentação dos dias de férias."},
    "FALTA": {"label": "Falta", "icon": "!", "impact": "BLOCK", "help": "Registre se foi integral ou parcial. O impacto financeiro fica em conferência."},
    "ATESTADO": {"label": "Atestado", "icon": "+", "impact": "BLOCK", "help": "Registre o período e anexe o documento quando disponível."},
    "DECLARAÇÃO": {"label": "Declaração", "icon": "▤", "impact": "BLOCK", "help": "Registre a data/período e anexe o comprovante quando houver."},
    "LICENÇA": {"label": "Licença", "icon": "▣", "impact": "BLOCK", "help": "Evento de RH. Fica visível no calendário e segue para conferência."},
    "AFASTAMENTO": {"label": "Afastamento", "icon": "◷", "impact": "BLOCK", "help": "Evento de RH. Fica visível no calendário e segue para conferência."},
}
AJUDA_TYPES = ["FÉRIAS", "FALTA", "ATESTADO", "DECLARAÇÃO"]
RH_TYPES = ["FÉRIAS", "FALTA", "ATESTADO", "DECLARAÇÃO", "LICENÇA", "AFASTAMENTO"]
ALL_TYPES = list(EVENT_CONFIG)


def _department_code(user: UserAccount | None) -> str:
    department = getattr(user, "department", None) if user else None
    return (getattr(department, "code", "") or "").strip().upper()


def _allowed_types(user: UserAccount | None) -> list[str]:
    if not user:
        return []
    role = (user.role or "").upper()
    department = _department_code(user)
    if role == "ADMIN" or department == "SISTEMAS":
        return ALL_TYPES
    if department == "RH":
        return RH_TYPES
    if role == "BENEFICIOS" or department == "AJUDA_CUSTOS":
        return AJUDA_TYPES
    return []


def _editor(request: Request, db: Session) -> UserAccount:
    user = current_user(request, db)
    if not user or not _allowed_types(user):
        raise HTTPException(status_code=403, detail="Sem permissão para registrar ocorrências.")
    return user


def _viewer(request: Request, db: Session) -> UserAccount:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    return user


def _safe_return_to(value: str | None, fallback: str) -> str:
    value = (value or "").strip()
    return value if value.startswith("/") and not value.startswith("//") and "://" not in value else fallback


def _parse_date(raw) -> date | None:
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except Exception:
        return None


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


def _sanitize_original_name(filename: str | None) -> str:
    name = Path(filename or "documento").name.strip() or "documento"
    return re.sub(r"[^A-Za-z0-9À-ÿ._ -]", "_", name)[:255]


async def _save_document(db: Session, occurrence: Occurrence, user: UserAccount, upload) -> OccurrenceDocument | None:
    if not upload or not getattr(upload, "filename", None):
        return None
    original_name = _sanitize_original_name(upload.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValueError("Formato não permitido")
    content = await upload.read()
    if not content:
        raise ValueError("Arquivo vazio")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Arquivo maior que 10 MB")

    ref_date = occurrence.start_date or date.today()
    folder = DOCUMENT_ROOT / f"{ref_date.year:04d}" / f"{ref_date.month:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    path = folder / stored_name
    path.write_bytes(content)
    document = OccurrenceDocument(
        occurrence_id=occurrence.id,
        original_name=original_name,
        stored_name=stored_name,
        relative_path=path.relative_to(DATA_DIR).as_posix(),
        mime_type=getattr(upload, "content_type", None) or mimetypes.guess_type(original_name)[0],
        size_bytes=len(content),
        status="RECEBIDO",
        created_by_user_id=user.id,
    )
    db.add(document)
    return document


@router.get("/ocorrencias", response_class=HTMLResponse)
def occurrences_v2(request: Request, q: str = "", tipo: str = "", status: str = "", db: Session = Depends(get_db)):
    user = _viewer(request, db)
    query = db.query(Occurrence).options(joinedload(Occurrence.employee))
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(Occurrence.employee_name_text.ilike(term), Occurrence.notes.ilike(term)))
    if tipo:
        query = query.filter(Occurrence.kind == tipo)
    if status:
        query = query.filter(Occurrence.status == status)
    rows = query.order_by(Occurrence.id.desc()).limit(500).all()
    docs_by_occurrence: dict[int, list[OccurrenceDocument]] = {}
    ids = [row.id for row in rows]
    if ids:
        for doc in db.query(OccurrenceDocument).filter(OccurrenceDocument.occurrence_id.in_(ids)).order_by(OccurrenceDocument.created_at.desc()).all():
            docs_by_occurrence.setdefault(doc.occurrence_id, []).append(doc)
    types = [x[0] for x in db.query(Occurrence.kind).distinct().order_by(Occurrence.kind).all()]
    return templates.TemplateResponse("occurrences_v2.html", {
        "request": request,
        "rows": rows,
        "q": q,
        "tipo": tipo,
        "status_filter": status,
        "types": types,
        "docs_by_occurrence": docs_by_occurrence,
        "can_edit_occurrence": bool(_allowed_types(user)),
        "allowed_types": _allowed_types(user),
    })


@router.get("/ocorrencias/nova", response_class=HTMLResponse)
def occurrence_new_v2(request: Request, employee_id: int | None = None, return_to: str | None = None, db: Session = Depends(get_db)):
    user = _editor(request, db)
    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).order_by(Employee.name).all()
    fallback = f"/colaboradores/{employee_id}" if employee_id else "/ocorrencias"
    allowed = _allowed_types(user)
    return templates.TemplateResponse("occurrence_new_v2.html", {
        "request": request,
        "employees": employees,
        "selected_employee_id": employee_id,
        "event_types": [(kind, EVENT_CONFIG[kind]) for kind in allowed],
        "return_to": _safe_return_to(return_to, fallback),
    })


@router.post("/ocorrencias/nova")
async def occurrence_create_v2(request: Request, db: Session = Depends(get_db)):
    user = _editor(request, db)
    form = await request.form()
    try:
        employee_id = int(form.get("employee_id"))
    except Exception:
        return RedirectResponse(url="/ocorrencias/nova?erro=colaborador", status_code=303)
    employee = db.get(Employee, employee_id)
    if not employee:
        return RedirectResponse(url="/ocorrencias/nova?erro=colaborador", status_code=303)

    kind = (form.get("kind") or "").strip().upper()
    if kind not in _allowed_types(user):
        raise HTTPException(status_code=403, detail="Tipo de ocorrência não permitido para este usuário.")
    start = _parse_date(form.get("start_date"))
    end = _parse_date(form.get("end_date")) or start
    if not start or not end or end < start:
        return RedirectResponse(url=f"/ocorrencias/nova?employee_id={employee.id}&erro=periodo", status_code=303)

    detail = None
    if kind == "FALTA":
        absence_mode = (form.get("absence_mode") or "INTEGRAL").strip().upper()
        if absence_mode not in {"INTEGRAL", "PARCIAL"}:
            absence_mode = "INTEGRAL"
        if absence_mode == "PARCIAL":
            start_time = (form.get("start_time") or "").strip()
            end_time = (form.get("end_time") or "").strip()
            if not start_time or not end_time:
                return RedirectResponse(url=f"/ocorrencias/nova?employee_id={employee.id}&erro=horario", status_code=303)
            detail = f"PARCIAL {start_time}–{end_time}"
        else:
            detail = "INTEGRAL"

    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    occurrence = Occurrence(
        source_key=f"MANUAL_V2:{employee.id}:{stamp}",
        employee_id=employee.id,
        employee_name_text=employee.name,
        kind=kind,
        start_date=start,
        end_date=end,
        impact_mode=EVENT_CONFIG[kind]["impact"],
        quantity_delta=None,
        quantity_original=detail,
        notes=(form.get("notes") or "").strip() or None,
        source="MANUAL",
        status="ATIVA",
    )
    db.add(occurrence)
    db.flush()
    try:
        document = await _save_document(db, occurrence, user, form.get("document"))
    except ValueError:
        db.rollback()
        return RedirectResponse(url=f"/ocorrencias/nova?employee_id={employee.id}&erro=documento", status_code=303)

    audit(db, user, "CRIAR_OCORRENCIA_V2", "OCORRENCIA", occurrence.id, f"{kind} registrada para {employee.name}" + (" com documento" if document else ""))
    db.commit()
    _reprocess_open_periods(db, start, end)
    fallback = f"/colaboradores/{employee.id}?occurrence_saved=1"
    return RedirectResponse(url=_safe_return_to(form.get("return_to"), fallback), status_code=303)


@router.post("/ocorrencias/{occurrence_id}/documentos")
async def occurrence_add_document(occurrence_id: int, request: Request, db: Session = Depends(get_db)):
    user = _editor(request, db)
    row = db.get(Occurrence, occurrence_id)
    if not row or row.status == "CANCELADA":
        return RedirectResponse(url="/ocorrencias?erro=ocorrencia", status_code=303)
    form = await request.form()
    try:
        document = await _save_document(db, row, user, form.get("document"))
        if not document:
            raise ValueError("Selecione um documento")
    except ValueError:
        db.rollback()
        return RedirectResponse(url="/ocorrencias?erro=documento", status_code=303)
    db.flush()
    audit(db, user, "ANEXAR_DOCUMENTO", "OCORRENCIA", row.id, f"Documento anexado à ocorrência {row.kind} de {row.employee_name_text or 'colaborador'}")
    db.commit()
    return RedirectResponse(url=_safe_return_to(form.get("return_to"), "/ocorrencias?documento=1"), status_code=303)


@router.get("/ocorrencias/{occurrence_id}/documentos/{document_id}")
def occurrence_document_download(occurrence_id: int, document_id: int, request: Request, db: Session = Depends(get_db)):
    user = _viewer(request, db)
    department = _department_code(user)
    if user.role not in {"ADMIN", "BENEFICIOS"} and department not in {"SISTEMAS", "AJUDA_CUSTOS", "RH"}:
        raise HTTPException(status_code=403, detail="Sem permissão para visualizar este documento.")
    document = db.query(OccurrenceDocument).filter(
        OccurrenceDocument.id == document_id,
        OccurrenceDocument.occurrence_id == occurrence_id,
    ).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    root = DATA_DIR.resolve()
    path = (DATA_DIR / document.relative_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(path=path, filename=document.original_name, media_type=document.mime_type or "application/octet-stream")


@router.post("/ocorrencias/{occurrence_id}/excluir")
def occurrence_cancel_v2(occurrence_id: int, request: Request, db: Session = Depends(get_db)):
    user = _editor(request, db)
    row = db.get(Occurrence, occurrence_id)
    if row and row.source == "MANUAL" and row.status != "CANCELADA":
        start, end = row.start_date, row.end_date or row.start_date
        row.status = "CANCELADA"
        audit(db, user, "CANCELAR_OCORRENCIA", "OCORRENCIA", row.id, f"{row.kind} cancelada para {row.employee_name_text or 'colaborador'}")
        db.commit()
        if start:
            _reprocess_open_periods(db, start, end)
    return RedirectResponse(url="/ocorrencias", status_code=303)


def install_occurrence_v2() -> None:
    """Substitui somente as rotas legadas de ocorrência durante a migração V0.9.3."""
    from . import main as main_module

    app = main_module.app
    if getattr(app.state, "occurrence_v2_installed", False):
        return
    legacy_paths = {"/ocorrencias", "/ocorrencias/nova", "/ocorrencias/{occurrence_id}/excluir"}
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) not in legacy_paths]
    app.include_router(router)
    app.state.occurrence_v2_installed = True
