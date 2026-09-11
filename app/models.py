from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


class HomologationDecision(Base):
    __tablename__ = "homologation_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_table: Mapped[str] = mapped_column(String(40), nullable=False)
    source_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str] = mapped_column(String(220), nullable=False)
    decision_needed: Mapped[str] = mapped_column(String(260), nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="ALTA")
    status: Mapped[str] = mapped_column(String(30), default="pending")

    rule_kind: Mapped[str | None] = mapped_column(String(60), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CostRule(Base):
    __tablename__ = "cost_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_decision_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    rule_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    location: Mapped[str] = mapped_column(String(220), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(240), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sheet: Mapped[str] = mapped_column(String(120), nullable=False)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    options: Mapped[list["CostRuleOption"]] = relationship(
        back_populates="rule", cascade="all, delete-orphan", order_by="CostRuleOption.sequence"
    )


class CostRuleOption(Base):
    __tablename__ = "cost_rule_options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("cost_rules.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    modal: Mapped[str | None] = mapped_column(String(120), nullable=True)
    origin: Mapped[str | None] = mapped_column(String(180), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(180), nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[str | None] = mapped_column(String(10), nullable=True)

    rule: Mapped[CostRule] = relationship(back_populates="options")


class PaymentCycleConfig(Base):
    __tablename__ = "payment_cycle_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    first_half_end_day: Mapped[int] = mapped_column(Integer, default=17, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PaymentPeriod(Base):
    __tablename__ = "payment_periods"
    __table_args__ = (UniqueConstraint("year", "month", "half", name="uq_payment_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    half: Mapped[int] = mapped_column(Integer, nullable=False)  # 1 ou 2
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="not_processed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    calculations: Mapped[list["PaymentCalculation"]] = relationship(
        back_populates="period", cascade="all, delete-orphan"
    )


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employees: Mapped[list["Employee"]] = relationship(back_populates="company")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users: Mapped[list["UserAccount"]] = relationship(back_populates="department")


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    source_system: Mapped[str] = mapped_column(String(40), default="Puzzle snapshot")
    name: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    cpf: Mapped[str | None] = mapped_column(String(11), nullable=True, index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    supervisor: Mapped[str | None] = mapped_column(String(220), nullable=True)
    work_city: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    work_state: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    profile: Mapped[str | None] = mapped_column(String(160), nullable=True)
    workload_key: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    workload_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mobility_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    regionals: Mapped[str | None] = mapped_column(Text, nullable=True)
    benefit_group: Mapped[str] = mapped_column(String(40), default="ADMIN_OUTROS", index=True)
    operational_group: Mapped[str] = mapped_column(String(40), default="REGULAR", nullable=False, index=True)
    employment_status: Mapped[str] = mapped_column(String(30), default="ATIVO", nullable=False, index=True)
    exclusive_supplier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    km_authorized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    km_authorized_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    km_authorized_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    km_authorization_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_status: Mapped[str] = mapped_column(String(30), default="OK")
    sync_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[Company | None] = relationship(back_populates="employees")
    occurrences: Mapped[list["Occurrence"]] = relationship(back_populates="employee")
    calculations: Mapped[list["PaymentCalculation"]] = relationship(back_populates="employee")


class BaseTariff(Base):
    __tablename__ = "base_tariffs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    city: Mapped[str] = mapped_column(String(180), nullable=False)
    city_norm: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    uf: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    modal: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_value: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="OK", index=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Holiday(Base):
    __tablename__ = "holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legacy_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(180), nullable=False)
    region_norm: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="OK")


class HolidayRule(Base):
    __tablename__ = "holiday_rules"
    __table_args__ = (UniqueConstraint("source_key", name="uq_holiday_rule_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(220), nullable=False, unique=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # NATIONAL/STATE/MUNICIPAL
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    city: Mapped[str | None] = mapped_column(String(180), nullable=True)
    city_norm: Mapped[str | None] = mapped_column(String(180), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(30), default="FERIADO", nullable=False)
    affects_calculation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(60), default="MANUAL_QS", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FinancialAdjustment(Base):
    __tablename__ = "financial_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # REEMBOLSO/DESCONTO/CREDITO/DEBITO
    benefit_type: Mapped[str] = mapped_column(String(30), default="VT", nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    reference_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_half: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_period_id: Mapped[int] = mapped_column(ForeignKey("payment_periods.id"), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(260), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDENTE", nullable=False, index=True)
    automatic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(40), default="MANUAL_QS", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    employee: Mapped[Employee] = relationship()
    payment_period: Mapped[PaymentPeriod] = relationship()


class CalculationPolicy(Base):
    __tablename__ = "calculation_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    workload_key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    eligible_group: Mapped[str] = mapped_column(String(40), default="PROMOTOR_VT")
    weekdays: Mapped[str] = mapped_column(String(40), default="0,1,2,3,4,5")
    vt_per_day: Mapped[float] = mapped_column(Float, default=2.0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Occurrence(Base):
    __tablename__ = "occurrences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    employee_name_text: Mapped[str | None] = mapped_column(String(220), nullable=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    impact_mode: Mapped[str] = mapped_column(String(30), default="INFO", nullable=False)
    quantity_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity_original: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="MANUAL")
    source_sheet: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="ATIVA")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    employee: Mapped[Employee | None] = relationship(back_populates="occurrences")


class PaymentCalculation(Base):
    __tablename__ = "payment_calculations"
    __table_args__ = (UniqueConstraint("period_id", "employee_id", name="uq_period_employee_calc"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("payment_periods.id"), nullable=False, index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("calculation_policies.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="review", nullable=False, index=True)
    scheduled_days: Mapped[int] = mapped_column(Integer, default=0)
    holiday_days: Mapped[int] = mapped_column(Integer, default=0)
    removed_days: Mapped[int] = mapped_column(Integer, default=0)
    eligible_days: Mapped[int] = mapped_column(Integer, default=0)
    unit_fare: Mapped[float | None] = mapped_column(Float, nullable=True)
    vt_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    vt_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    vt_total: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fare_source: Mapped[str | None] = mapped_column(String(220), nullable=True)
    primary_issue: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    period: Mapped[PaymentPeriod] = relationship(back_populates="calculations")
    employee: Mapped[Employee] = relationship(back_populates="calculations")
    policy: Mapped[CalculationPolicy | None] = relationship()
    lines: Mapped[list["CalculationLine"]] = relationship(
        back_populates="calculation", cascade="all, delete-orphan", order_by="CalculationLine.sequence"
    )


class CalculationLine(Base):
    __tablename__ = "calculation_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calculation_id: Mapped[int] = mapped_column(ForeignKey("payment_calculations.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    line_type: Mapped[str] = mapped_column(String(40), nullable=False)
    line_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    label: Mapped[str] = mapped_column(String(260), nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(220), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    calculation: Mapped[PaymentCalculation] = relationship(back_populates="lines")


class EmployeePolicyOverride(Base):
    __tablename__ = "employee_policy_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), nullable=False, index=True)
    weekdays: Mapped[str | None] = mapped_column(String(40), nullable=True)
    vt_per_day: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reason: Mapped[str] = mapped_column(String(260), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee: Mapped[Employee] = relationship()


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True, index=True)
    position: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(30), default="CONSULTA", nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    department: Mapped[Department | None] = relationship(back_populates="users")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user_accounts.id"), nullable=True, index=True)
    username: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PointOfSale(Base):
    __tablename__ = "pdvs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo_pdv: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    bandeira: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    nome_pdv: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    rede: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    regional: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    cnpj: Mapped[str | None] = mapped_column(String(40), nullable=True)
    endereco: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_cadastro: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PromoterRoute(Base):
    __tablename__ = "promoter_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    promoter_name: Mapped[str] = mapped_column(String(220), nullable=False, index=True)
    pdv_name: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    uf: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    frequency: Mapped[int] = mapped_column(Integer, default=1)
    monday: Mapped[str | None] = mapped_column(Text, nullable=True)
    tuesday: Mapped[str | None] = mapped_column(Text, nullable=True)
    wednesday: Mapped[str | None] = mapped_column(Text, nullable=True)
    thursday: Mapped[str | None] = mapped_column(Text, nullable=True)
    friday: Mapped[str | None] = mapped_column(Text, nullable=True)
    saturday: Mapped[str | None] = mapped_column(Text, nullable=True)
    sunday: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    employee: Mapped[Employee | None] = relationship()
