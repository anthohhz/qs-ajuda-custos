import calendar
import re
from datetime import date
from .db import Base, engine, SessionLocal
from .models import (
    HomologationDecision, PaymentCycleConfig, PaymentPeriod,
    Company, Department, Employee, BaseTariff, Holiday, HolidayRule,
    CalculationPolicy, Occurrence,
)
from .seed_data import PENDING_DECISIONS
from .operational_seed_data import EMPLOYEE_SNAPSHOT, BASE_TARIFFS, HOLIDAY_SNAPSHOT, HISTORICAL_OCCURRENCES
from .calculation_engine import norm_text, canonical_city
from .holiday_2026_data import (
    NATIONAL_HOLIDAYS_2026, FEDERAL_OPTIONAL_DAYS_2026, STATE_HOLIDAYS_2026,
    FEDERAL_SOURCE, STATE_SOURCE,
)


QS_FIRST_HALF_END_DAY = 17

QS_DEPARTMENTS = [
    ("SISTEMAS", "Sistemas"),
    ("AJUDA_CUSTOS", "Ajuda de Custos"),
    ("RH", "RH"),
    ("COMERCIAL", "Comercial"),
]

QS_COMPANIES = [
    ("MAM", "MAM"),
    ("KNE", "KNE"),
    ("REC", "REC"),
    ("RMV", "RMV"),
    ("QS", "QS"),
    ("DR", "DR"),
]


def ensure_periods(db, year: int, month: int, cutoff: int):
    """Garante as duas quinzenas do mês sem alterar períodos históricos fechados.

    Regra oficial QS V0.9.3:
    - 1ª quinzena: 01 até 17, inclusive;
    - 2ª quinzena: 18 até o último dia do mês.

    Períodos existentes que ainda estejam abertos podem ter suas datas corrigidas para
    refletir a regra vigente. Uma quinzena fechada nunca é reescrita por esta rotina.
    """
    last_day = calendar.monthrange(year, month)[1]
    cutoff = max(1, min(cutoff, last_day - 1))
    specs = [
        (1, date(year, month, 1), date(year, month, cutoff)),
        (2, date(year, month, cutoff + 1), date(year, month, last_day)),
    ]
    for half, start, end in specs:
        row = db.query(PaymentPeriod).filter_by(year=year, month=month, half=half).first()
        if not row:
            db.add(PaymentPeriod(year=year, month=month, half=half, start_date=start, end_date=end))
            continue

        # Histórico fechado é imutável. Somente períodos ainda abertos acompanham
        # uma correção da regra de calendário.
        if row.status != "closed" and (row.start_date != start or row.end_date != end):
            row.start_date = start
            row.end_date = end


def _seed_corporate_structure(db):
    """Cria somente os cadastros-base; empresas e setores continuam expansíveis via CRUD."""
    for code, name in QS_DEPARTMENTS:
        row = db.query(Department).filter(Department.code == code).first()
        if not row:
            db.add(Department(code=code, name=name, active=True))

    for code, name in QS_COMPANIES:
        row = db.query(Company).filter(Company.code == code).first()
        if not row:
            db.add(Company(code=code, name=name, active=True))

    db.flush()


def _seed_employees(db):
    if db.query(Employee).count() > 0:
        return
    for row in EMPLOYEE_SNAPSHOT:
        db.add(Employee(**row, source_system="Puzzle snapshot", active=True, in_scope=(row.get("benefit_group") != "ADMIN_OUTROS")))
    db.flush()


def _seed_tariffs(db):
    if db.query(BaseTariff).count() > 0:
        return
    for row in BASE_TARIFFS:
        valid_from = date.fromisoformat(str(row["valid_from"])[:10]) if row.get("valid_from") else None
        db.add(BaseTariff(
            legacy_id=row["legacy_id"], city=row["city"], city_norm=canonical_city(row["city"]),
            uf=row["uf"], modal=row.get("modal"), amount=row.get("amount"), valid_from=valid_from,
            notes=row.get("notes"), original_value=row.get("original_value"), source_sheet=row.get("source_sheet"),
            source_row=row.get("source_row"), status=row.get("status") or "OK", review_reason=row.get("review_reason"),
        ))
    db.flush()


def _seed_holidays(db):
    if db.query(Holiday).count() > 0:
        return
    for row in HOLIDAY_SNAPSHOT:
        if not row.get("region") or not row.get("uf"):
            continue
        db.add(Holiday(
            legacy_id=row["legacy_id"], date=date.fromisoformat(row["date"]),
            region=row["region"], region_norm=canonical_city(row["region"]), uf=row["uf"],
            reason=row.get("reason") or "Feriado", source_year=row.get("source_year"), status=row.get("status") or "OK",
        ))
    db.flush()


def _seed_holiday_rules_2026(db):
    """Cria a camada operacional de feriados sem alterar o legado migrado."""
    # Feriados nacionais: um único registro NATIONAL se aplica a todas as UFs.
    for date_text, name in NATIONAL_HOLIDAYS_2026:
        key = f"OFICIAL2026:NATIONAL:{date_text}:{norm_text(name)}"
        if not db.query(HolidayRule).filter(HolidayRule.source_key == key).first():
            db.add(HolidayRule(
                source_key=key, date=date.fromisoformat(date_text), name=name, scope_type="NATIONAL",
                category="FERIADO", affects_calculation=True, source="OFICIAL_2026",
                source_ref=FEDERAL_SOURCE, active=True,
            ))

    # Pontos facultativos federais ficam visíveis, mas nunca retiram dia automaticamente.
    for date_text, name in FEDERAL_OPTIONAL_DAYS_2026:
        key = f"OFICIAL2026:OPTIONAL:{date_text}:{norm_text(name)}"
        if not db.query(HolidayRule).filter(HolidayRule.source_key == key).first():
            db.add(HolidayRule(
                source_key=key, date=date.fromisoformat(date_text), name=name, scope_type="NATIONAL",
                category="PONTO_FACULTATIVO", affects_calculation=False, source="OFICIAL_2026",
                source_ref=FEDERAL_SOURCE,
                notes="Visível no calendário para conferência; não altera o cálculo da ajuda de custos por padrão.", active=True,
            ))

    for uf, holidays in STATE_HOLIDAYS_2026.items():
        for date_text, name, notes in holidays:
            key = f"OFICIAL2026:STATE:{uf}:{date_text}:{norm_text(name)}"
            if not db.query(HolidayRule).filter(HolidayRule.source_key == key).first():
                db.add(HolidayRule(
                    source_key=key, date=date.fromisoformat(date_text), name=name, scope_type="STATE", uf=uf,
                    category="FERIADO", affects_calculation=True, source="OFICIAL_2026",
                    source_ref=STATE_SOURCE, notes=notes, active=True,
                ))

    db.flush()

    # O histórico municipal que já veio do Excel também entra na nova camada, de forma idempotente.
    for old in db.query(Holiday).all():
        if not old.uf or not old.region:
            continue
        key = f"MIGRACAO_HOLIDAY:{old.legacy_id}"
        if db.query(HolidayRule).filter(HolidayRule.source_key == key).first():
            continue
        db.add(HolidayRule(
            source_key=key, date=old.date, name=old.reason or "Feriado municipal", scope_type="MUNICIPAL",
            uf=old.uf, city=old.region, city_norm=canonical_city(old.region), category="FERIADO",
            affects_calculation=(old.status == "OK"), source="MIGRACAO_EXCEL",
            source_ref=f"Calendário histórico migrado · registro {old.legacy_id}", active=True,
        ))
    db.flush()


def _seed_policies(db):
    if db.query(CalculationPolicy).count() > 0:
        return
    policies = [
        {
            "policy_key": "PROMOTOR_44H", "label": "Promotor · 44h", "workload_key": "44H",
            "weekdays": "0,1,2,3,4,5", "vt_per_day": 2.0, "approved": False,
            "notes": "Sugestão inicial para simulação: segunda a sábado e 2 VT/dia. Homologar antes de usar financeiramente.",
        },
        {
            "policy_key": "PROMOTOR_40H", "label": "Promotor · 40h", "workload_key": "40H",
            "weekdays": "0,1,2,3,4", "vt_per_day": 2.0, "approved": False,
            "notes": "Sugestão inicial para simulação: segunda a sexta e 2 VT/dia. Homologar antes de usar financeiramente.",
        },
        {
            "policy_key": "PROMOTOR_22H", "label": "Promotor · 22h", "workload_key": "22H",
            "weekdays": "0,1,2,3,4", "vt_per_day": 2.0, "approved": False,
            "notes": "Sugestão inicial para simulação: segunda a sexta e 2 VT/dia. Ajustar conforme a regra real da QSPROMO.",
        },
        {
            "policy_key": "PROMOTOR_MEI_6H", "label": "Promotor MEI · 6h", "workload_key": "6H_MEI",
            "weekdays": "0,1,2,3,4,5", "vt_per_day": 2.0, "approved": False,
            "notes": "A carga de 6h foi informada pela QSPROMO. Dias da semana e quantidade de VT são apenas sugestão para simulação e precisam ser homologados.",
        },
    ]
    for row in policies:
        db.add(CalculationPolicy(eligible_group="PROMOTOR_VT", **row))
    db.flush()


def _name_variants(name: str):
    n = norm_text(name)
    stripped = norm_text(re.sub(r"\([^)]*\)", "", name or ""))
    return {x for x in [n, stripped] if x}


def _seed_occurrences(db):
    if db.query(Occurrence).count() > 0:
        return
    employees = db.query(Employee).all()
    by_name = {}
    for e in employees:
        for key in _name_variants(e.name):
            by_name.setdefault(key, []).append(e)

    for row in HISTORICAL_OCCURRENCES:
        matches = []
        for key in _name_variants(row.get("employee_name") or ""):
            matches.extend(by_name.get(key, []))
        # Deduplica objetos encontrados por variantes.
        unique = {e.id: e for e in matches}
        employee = next(iter(unique.values())) if len(unique) == 1 else None
        start_date = date.fromisoformat(row["start_date"]) if row.get("start_date") else None
        end_date = date.fromisoformat(row["end_date"]) if row.get("end_date") else start_date
        db.add(Occurrence(
            source_key=f"MIGRACAO:{row['legacy_id']}", employee_id=employee.id if employee else None,
            employee_name_text=row.get("employee_name"), kind=row.get("kind") or "OUTRO",
            start_date=start_date, end_date=end_date, impact_mode=row.get("impact_mode") or "INFO",
            quantity_original=row.get("quantity_original"), notes=row.get("notes"), source="MIGRACAO_EXCEL",
            source_sheet=row.get("source_sheet"), source_row=row.get("source_row"),
            status="ATIVA" if row.get("status") == "OK" else "REVISAR",
        ))
    db.flush()


def _ensure_sqlite_migrations():
    """Migra o banco persistente das versões anteriores sem apagar dados."""
    from sqlalchemy import text

    def columns(conn, table_name):
        return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table_name})"))}

    with engine.begin() as conn:
        employee_cols = columns(conn, "employees")
        if employee_cols:
            additions = {
                "in_scope": "BOOLEAN NOT NULL DEFAULT 1",
                "cpf": "VARCHAR(11)",
                "company_id": "INTEGER REFERENCES companies(id)",
                "operational_group": "VARCHAR(40) NOT NULL DEFAULT 'REGULAR'",
                "employment_status": "VARCHAR(30) NOT NULL DEFAULT 'ATIVO'",
                "exclusive_supplier": "BOOLEAN NOT NULL DEFAULT 0",
                "supplier_name": "VARCHAR(180)",
                "km_authorized": "BOOLEAN NOT NULL DEFAULT 0",
                "km_authorized_from": "DATE",
                "km_authorized_to": "DATE",
                "km_authorization_reason": "TEXT",
            }
            for name, ddl in additions.items():
                if name not in employee_cols:
                    conn.execute(text(f"ALTER TABLE employees ADD COLUMN {name} {ddl}"))

            # Mantém a regra legada de escopo ao migrar bancos anteriores.
            conn.execute(text(
                "UPDATE employees SET in_scope = 0 "
                "WHERE benefit_group = 'ADMIN_OUTROS' AND (in_scope IS NULL OR in_scope = 1)"
            ))

        user_cols = columns(conn, "user_accounts")
        if user_cols:
            if "department_id" not in user_cols:
                conn.execute(text(
                    "ALTER TABLE user_accounts ADD COLUMN department_id INTEGER REFERENCES departments(id)"
                ))
            if "position" not in user_cols:
                conn.execute(text("ALTER TABLE user_accounts ADD COLUMN position VARCHAR(40)"))


def seed():
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_migrations()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _seed_corporate_structure(db)

        if db.query(HomologationDecision).count() == 0:
            for row in PENDING_DECISIONS:
                db.add(HomologationDecision(**row, status="pending"))

        config = db.get(PaymentCycleConfig, 1)
        if not config:
            config = PaymentCycleConfig(id=1, first_half_end_day=QS_FIRST_HALF_END_DAY)
            db.add(config)
            db.flush()
        elif config.first_half_end_day != QS_FIRST_HALF_END_DAY:
            # Regra oficial confirmada para a V0.9.3. A alteração da configuração
            # não reescreve períodos fechados; ensure_periods respeita esse histórico.
            config.first_half_end_day = QS_FIRST_HALF_END_DAY
            db.flush()

        _seed_employees(db)
        _seed_tariffs(db)
        _seed_holidays(db)
        _seed_holiday_rules_2026(db)
        _seed_policies(db)
        _seed_occurrences(db)

        today = date.today()
        ensure_periods(db, today.year, today.month, config.first_half_end_day)
        db.commit()
    finally:
        db.close()
