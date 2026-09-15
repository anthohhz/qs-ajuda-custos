from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable


CENTRO_OESTE_UFS = {"DF", "GO", "MT", "MS"}
VACATION_KINDS = {"FÉRIAS", "FERIAS"}
REVIEW_KINDS = {"FALTA", "ATESTADO", "DECLARAÇÃO", "DECLARACAO", "LICENÇA", "LICENCA", "AFASTAMENTO"}


@dataclass(frozen=True)
class BenefitPart:
    code: str
    label: str
    amount: float | None
    status: str
    reason: str


@dataclass(frozen=True)
class DailyBenefitPreview:
    day: date
    transport: BenefitPart
    meal: BenefitPart
    known_total: float
    complete: bool
    status: str
    warnings: tuple[str, ...]


def _text(value) -> str:
    return str(value or "").strip().upper()


def _workload_group(employee) -> str | None:
    """Traduz as cargas já conhecidas da QS para a regra diária de alimentação.

    Não convertemos genericamente '44h semanais' em uma jornada diária arbitrária.
    Enquanto a V0.9.3 não tiver escala diária estruturada, usamos somente os grupos
    explicitamente conhecidos na operação atual.
    """
    key = _text(getattr(employee, "workload_key", None))
    label = _text(getattr(employee, "workload_label", None))
    profile = _text(getattr(employee, "profile", None))
    combined = " ".join((key, label, profile))

    if "6H_MEI" in combined or " 6H" in f" {combined}":
        return "LANCHE"
    if "22H" in combined:
        return "LANCHE"
    if "40H" in combined or "44H" in combined:
        return "VR"

    # Perfis que já chegam com jornada diária explícita.
    for marker in ("7H", "8H", "9H", "10H", "11H", "12H"):
        if marker in combined:
            return "VR"
    for marker in ("1H", "2H", "3H", "4H", "5H", "6H"):
        if marker in combined:
            return "LANCHE"
    return None


def _meal_amount(employee) -> tuple[str | None, float | None, str]:
    group = _workload_group(employee)
    if group == "LANCHE":
        return "LANCHE", 10.0, "Jornada de até 6h: lanche fixo QS de R$ 10,00."
    if group == "VR":
        uf = _text(getattr(employee, "work_state", None))
        if uf == "SE":
            return "VR", 27.0, "VR Sergipe: R$ 27,00 por dia."
        if uf in CENTRO_OESTE_UFS:
            return "VR", 25.0, "VR Centro-Oeste: R$ 25,00 por dia."
        return "VR", 22.0, "VR demais regiões: R$ 22,00 por dia."
    return None, None, "Carga diária ainda não estruturada para definir VR ou lanche automaticamente."


def _km_authorized_on(employee, day: date) -> bool:
    if not bool(getattr(employee, "km_authorized", False)):
        return False
    start = getattr(employee, "km_authorized_from", None)
    end = getattr(employee, "km_authorized_to", None)
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _occurrence_kinds(occurrences: Iterable) -> set[str]:
    return {_text(getattr(row, "kind", None)) for row in occurrences}


def build_daily_benefit_preview(
    *,
    employee,
    day: date,
    route_stops: list,
    holidays: list,
    occurrences: list,
) -> DailyBenefitPreview:
    """Gera uma prévia diária segura do motor V0.9.3.

    Nesta etapa, alimentação já pode ser calculada pelas regras confirmadas. VT/KM
    ainda não recebe valor monetário automático porque distância entre PDVs, tarifa
    por trecho e evidência de combustível serão conectadas nos próximos módulos.
    """
    kinds = _occurrence_kinds(occurrences)
    warnings: list[str] = []

    vacation = bool(kinds & VACATION_KINDS)
    calculation_holiday = any(bool(getattr(row, "affects_calculation", False)) for row in holidays)

    if vacation:
        reason = "Férias retiram VT/KM e o benefício de alimentação no período."
        zero = BenefitPart("BLOQUEADO", "Sem benefício", 0.0, "BLOQUEADO", reason)
        return DailyBenefitPreview(day, zero, zero, 0.0, True, "BLOQUEADO", ())

    if calculation_holiday:
        reason = "Feriado aplicável marcado para impactar o cálculo; não há ajuda diária automática."
        zero = BenefitPart("FERIADO", "Sem benefício", 0.0, "BLOQUEADO", reason)
        return DailyBenefitPreview(day, zero, zero, 0.0, True, "BLOQUEADO", ())

    review_events = sorted(kinds & REVIEW_KINDS)
    if review_events:
        warnings.append("Ocorrência em conferência: " + ", ".join(review_events) + ".")

    if not route_stops:
        transport = BenefitPart(
            "SEM_ROTEIRO",
            "Transporte pendente",
            None,
            "PENDENTE",
            "Nenhum PDV programado para o dia; o sistema não presume quantidade de VT.",
        )
        meal = BenefitPart(
            "SEM_ROTEIRO",
            "Alimentação pendente",
            None,
            "PENDENTE",
            "Sem roteiro do dia, a alimentação não é lançada automaticamente nesta etapa.",
        )
        return DailyBenefitPreview(day, transport, meal, 0.0, False, "PENDENTE", tuple(warnings))

    if _km_authorized_on(employee, day):
        transport = BenefitPart(
            "KM",
            "KM autorizado",
            None,
            "PENDENTE_KM",
            "Colaborador autorizado para KM. Valor depende de distância, política do veículo e combustível aprovado.",
        )
    else:
        transport = BenefitPart(
            "VT",
            "VT por roteiro",
            None,
            "PENDENTE_DISTANCIA",
            "Roteiro identificado. Valor será calculado após tarifa por trecho e regra automática de até 750 m.",
        )

    meal_code, meal_amount, meal_reason = _meal_amount(employee)
    if meal_code:
        meal = BenefitPart(meal_code, "VR" if meal_code == "VR" else "Lanche", meal_amount, "CALCULADO", meal_reason)
    else:
        meal = BenefitPart("ALIMENTACAO", "Alimentação pendente", None, "PENDENTE", meal_reason)

    known_total = float(meal.amount or 0.0) + float(transport.amount or 0.0)
    complete = meal.amount is not None and transport.amount is not None
    status = "CALCULADO" if complete and not warnings else ("REVISAR" if warnings else "PARCIAL")
    return DailyBenefitPreview(day, transport, meal, known_total, complete, status, tuple(warnings))
