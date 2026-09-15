from datetime import date
from types import SimpleNamespace
import unittest

from app.daily_engine_v093 import build_daily_benefit_preview


def employee(**kwargs):
    base = {
        "workload_key": "40H",
        "workload_label": "40h",
        "profile": "PROMOTOR",
        "work_state": "CE",
        "km_authorized": False,
        "km_authorized_from": None,
        "km_authorized_to": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def occurrence(kind):
    return SimpleNamespace(kind=kind)


def holiday(affects=True):
    return SimpleNamespace(affects_calculation=affects)


class DailyEngineTests(unittest.TestCase):
    def test_6h_recebe_lanche_10(self):
        result = build_daily_benefit_preview(
            employee=employee(workload_key="6H_MEI", workload_label="6h"),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[],
            occurrences=[],
        )
        self.assertEqual(result.meal.code, "LANCHE")
        self.assertEqual(result.meal.amount, 10.0)

    def test_vr_sergipe_27(self):
        result = build_daily_benefit_preview(
            employee=employee(workload_key="40H", work_state="SE"),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[],
            occurrences=[],
        )
        self.assertEqual(result.meal.code, "VR")
        self.assertEqual(result.meal.amount, 27.0)

    def test_vr_centro_oeste_25(self):
        result = build_daily_benefit_preview(
            employee=employee(workload_key="44H", work_state="MS"),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[],
            occurrences=[],
        )
        self.assertEqual(result.meal.amount, 25.0)

    def test_vr_demais_regioes_22(self):
        result = build_daily_benefit_preview(
            employee=employee(workload_key="40H", work_state="CE"),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[],
            occurrences=[],
        )
        self.assertEqual(result.meal.amount, 22.0)

    def test_ferias_zeram_transporte_e_alimentacao(self):
        result = build_daily_benefit_preview(
            employee=employee(),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[],
            occurrences=[occurrence("FÉRIAS")],
        )
        self.assertEqual(result.transport.amount, 0.0)
        self.assertEqual(result.meal.amount, 0.0)
        self.assertEqual(result.status, "BLOQUEADO")

    def test_km_autorizado_nao_inventa_valor(self):
        result = build_daily_benefit_preview(
            employee=employee(km_authorized=True),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV A"}, {"name": "PDV B"}],
            holidays=[],
            occurrences=[],
        )
        self.assertEqual(result.transport.code, "KM")
        self.assertIsNone(result.transport.amount)
        self.assertEqual(result.meal.amount, 22.0)
        self.assertFalse(result.complete)

    def test_sem_rota_nao_presume_vt_ou_alimentacao(self):
        result = build_daily_benefit_preview(
            employee=employee(),
            day=date(2026, 9, 10),
            route_stops=[],
            holidays=[],
            occurrences=[],
        )
        self.assertIsNone(result.transport.amount)
        self.assertIsNone(result.meal.amount)
        self.assertEqual(result.status, "PENDENTE")

    def test_feriado_que_impacta_calculo_zera_dia(self):
        result = build_daily_benefit_preview(
            employee=employee(),
            day=date(2026, 9, 10),
            route_stops=[{"name": "PDV"}],
            holidays=[holiday(True)],
            occurrences=[],
        )
        self.assertEqual(result.known_total, 0.0)
        self.assertEqual(result.status, "BLOQUEADO")


if __name__ == "__main__":
    unittest.main()
