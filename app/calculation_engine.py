import re
import unicodedata
from datetime import date, datetime, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from .models import (
    Employee, PaymentPeriod, CalculationPolicy, BaseTariff, Holiday, HolidayRule,
    Occurrence, CostRule, PaymentCalculation, CalculationLine, EmployeePolicyOverride,
)


def norm_text(value) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()



def canonical_city(value) -> str:
    n = norm_text(value)
    n = re.sub(r"\([^)]*\)", "", n).strip()
    n = re.sub(r"\s*/\s*[A-Z]{2}$", "", n).strip()
    aliases = {
        "JUAZEIRO DA BAHIA": "JUAZEIRO",
        "SAO LUIZ": "SAO LUIS",
        "ARCO VERDE": "ARCOVERDE",
        "ARCO-VERDE": "ARCOVERDE",
        "CABO SANTO AGOSTINHO": "CABO DE SANTO AGOSTINHO",
        "ALAGOINHA": "ALAGOINHAS",
        "PALMEIRAS DOS INDIOS": "PALMEIRA DOS INDIOS",
    }
    return aliases.get(n, n)


def normalize_modal(value) -> str:
    n = norm_text(value)
    if "ONIBUS" in n:
        return "ONIBUS"
    if "CARRO" in n:
        return "CARRO"
    if "MOTO" in n:
        return "MOTO"
    return n


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except Exception:
        return None


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def weekdays_from_policy(policy: CalculationPolicy) -> set[int]:
    out = set()
    for token in (policy.weekdays or "").split(","):
        try:
            val = int(token.strip())
            if 0 <= val <= 6:
                out.add(val)
        except ValueError:
            continue
    return out


def _rule_matches_employee(rule: CostRule, employee: Employee) -> bool:
    city = canonical_city(employee.work_city)
    uf = norm_text(employee.work_state)
    hay = " | ".join([canonical_city(rule.location), canonical_city(rule.scope)])
    if not city:
        return False
    # A cidade deve aparecer como termo inteiro ou início de um escopo cidade/UF.
    pattern = rf"(^|[^A-Z0-9]){re.escape(city)}([^A-Z0-9]|$)"
    if not re.search(pattern, hay):
        return False
    if uf and "/" in hay:
        # UF reforça o match quando o escopo a contém; não bloqueia registros históricos sem UF.
        scope_uf = re.findall(r"/([A-Z]{2})(?:\b|$)", hay)
        if scope_uf and uf not in scope_uf:
            return False
    return True


def _choose_rule_option(rule: CostRule, employee: Employee, period: PaymentPeriod):
    candidates = []
    emp_name = norm_text(employee.name)
    emp_modal = normalize_modal(employee.mobility_mode)
    for opt in rule.options:
        if opt.amount is None:
            continue
        valid_from = parse_iso_date(opt.valid_from)
        if valid_from and valid_from > period.end_date:
            continue
        modal = normalize_modal(opt.modal)
        text = " ".join([norm_text(opt.label), norm_text(opt.condition), norm_text(opt.origin), norm_text(opt.destination)])
        score = 0
        if emp_name and emp_name in text:
            score += 20
        if modal:
            if modal == emp_modal:
                score += 8
            elif emp_modal:
                score -= 8
        if any(word in text for word in ["PADRAO", "GERAL", "MUNICIPAL", "TARIFA PADRAO"]):
            score += 3
        if "CONFIRMAR" in text or "DUVIDA" in text:
            score -= 4
        if valid_from:
            score += 1
        candidates.append((score, valid_from or date.min, opt))

    if not candidates:
        return None, "Regra homologada encontrada, mas sem valor aplicável para o período."
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if len(candidates) == 1:
        return candidates[0][2], None

    top = candidates[0]
    second = candidates[1]
    # Só seleciona automaticamente quando há vantagem clara ou opção individual explícita.
    if top[0] >= second[0] + 5 or (emp_name and emp_name in " ".join([
        norm_text(top[2].label), norm_text(top[2].condition)
    ])):
        return top[2], None

    # Se só uma opção casa com o modal do colaborador, ela é segura.
    modal_matches = [item for item in candidates if normalize_modal(item[2].modal) == emp_modal and emp_modal]
    if len(modal_matches) == 1:
        return modal_matches[0][2], None

    return None, "A regra homologada possui mais de uma condição possível e o QS não conseguiu escolher automaticamente."


def resolve_fare(db: Session, employee: Employee, period: PaymentPeriod):
    active_rules = db.query(CostRule).options(joinedload(CostRule.options)).filter(CostRule.active == True).all()
    matched_rules = [r for r in active_rules if _rule_matches_employee(r, employee)]

    tariff_rules = [r for r in matched_rules if "TARIFA" in norm_text(r.rule_kind)]
    other_vt_rules = [r for r in matched_rules if r not in tariff_rules and "VT" in norm_text(r.rule_kind)]

    if tariff_rules:
        if len(tariff_rules) > 1:
            # Prefere o escopo mais específico (cidade+UF) quando existe apenas um.
            exact = [r for r in tariff_rules if canonical_city(employee.work_city) in canonical_city(r.scope or r.location)]
            if len(exact) == 1:
                tariff_rules = exact
            else:
                return None, None, "Mais de uma regra de tarifa homologada coincide com este colaborador."
        rule = tariff_rules[0]
        option, issue = _choose_rule_option(rule, employee, period)
        if issue:
            return None, None, issue
        return option.amount, f"Regra homologada #{rule.id} · {rule.location} · {option.label or 'opção'}", None

    if other_vt_rules:
        return None, None, "Existe uma exceção de VT homologada para a localidade; o motor V0.9.2 ainda não sabe aplicá-la automaticamente."

    city_norm = canonical_city(employee.work_city)
    uf = norm_text(employee.work_state)
    rows = db.query(BaseTariff).filter(
        BaseTariff.city_norm == city_norm,
        BaseTariff.uf == uf,
        BaseTariff.status.in_(["OK", "OK_AUTO", "OK_MANUAL"]),
    ).all()
    rows = [r for r in rows if normalize_modal(r.modal) in {"", "ONIBUS"}]
    rows = [r for r in rows if r.amount is not None and (r.valid_from is None or r.valid_from <= period.end_date)]
    if not rows:
        return None, None, "Não há tarifa automática homologável para cidade/UF no catálogo base."

    rows.sort(key=lambda r: r.valid_from or date.min, reverse=True)
    best_date = rows[0].valid_from or date.min
    top = [r for r in rows if (r.valid_from or date.min) == best_date]
    amounts = {round(float(r.amount), 4) for r in top if r.amount is not None}
    if len(amounts) != 1:
        return None, None, "Há mais de uma tarifa base vigente para a mesma localidade."
    row = top[0]
    source = f"Tarifa base · {row.city}/{row.uf} · {row.source_sheet or 'migração'} linha {row.source_row or '—'}"
    return row.amount, source, None


def _line(calc: PaymentCalculation, sequence: int, line_type: str, label: str,
          line_date=None, quantity=None, unit_amount=None, amount=None, source=None, detail=None):
    calc.lines.append(CalculationLine(
        sequence=sequence, line_type=line_type, line_date=line_date, label=label,
        quantity=quantity, unit_amount=unit_amount, amount=amount, source=source, detail=detail,
    ))


def calculate_employee(db: Session, period: PaymentPeriod, employee: Employee) -> PaymentCalculation:
    calc = PaymentCalculation(period_id=period.id, employee_id=employee.id, processed_at=datetime.utcnow())
    seq = 1

    if not employee.active or not getattr(employee, "in_scope", True):
        calc.status = "excluded"
        calc.primary_issue = "Colaborador inativo ou fora do escopo operacional."
        _line(calc, seq, "ESCOPO", "Colaborador inativo/fora do escopo — não participa do processamento")
        return calc

    if employee.benefit_group != "PROMOTOR_VT":
        calc.status = "excluded"
        if employee.benefit_group == "LIDERANCA_KM":
            calc.primary_issue = "Perfil direcionado ao futuro motor de KM; não entra no motor VT V0.9.2."
        else:
            calc.primary_issue = "Perfil administrativo/outro fora do motor VT V0.9.2."
        _line(calc, seq, "ESCOPO", calc.primary_issue)
        return calc

    if employee.sync_status != "OK":
        calc.status = "review"
        calc.primary_issue = employee.sync_note or "Cadastro do colaborador requer revisão."
        _line(calc, seq, "PENDENCIA", calc.primary_issue)
        return calc

    if normalize_modal(employee.mobility_mode) != "ONIBUS":
        calc.status = "review"
        calc.primary_issue = f"Modal {employee.mobility_mode or 'não informado'} ainda não é calculado no motor VT V0.9.2."
        _line(calc, seq, "PENDENCIA", calc.primary_issue)
        return calc

    policy = db.query(CalculationPolicy).filter(
        CalculationPolicy.workload_key == employee.workload_key,
        CalculationPolicy.eligible_group == employee.benefit_group,
    ).first()
    if not policy:
        calc.status = "review"
        calc.primary_issue = f"Não existe política de cálculo para carga {employee.workload_label or employee.workload_key or 'não informada'}."
        _line(calc, seq, "PENDENCIA", calc.primary_issue)
        return calc

    calc.policy_id = policy.id
    override = db.query(EmployeePolicyOverride).filter(
        EmployeePolicyOverride.employee_id == employee.id,
        EmployeePolicyOverride.active == True,
        or_(EmployeePolicyOverride.valid_from == None, EmployeePolicyOverride.valid_from <= period.end_date),
        or_(EmployeePolicyOverride.valid_to == None, EmployeePolicyOverride.valid_to >= period.start_date),
    ).order_by(EmployeePolicyOverride.valid_from.desc(), EmployeePolicyOverride.id.desc()).first()

    effective_vt_per_day = override.vt_per_day if override and override.vt_per_day is not None else policy.vt_per_day
    effective_weekdays = override.weekdays if override and override.weekdays else policy.weekdays
    calc.vt_per_day = effective_vt_per_day

    class _EffectivePolicy:
        weekdays = effective_weekdays
    weekdays = weekdays_from_policy(_EffectivePolicy())
    if override:
        _line(calc, seq, "POLITICA_INDIVIDUAL", "Ajuste individual aplicado ao colaborador",
              quantity=effective_vt_per_day, source="Perfil do colaborador", detail=override.reason + (f" · {override.notes}" if override.notes else "")); seq += 1
    if not weekdays:
        calc.status = "review"
        calc.primary_issue = "A política de cálculo não possui dias da semana configurados."
        _line(calc, seq, "PENDENCIA", calc.primary_issue)
        return calc

    scheduled = [d for d in daterange(period.start_date, period.end_date) if d.weekday() in weekdays]
    calc.scheduled_days = len(scheduled)
    _line(calc, seq, "AGENDA", f"Dias previstos pela política {policy.label}", quantity=len(scheduled), detail=policy.notes); seq += 1

    # Calendário operacional: nacional + estadual/distrital + municipal aplicável ao colaborador.
    uf = norm_text(employee.work_state)
    city_norm = canonical_city(employee.work_city)
    candidates = db.query(HolidayRule).filter(
        HolidayRule.date >= period.start_date,
        HolidayRule.date <= period.end_date,
        HolidayRule.active == True,
        HolidayRule.affects_calculation == True,
    ).all()
    holiday_rows = []
    for h in candidates:
        if h.scope_type == "NATIONAL":
            holiday_rows.append(h)
        elif h.scope_type == "STATE" and h.uf == uf:
            holiday_rows.append(h)
        elif h.scope_type == "MUNICIPAL" and h.uf == uf and h.city_norm == city_norm:
            holiday_rows.append(h)
    holiday_dates = {h.date for h in holiday_rows if h.date in scheduled}
    calc.holiday_days = len(holiday_dates)
    for h in sorted(holiday_rows, key=lambda x: (x.date, x.scope_type)):
        if h.date in holiday_dates:
            scope_label = {"NATIONAL": "nacional", "STATE": "estadual/distrital", "MUNICIPAL": "municipal"}.get(h.scope_type, h.scope_type)
            _line(calc, seq, "FERIADO", f"Feriado {scope_label}: {h.name}", line_date=h.date, quantity=-1,
                  source=h.source_ref or h.source, detail=h.notes); seq += 1

    occs = db.query(Occurrence).filter(
        Occurrence.employee_id == employee.id,
        Occurrence.status == "ATIVA",
    ).all()
    removed_dates = set()
    extra_vt = 0.0
    removed_vt = 0.0
    blocking = []
    for occ in occs:
        start = occ.start_date or period.start_date
        end = occ.end_date or start
        if end < period.start_date or start > period.end_date:
            continue
        overlap_start = max(start, period.start_date)
        overlap_end = min(end, period.end_date)
        if occ.impact_mode == "REMOVE_DAY":
            affected = {d for d in daterange(overlap_start, overlap_end) if d in scheduled and d not in holiday_dates}
            removed_dates.update(affected)
            _line(calc, seq, "OCORRENCIA", f"{occ.kind}: retirada de dias elegíveis", line_date=overlap_start,
                  quantity=-len(affected), source=occ.source, detail=occ.notes); seq += 1
        elif occ.impact_mode == "ADD_VT":
            delta = max(0.0, float(occ.quantity_delta or 0))
            extra_vt += delta
            _line(calc, seq, "OCORRENCIA", f"{occ.kind}: passagens adicionais", line_date=overlap_start,
                  quantity=delta, source=occ.source, detail=occ.notes); seq += 1
        elif occ.impact_mode == "REMOVE_VT":
            delta = max(0.0, float(occ.quantity_delta or 0))
            removed_vt += delta
            _line(calc, seq, "OCORRENCIA", f"{occ.kind}: trecho sem passagem / lojas próximas", line_date=overlap_start,
                  quantity=-delta, source=occ.source, detail=occ.notes); seq += 1
        elif occ.impact_mode == "BLOCK":
            blocking.append(occ)
            _line(calc, seq, "PENDENCIA", f"{occ.kind}: ocorrência exige revisão", line_date=overlap_start,
                  source=occ.source, detail=occ.notes); seq += 1
        else:
            _line(calc, seq, "INFORMATIVO", f"{occ.kind}: ocorrência informativa", line_date=overlap_start,
                  source=occ.source, detail=occ.notes); seq += 1

    calc.removed_days = len(removed_dates)
    eligible_dates = [d for d in scheduled if d not in holiday_dates and d not in removed_dates]
    calc.eligible_days = len(eligible_dates)
    _line(calc, seq, "ELEGIBILIDADE", "Dias elegíveis após feriados e ocorrências", quantity=calc.eligible_days); seq += 1

    if blocking:
        calc.status = "review"
        calc.primary_issue = "Há ocorrência marcada para bloquear o cálculo."
        return calc

    fare, fare_source, fare_issue = resolve_fare(db, employee, period)
    if fare_issue:
        calc.status = "review"
        calc.primary_issue = fare_issue
        _line(calc, seq, "PENDENCIA", fare_issue); seq += 1
        return calc

    calc.unit_fare = fare
    calc.fare_source = fare_source
    _line(calc, seq, "TARIFA", "Tarifa unitária aplicada", unit_amount=fare, source=fare_source); seq += 1

    base_qty = calc.eligible_days * float(effective_vt_per_day or 0)
    total_qty = max(0.0, base_qty + extra_vt - removed_vt)
    calc.vt_quantity = total_qty
    detail = f"{calc.eligible_days} dias × {float(effective_vt_per_day or 0):g} VT/dia"
    if extra_vt:
        detail += f" + {extra_vt:g} passagem(ns) adicional(is)"
    if removed_vt:
        detail += f" - {removed_vt:g} passagem(ns) por trecho sem VT/lojas próximas"
    _line(calc, seq, "QUANTIDADE", "VT previsto na quinzena", quantity=total_qty, detail=detail); seq += 1
    if extra_vt:
        _line(calc, seq, "ADICIONAL", "Impacto das passagens adicionais no Total Conhecido", quantity=extra_vt,
              unit_amount=fare, amount=round(extra_vt * float(fare), 2), source="Ocorrências"); seq += 1
    if removed_vt:
        _line(calc, seq, "LOJAS_PROXIMAS", "Trechos sem pagamento de passagem", quantity=-removed_vt,
              unit_amount=fare, amount=round(-removed_vt * float(fare), 2), source="Regra de lojas próximas"); seq += 1

    calc.vt_total = round(total_qty * float(fare), 2)
    calc.total_amount = calc.vt_total
    _line(calc, seq, "TOTAL", "VT calculado", quantity=total_qty, unit_amount=fare, amount=calc.vt_total,
          source=fare_source); seq += 1

    if policy.approved:
        calc.status = "calculated"
    else:
        calc.status = "simulation"
        calc.primary_issue = "Cálculo válido apenas como simulação: política de dias/quantidade ainda não foi homologada."
        _line(calc, seq, "AVISO", calc.primary_issue, source="Política em rascunho")
    return calc


def process_period(db: Session, period: PaymentPeriod):
    if period.status == "closed":
        raise ValueError("Quinzena fechada não pode ser recalculada.")

    existing = db.query(PaymentCalculation).filter(PaymentCalculation.period_id == period.id).all()
    for row in existing:
        db.delete(row)
    db.flush()

    employees = db.query(Employee).filter(Employee.active == True, Employee.in_scope == True).order_by(Employee.name).all()
    counts = {"calculated": 0, "simulation": 0, "review": 0, "excluded": 0}
    total = 0.0
    for employee in employees:
        calc = calculate_employee(db, period, employee)
        db.add(calc)
        counts[calc.status] = counts.get(calc.status, 0) + 1
        if calc.total_amount:
            total += calc.total_amount
    db.flush()

    if counts.get("simulation", 0) > 0:
        period.status = "simulated"
    elif counts.get("review", 0) > 0:
        period.status = "processed_with_issues"
    else:
        period.status = "processed"
    db.commit()
    counts["total_amount"] = round(total, 2)
    counts["employees"] = len(employees)
    return counts
