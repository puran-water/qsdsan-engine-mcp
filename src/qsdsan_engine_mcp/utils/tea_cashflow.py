'Ledger-driven full cash-flow TEA — the bridge from ``engineering_utils.economics``.'

from __future__ import annotations

import itertools
import re
from typing import Any, Dict, List, Optional

import qsdsan as qs
from biosteam import TEA as BSTTEA

from puran_mcp_support.engineering.economics.ledger import CostLedger

__all__ = ["run_cashflow_tea"]

# Depreciation vocabulary shown in errors: the fixed MACRS keys biosteam ships
# plus the parametric generators (see biosteam.TEA.depreciation_schedules).
VALID_DEPRECIATION = (
    "MACRS3", "MACRS5", "MACRS7", "MACRS10", "MACRS15", "MACRS20",
    "SL", "SL<n>", "DDB<n>", "SYD<n>",
)

_REQUIRED_BASIS_KEYS = ("discount_rate", "plant_life_years")
_ALLOWED_BASIS_KEYS = frozenset({
    "discount_rate",
    "plant_life_years",
    "income_tax",
    "depreciation",
    "start_year",
    "annual_revenue_usd",
    "startup",
    "finance",
    "construction_schedule",
    "WC_over_FCI",
})
_ALLOWED_STARTUP_KEYS = frozenset({"months", "FOC_frac", "VOC_frac", "sales_frac"})
_REQUIRED_FINANCE_KEYS = ("loan_fraction", "interest", "years")

_system_counter = itertools.count()


class LedgerCashflowTEA(qs.TEA):
    """qsdsan.TEA whose capital/operating hooks read a CostLedger, not units."""

    __slots__ = ("_ledger_installed_usd", "_ledger_annual_opex_usd", "_annual_revenue_usd")

    def __init__(
        self,
        system,
        installed_usd: float,
        annual_opex_usd: float,
        annual_revenue_usd: float,
        **tea_kwargs,
    ):
        # Slots must exist before super().__init__: qsdsan's lang_factor setter
        # walks CAPEX -> TCI -> FCI -> _DPI during construction.
        self._ledger_installed_usd = float(installed_usd)
        self._ledger_annual_opex_usd = float(annual_opex_usd)
        self._annual_revenue_usd = float(annual_revenue_usd)
        super().__init__(
            system=system,
            simulate_system=False,
            CAPEX=installed_usd,
            **tea_kwargs,
        )

    # biosteam abstract capital hooks, ledger-driven. DPI == TDC == FCI is the
    # qsdsan pass-through convention: the ledger's installed total is taken as
    # already carrying whatever factors the caller wanted (see assumptions).
    def _DPI(self, installed_equipment_cost):
        return self._ledger_installed_usd

    def _TDC(self, DPI):
        return DPI

    def _FCI(self, TDC):
        return TDC

    def _FOC(self, FCI):
        # ALL ledger OPEX rides the fixed-cost schedule; the empty system
        # contributes zero VOC (no feeds, products, or utilities).
        return self._ledger_annual_opex_usd

    @property
    def sales(self):
        return self._annual_revenue_usd


def _fail(message: str) -> ValueError:
    return ValueError(message)


def _require_number(basis: Dict[str, Any], key: str, default=None) -> Optional[float]:
    value = basis.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"basis.{key} must be a number, got {type(value).__name__}: {value!r}")
    return float(value)


def _validate_depreciation(name: Any, plant_life_years: int) -> str:
    if not isinstance(name, str):
        raise _fail(
            f"basis.depreciation must be a string, got {type(name).__name__}; "
            f"valid schedules: {', '.join(VALID_DEPRECIATION)}"
        )
    try:
        key = BSTTEA._depreciation_key_from_name(name)
    except ValueError as exc:
        raise _fail(
            f"invalid depreciation schedule {name!r}; valid schedules: "
            f"{', '.join(VALID_DEPRECIATION)} (e.g. 'MACRS7', 'SL', 'SL10', 'DDB15')"
        ) from exc
    schedule_years = key[1]
    if schedule_years is not None:
        n_years = BSTTEA._depreciation_array_from_key(key).size
        if n_years > plant_life_years:
            raise _fail(
                f"depreciation schedule {name!r} spans {n_years} years but "
                f"plant_life_years={plant_life_years}; pick a schedule that fits "
                f"the plant life (e.g. 'SL' defaults to the full life) or "
                f"increase plant_life_years"
            )
    return name


def _resolve_basis(basis: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(basis, dict):
        raise _fail(f"basis must be a dict, got {type(basis).__name__}")
    unknown = set(basis) - _ALLOWED_BASIS_KEYS
    if unknown:
        raise _fail(
            f"unknown basis key(s) {sorted(unknown)}; allowed keys: "
            f"{sorted(_ALLOWED_BASIS_KEYS)}"
        )
    missing = [k for k in _REQUIRED_BASIS_KEYS if k not in basis]
    if missing:
        raise _fail(
            f"basis is missing required key(s) {missing}; minimum basis is "
            "{'discount_rate': <fraction>, 'plant_life_years': <int>}"
        )

    discount_rate = _require_number(basis, "discount_rate")
    if not 0.0 < discount_rate < 1.0:
        raise _fail(
            f"basis.discount_rate must be a fraction in (0, 1), got {discount_rate}"
        )

    life_raw = basis["plant_life_years"]
    if isinstance(life_raw, bool) or not isinstance(life_raw, int):
        raise _fail(
            f"basis.plant_life_years must be an integer year count, got {life_raw!r}"
        )
    if life_raw < 1:
        raise _fail(f"basis.plant_life_years must be >= 1, got {life_raw}")

    income_tax = _require_number(basis, "income_tax", 0.0)
    if not 0.0 <= income_tax < 1.0:
        raise _fail(f"basis.income_tax must be a fraction in [0, 1), got {income_tax}")

    depreciation = _validate_depreciation(basis.get("depreciation", "SL"), life_raw)

    start_year = basis.get("start_year", 1)
    if isinstance(start_year, bool) or not isinstance(start_year, int):
        raise _fail(f"basis.start_year must be an integer, got {start_year!r}")

    annual_revenue = _require_number(basis, "annual_revenue_usd", 0.0)
    if annual_revenue < 0.0:
        raise _fail(f"basis.annual_revenue_usd must be >= 0, got {annual_revenue}")

    construction_schedule = basis.get("construction_schedule", [1.0])
    if (
        not isinstance(construction_schedule, (list, tuple))
        or not construction_schedule
        or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in construction_schedule)
    ):
        raise _fail(
            "basis.construction_schedule must be a non-empty list of yearly "
            f"fractions summing to 1 (e.g. [1.0] or [0.5, 0.5]), got {construction_schedule!r}"
        )
    if abs(sum(construction_schedule) - 1.0) > 1e-9:
        raise _fail(
            f"basis.construction_schedule must sum to 1, got {sum(construction_schedule)}"
        )

    wc_over_fci = _require_number(basis, "WC_over_FCI", 0.0)
    if not 0.0 <= wc_over_fci < 1.0:
        raise _fail(f"basis.WC_over_FCI must be a fraction in [0, 1), got {wc_over_fci}")

    startup_raw = basis.get("startup") or {}
    if not isinstance(startup_raw, dict):
        raise _fail(
            "basis.startup must be a dict "
            "{'months': <=12, 'FOC_frac': f, 'VOC_frac': f, 'sales_frac': f}, "
            f"got {startup_raw!r}"
        )
    unknown_startup = set(startup_raw) - _ALLOWED_STARTUP_KEYS
    if unknown_startup:
        raise _fail(
            f"unknown basis.startup key(s) {sorted(unknown_startup)}; allowed: "
            f"{sorted(_ALLOWED_STARTUP_KEYS)}"
        )
    startup_months = _require_number(startup_raw, "months", 0.0)
    if not 0.0 <= startup_months <= 12.0:
        raise _fail(
            f"basis.startup.months must be in [0, 12] (biosteam limits startup to "
            f"the first operating year), got {startup_months}"
        )
    startup = {
        "months": startup_months,
        "FOC_frac": _require_number(startup_raw, "FOC_frac", 1.0),
        "VOC_frac": _require_number(startup_raw, "VOC_frac", 1.0),
        "sales_frac": _require_number(startup_raw, "sales_frac", 1.0),
    }

    finance_raw = basis.get("finance") or {}
    if not isinstance(finance_raw, dict):
        raise _fail(
            "basis.finance must be a dict "
            "{'loan_fraction': f, 'interest': f, 'years': n}, "
            f"got {finance_raw!r}"
        )
    finance = {"loan_fraction": 0.0, "interest": 0.0, "years": 0}
    if finance_raw:
        missing_fin = [k for k in _REQUIRED_FINANCE_KEYS if k not in finance_raw]
        unknown_fin = set(finance_raw) - set(_REQUIRED_FINANCE_KEYS)
        if missing_fin or unknown_fin:
            raise _fail(
                f"basis.finance requires exactly {list(_REQUIRED_FINANCE_KEYS)}; "
                f"missing {missing_fin or 'none'}, unknown {sorted(unknown_fin) or 'none'}"
            )
        loan_fraction = _require_number(finance_raw, "loan_fraction")
        interest = _require_number(finance_raw, "interest")
        years = finance_raw["years"]
        if not 0.0 <= loan_fraction <= 1.0:
            raise _fail(f"basis.finance.loan_fraction must be in [0, 1], got {loan_fraction}")
        if interest <= 0.0:
            raise _fail(f"basis.finance.interest must be > 0, got {interest}")
        if isinstance(years, bool) or not isinstance(years, int) or years < 1:
            raise _fail(f"basis.finance.years must be an integer >= 1, got {years!r}")
        if years > life_raw:
            raise _fail(
                f"basis.finance.years={years} exceeds plant_life_years={life_raw}; "
                "shorten the loan tenor or extend the plant life"
            )
        finance = {"loan_fraction": loan_fraction, "interest": interest, "years": years}

    return {
        "discount_rate": discount_rate,
        "plant_life_years": life_raw,
        "income_tax": income_tax,
        "depreciation": depreciation,
        "start_year": start_year,
        "annual_revenue_usd": annual_revenue,
        "construction_schedule": [float(x) for x in construction_schedule],
        "WC_over_FCI": wc_over_fci,
        "startup": startup,
        "finance": finance,
    }


def _validate_ledger(ledger: Dict[str, Any]) -> CostLedger:
    if not isinstance(ledger, dict):
        raise _fail(f"ledger must be a dict (CostLedger model dump), got {type(ledger).__name__}")
    # Accept the plant-tea wrapper shape {"ledger": {...}, "tea": {...}} too, so
    # the same input file can feed both CLIs.
    payload = ledger["ledger"] if "ledger" in ledger else ledger
    try:
        model = CostLedger.model_validate(payload)
    except Exception as exc:
        raise _fail(
            f"ledger failed engineering_utils CostLedger validation: {exc}"
        ) from exc
    if not model.capex_lines and not model.opex_lines:
        raise _fail("ledger has no capex_lines and no opex_lines — nothing to evaluate")
    return model


def _column_key(column: str) -> str:
    """'Fixed capital investment [MM$]' -> 'fixed_capital_investment_usd'."""
    is_money = column.endswith(" [MM$]")
    stem = column.removesuffix(" [MM$]")
    key = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"{key}_usd" if is_money else key


def _cashflow_rows(tea: LedgerCashflowTEA) -> List[Dict[str, float]]:
    frame = tea.get_cashflow_table()  # values in MM$ except 'Discount factor'
    rows: List[Dict[str, float]] = []
    for year, record in zip(frame.index, frame.to_dict(orient="records")):
        row: Dict[str, float] = {"year": int(year)}
        for column, value in record.items():
            key = _column_key(column)
            row[key] = float(value) * (1e6 if column.endswith(" [MM$]") else 1.0)
        rows.append(row)
    return rows


def run_cashflow_tea(ledger: Dict[str, Any], basis: Dict[str, Any]) -> Dict[str, Any]:
    """Full cash-flow TEA of a CostLedger dict; deterministic, raises on bad input.

    Returns ``{npv_usd, irr_solved, payback_years, cashflow_table, capital,
    operating, basis, assumptions}`` — money in USD, cashflow_table a
    year-indexed list of dicts straight from biosteam's cash-flow engine.
    """
    model = _validate_ledger(ledger)
    resolved = _resolve_basis(basis)

    installed = model.installed_total_usd
    annual_opex = model.annual_opex_total_usd
    revenue = resolved["annual_revenue_usd"]
    startup = resolved["startup"]
    finance = resolved["finance"]

    system = qs.System(ID=f"ledger_cashflow_tea_{next(_system_counter)}", path=())
    tea = LedgerCashflowTEA(
        system,
        installed_usd=installed,
        annual_opex_usd=annual_opex,
        annual_revenue_usd=revenue,
        discount_rate=resolved["discount_rate"],
        income_tax=resolved["income_tax"],
        start_year=resolved["start_year"],
        lifetime=resolved["plant_life_years"],
        uptime_ratio=1.0,  # ledger OPEX lines already carry availability in their basis
        depreciation=resolved["depreciation"],
        construction_schedule=tuple(resolved["construction_schedule"]),
        # Always pass all eight biosteam tea_kwargs — see module docstring.
        startup_months=startup["months"],
        startup_FOCfrac=startup["FOC_frac"],
        startup_VOCfrac=startup["VOC_frac"],
        startup_salesfrac=startup["sales_frac"],
        WC_over_FCI=resolved["WC_over_FCI"],
        finance_interest=finance["interest"],
        finance_years=finance["years"],
        finance_fraction=finance["loan_fraction"],
    )

    assumptions = [
        "Cash-flow engine: qsdsan.TEA (biosteam) over a ledger-driven empty system; "
        "simulate_system=False.",
        f"CAPEX basis: sum of per-line installed costs = {installed:,.2f} USD; "
        "DPI = TDC = FCI (qsdsan pass-through). No factor rollup applied — run the "
        "plant-tea capex rollup upstream if engineering/contingency factors belong "
        "in the depreciable base.",
        f"OPEX: all ledger categories ({', '.join(sorted(model.opex_by_category))}) "
        f"carried as fixed operating cost {annual_opex:,.2f} USD/yr; system VOC = 0, "
        "so startup VOC_frac is inert.",
        f"Depreciation: {resolved['depreciation']} on TDC; income tax "
        f"{resolved['income_tax']:.1%} with loss carry-forward (biosteam convention).",
        f"Discounting at discount_rate = {resolved['discount_rate']} (TEA IRR basis); "
        f"cashflow_table year {resolved['start_year'] - len(resolved['construction_schedule'])} "
        "onward: construction year(s) first, then operating years; the first "
        "construction year is the discounting origin (DF = 1).",
    ]
    if revenue > 0.0:
        assumptions.append(
            f"Revenue: flat {revenue:,.2f} USD/yr (basis.annual_revenue_usd); "
            "IRR and payback solved against it."
        )
    else:
        assumptions.append(
            "No annual_revenue_usd given: NPV is a net present cost; "
            "IRR and payback are undefined and returned as null."
        )
    if finance["loan_fraction"] > 0.0:
        assumptions.append(
            f"Financing: {finance['loan_fraction']:.0%} of fixed capital borrowed at "
            f"{finance['interest']:.2%} over {finance['years']} yr "
            "(level-payment amortization; interest during construction paid from equity)."
        )
    else:
        assumptions.append("No debt: 100% equity.")
    if startup["months"] > 0.0:
        assumptions.append(
            f"Startup: {startup['months']:.1f} months at FOC_frac={startup['FOC_frac']}, "
            f"sales_frac={startup['sales_frac']} in the first operating year."
        )
    if model.aace_class:
        assumptions.append(f"Ledger AACE class: {model.aace_class}.")

    npv = float(tea.NPV)

    irr_solved: Optional[float] = None
    payback_years: Optional[float] = None
    if revenue > 0.0:
        try:
            candidate = float(tea.solve_IRR())
        except Exception:
            candidate = float("nan")
        if candidate == candidate:  # not NaN
            irr_solved = candidate
        else:
            assumptions.append(
                "solve_IRR did not converge (cash flows may never turn positive); "
                "irr_solved returned as null."
            )
        net_earnings = float(tea.net_earnings)
        if net_earnings > 0.0:
            payback_years = float(tea.PBP)
            assumptions.append(
                "Payback = FCI / annual net earnings (biosteam PBP; undiscounted)."
            )
        else:
            assumptions.append(
                "Payback undefined: annual net earnings are not positive at this "
                "revenue; returned as null."
            )

    return {
        "npv_usd": npv,
        "irr_solved": irr_solved,
        "payback_years": payback_years,
        "cashflow_table": _cashflow_rows(tea),
        "capital": {
            "installed_equipment_cost_usd": float(tea.installed_equipment_cost),
            "DPI_usd": float(tea.DPI),
            "TDC_usd": float(tea.TDC),
            "FCI_usd": float(tea.FCI),
            "TCI_usd": float(tea.TCI),
            "working_capital_usd": float(tea.working_capital),
        },
        "operating": {
            "annual_opex_usd": annual_opex,
            "opex_by_category": model.opex_by_category,
            "annual_revenue_usd": revenue,
        },
        "basis": resolved,
        "assumptions": assumptions,
    }
