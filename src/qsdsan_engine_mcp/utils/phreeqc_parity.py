'PHREEQC parity of the twin\'s shared mADM1 speciation and mineral IAPs: the gate the owner restated on 2026-10-08\n(HOUSE-MADM1-ACID-CONSTANTS-EVALUATED-LITERATURE-20261008, "Evaluated literature (Recommended)"). Read-only; it\npersists nothing. Three parts, each reported on its own:\n\n    python -m utils.phreeqc_parity run --checkpoints <rows.jsonl> --output <parity.json> [--reference-db-dir <dir>]\n    python -m utils.phreeqc_parity gate <parity.json>        # exit 0 PASS, 1 FAIL, 2 incomplete or non-finite'
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

#: The USGS install's databases (the copies the 2026-10-08 review read).
REFERENCE_DB_DIR = Path("/mnt/c/Program Files/USGS/phreeqc-3.8.6-17100-x64/database")
REFERENCE_DBS = ("phreeqc.dat", "minteq.v4.dat")
ION_TOLERANCE_LOG = 1e-3
PH_TOLERANCE = 1e-3
#: twin ion name -> the matched database's species
MATCHED_ION = {"Ca2+": "Ca+2", "Mg2+": "Mg+2", "Fe2+": "Fe+2", "Fe3+": "Fx+3", "Al3+": "Al+3", "K+": "K+",
               "Na+": "Na+", "H+": "H+", "OH-": "OH-", "NH4+": "HNx+", "CO32-": "Cb-2", "HS-": "HSx-",
               "H2PO4-": "H2Px-", "HPO42-": "HPx-2", "PO43-": "Px-3"}
#: twin ion name -> the reference databases' species
REFERENCE_ION = {"Ca2+": "Ca+2", "Mg2+": "Mg+2", "Fe2+": "Fe+2", "Fe3+": "Fe+3", "Al3+": "Al+3", "K+": "K+",
                 "Na+": "Na+", "H+": "H+", "OH-": "OH-", "NH4+": "NH4+", "CO32-": "CO3-2", "HS-": "HS-",
                 "H2PO4-": "H2PO4-", "HPO42-": "HPO4-2", "PO43-": "PO4-3"}
#: the registry phases each reference database carries natively (name in that database), for the native-SI view
NATIVE_PHASES = {
    "phreeqc.dat": {"CCM": "Calcite", "ACC": "Aragonite", "HAP": "Hydroxyapatite", "FeS": "FeS(ppt)",
                    "Mackinawite": "Mackinawite", "Siderite": "Siderite", "Fe3PO42": "Vivianite"},
    "minteq.v4.dat": {"CCM": "Calcite", "ACC": "Aragonite", "HAP": "Hydroxylapatite", "ACP": "Ca3(PO4)2(beta)",
                      "DCPD": "CaHPO4:2H2O", "OCP": "Ca4H(PO4)3:3H2O", "newb": "MgHPO4:3H2O", "magn": "Magnesite",
                      "FeS": "FeS(ppt)", "Mackinawite": "Mackinawite", "Siderite": "Siderite", "Fe3PO42": "Vivianite"},
}
#: registry phases a reference database does not carry under any name (read from the 3.8.6 install's files): listed,
#: never silently dropped
NATIVE_NOT_CARRIED = {
    "phreeqc.dat": ("ACP", "DCPD", "OCP", "struv", "newb", "magn", "kstruv", "AlPO4"),
    "minteq.v4.dat": ("struv", "kstruv", "AlPO4"),
}
#: the shared solve's ligand keys -> the matched database's species
_KEY_SPECIES = {"Ca": "Ca+2", "Mg": "Mg+2", "Fe2": "Fe+2", "SO4": "Su-2", "NH4": "HNx+", "CO3": "Cb-2",
                "HCO3": "HCb-", "HPO4": "HPx-2", "H2PO4": "H2Px-", "HS": "HSx-", "H": "H+"}
_KEY_ELEMENTS = {"Ca": {"Ca": 1}, "Mg": {"Mg": 1}, "Fe2": {"Fe": 1}, "SO4": {"Su": 1}, "NH4": {"Nx": 1, "H": 1},
                 "CO3": {"Cb": 1}, "HCO3": {"Cb": 1, "H": 1}, "HPO4": {"Px": 1, "H": 1},
                 "H2PO4": {"Px": 1, "H": 2}, "HS": {"Sx": 1, "H": 1}, "H": {"H": 1}}
_VFAS = ("ac", "pro", "bu", "va")


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _charge(z: int) -> str:
    return "" if z == 0 else (("+" if z > 0 else "-") + (str(abs(z)) if abs(z) > 1 else ""))


def _pair_formula(pair) -> str:
    counts: dict[str, int] = {}
    for key, n in pair.reactants:
        for element, k in _KEY_ELEMENTS[key].items():
            counts[element] = counts.get(element, 0) + k * n
    order = ("Ca", "Mg", "Fe", "Nx", "Cb", "Px", "Sx", "Su", "H")
    body = "".join(f"{e}{counts[e] if counts[e] != 1 else ''}" for e in order if counts.get(e))
    return body + _charge(pair.charge)


def matched_database(pk: dict, ion_pairs) -> str:
    """The shared model as a PHREEQC database at ONE temperature: log K at T (``pk``: the shared solve's own pKa
    dictionary), no enthalpies, Davies for charged species (no -gamma), log gamma 0 for neutral ones (-gamma 0 0)."""
    neutral = "\n    -gamma 0 0"
    lines = [
        "SOLUTION_MASTER_SPECIES",
        "H     H+     -1  H   1.008", "H(0)  H2     0   H", "H(1)  H+     -1  H", "E     e-     0   0   0",
        "O     H2O    0   O   16.0", "O(0)  O2     0   O", "O(-2) H2O    0   0",
        "Na    Na+    0   Na  22.99", "K     K+     0   K   39.098", "Cl    Cl-    0   Cl  35.453",
        "Ca    Ca+2   0   Ca  40.078", "Mg    Mg+2   0   Mg  24.305", "Fe    Fe+2   0   Fe  55.845",
        "Fx    Fx+3   0   Fx  55.845", "Al    Al+3   0   Al  26.982", "Su    Su-2   0   Su  96.06",
        "Nx    Nx     0   Nx  14.007", "Cb    Cb-2   0   Cb  12.011", "Px    Px-3   0   Px  30.974",
        "Sx    HSx-   0   Sx  32.06",
        *(f"V{a}   V{a}-   0   V{a}  60.0" for a in _VFAS),
        "SOLUTION_SPECIES",
        "H+ = H+\n    log_k 0", "e- = e-\n    log_k 0", "H2O = H2O\n    log_k 0",
        f"H2O = OH- + H+\n    log_k {-pk['w']:.15g}",
        # PHREEQC requires H(0) and O(0); at the solution's default pe they hold nothing (phreeqc.dat's constants)
        "2 H+ + 2 e- = H2\n    log_k -3.15" + neutral, "2 H2O = O2 + 4 H+ + 4 e-\n    log_k -86.08" + neutral,
        *(f"{s} = {s}\n    log_k 0" for s in ("Na+", "K+", "Cl-", "Ca+2", "Mg+2", "Fe+2", "Fx+3", "Al+3", "Su-2",
                                               "Cb-2", "Px-3", "HSx-")),
        "Nx = Nx\n    log_k 0" + neutral,
        f"Nx + H+ = HNx+\n    log_k {pk['nh4']:.15g}",
        f"Cb-2 + H+ = HCb-\n    log_k {pk['co3']:.15g}",
        f"Cb-2 + 2H+ = H2Cb\n    log_k {pk['co3'] + pk['co2']:.15g}" + neutral,
        f"Px-3 + H+ = HPx-2\n    log_k {-pk['po4_log_k']:.15g}",
        f"Px-3 + 2H+ = H2Px-\n    log_k {-pk['po4_log_k'] + pk['h2po4']:.15g}",
        f"HSx- + H+ = H2Sx\n    log_k {pk['h2s']:.15g}" + neutral,
        *(f"V{a}- = V{a}-\n    log_k 0\nV{a}- + H+ = HV{a}\n    log_k {pk[a]:.15g}" + neutral for a in _VFAS),
    ]
    for pair in ion_pairs:
        lhs = " + ".join(f"{n if n != 1 else ''}{_KEY_SPECIES[key]}" for key, n in pair.reactants)
        line = f"{lhs} = {_pair_formula(pair)}\n    log_k {pk['ion_pairs'][pair.name]:.15g}"
        lines.append(line + (neutral if pair.charge == 0 else ""))
    return "\n".join(lines) + "\nEND\n"


class PhreeqcBinary:
    """One PHREEQC executable, run on a database and an input, read through SELECTED_OUTPUT. The binary is named by the
    caller and recorded (path, sha256 and the version line it prints): the reference databases of the USGS 3.8.6
    install need its own engine (phreeqpython's bundled engine refuses their MEAN_GAMMAS and gas-binary blocks), and
    an engine chosen by whatever happens to import would choose the answer."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"no PHREEQC executable at {self.path} (python -m utils.phreeqc_parity names one "
                                    "with --phreeqc)")
        self.sha256 = sha256(self.path)

    def run(self, database: Path, body: str, columns: dict) -> list[dict]:
        """``columns``: {"activities": [species], "molalities": [species], "si": [phases]}; one row per SOLUTION."""
        import subprocess

        with tempfile.TemporaryDirectory(prefix="phreeqc-parity-") as tmp:
            sel = Path(tmp) / "sel.txt"
            # the SELECTED_OUTPUT definition precedes the first SOLUTION (it then punches every later simulation)
            pre, solutions = body.split("#SOLUTIONS#\n", 1) if "#SOLUTIONS#\n" in body else ("", body)
            text = pre + "\n".join([
                "SELECTED_OUTPUT 1", f" -file {sel}", " -reset false", " -solution true", " -pH true",
                " -ionic_strength true", " -water true",
                " -activities " + " ".join(["H2O", *columns.get("activities", ())]),
                *([" -molalities " + " ".join(columns["molalities"])] if columns.get("molalities") else []),
                *([" -saturation_indices " + " ".join(columns["si"])] if columns.get("si") else []), ""]) \
                + solutions + "\nEND\n"
            (Path(tmp) / "in.pqi").write_text(text)
            out = Path(tmp) / "out.pqo"
            proc = subprocess.run([str(self.path), str(Path(tmp) / "in.pqi"), str(out), str(database)],
                                  capture_output=True, text=True, timeout=600)
            log = out.read_text() if out.is_file() else ""
            errors = [line for line in (proc.stdout + proc.stderr + log).splitlines() if "ERROR" in line]
            if proc.returncode or errors or not sel.is_file():
                raise RuntimeError(f"PHREEQC refused ({proc.returncode}): {errors[:5] or proc.stderr[-500:]}")
            lines = [l.split() for l in sel.read_text().splitlines() if l.strip()]
        header, rows = lines[0], lines[1:]
        return [dict(zip(header, (float(v) for v in row))) for row in rows]

    def version(self) -> str:
        """The build's own provenance note (``SOURCE.txt`` beside the binary) or the binary's first version line: the
        3.8.6 source prints no version string, so a note written at build time is the record."""
        note = self.path.parent / "SOURCE.txt"
        if note.is_file():
            return note.read_text().strip()
        return "no SOURCE.txt beside the binary; the PHREEQC source prints no version string"


def _la(row: dict, species: str):
    v = row.get(f"la_{species}")
    return None if v is None or v <= -999 else 10.0 ** v


def _solution_block(number: int, temp_c: float, ph, totals: dict) -> str:
    lines = [f"SOLUTION {number}", " units mol/kgw", f" temp {temp_c:.12g}", " water 1",
             (" pH 7 charge" if ph == "charge" else f" pH {ph:.15g}")]
    lines += [f" {k} {v:.15g}" for k, v in totals.items() if v > 0]
    return "\n".join(lines) + "\n"


def twin_inputs(state, cmps, params, *, davies_a: float | None = None) -> dict:
    """The twin's own numbers at one stored state: the shared solve (pcm_speciation), its activities, every registry
    IAP, and the totals (mol/kg water) the solve used, element by element. ``davies_a``: solve with THIS Debye-Hueckel A
    instead of the shared Kell/Malmberg-Maryott correlation (PHREEQC's own A, backed out of its Davies gamma), so the
    one part of the activity model PHREEQC cannot be given is matched from the other side; the harness reports both."""
    import contextlib
    from unittest import mock

    import puran_mcp_support.chemistry.madm1_speciation as shared
    from qsdsan_engine_mcp.models.madm1 import _davies_liquor, madm1_mass2mol, pcm_speciation
    from qsdsan_engine_mcp.models.thermodynamics import MINERAL_PHASES, activities_from_shared_speciation

    t_k = float(params.get("T_op", params["T_base"]))
    u = madm1_mass2mol(cmps)
    patch = (mock.patch.object(shared, "davies_A", lambda temperature_k: float(davies_a)) if davies_a is not None
             else contextlib.nullcontext())
    with patch:
        spec = pcm_speciation(state, params)
    acts = activities_from_shared_speciation(state, cmps, u, spec, t_k)
    liquor = _davies_liquor(np.asarray(state[:len(cmps)], dtype=float), cmps, u,
                            params.get("model_reduction") or {})

    def molar(cid):
        return float(state[cmps.index(cid)]) * float(u[cmps.index(cid)]) if cid in cmps.IDs else 0.0

    m = float(spec["molar_to_molal"])
    strong = {"Na": molar("S_Na"), "K": molar("S_K"), "Cl": molar("S_Cl"), "Fx": molar("S_Fe3"),
              "Al": molar("S_Al")}
    totals_M = {**strong, "Ca": liquor.Ca, "Mg": liquor.Mg, "Fe": liquor.Fe2, "Su": liquor.SO4, "Nx": liquor.IN,
                "Px": liquor.IP, "Cb": liquor.IC, "Sx": liquor.IS, **{f"V{a}": getattr(liquor, a) for a in _VFAS}}
    # the solve's strong lumps must be exactly these ions (fail closed on any lump the harness does not carry)
    charge = (strong["Na"] + strong["K"] + 2 * (liquor.Ca + liquor.Mg + liquor.Fe2) + 3 * (strong["Fx"] + strong["Al"])
              - strong["Cl"] - 2 * liquor.SO4)
    ionic = (strong["Na"] + strong["K"] + 4 * (liquor.Ca + liquor.Mg + liquor.Fe2) + 9 * (strong["Fx"] + strong["Al"])
             + strong["Cl"] + 4 * liquor.SO4)
    for got, want, what in ((charge, liquor.strong_charge_M, "strong charge"), (ionic, liquor.strong_ionic_M,
                                                                                 "strong ionic sum")):
        if not math.isclose(got, want, rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError(f"the solve's {what} {want!r} is not the harness's ions {got!r}: a lumped ion the "
                             "harness does not carry")
    return {"T_K": t_k, "pH": float(spec["pH"]), "ionic_strength": float(spec["ionic_strength_molal"]),
            "davies_A": float(spec["A"]), "molar_to_molal": m,
            "totals_molal": {k: v * m for k, v in totals_M.items()},
            "pk": {k: (dict(v) if isinstance(v, dict) else float(v)) for k, v in spec["pKa"].items()},
            "activities": {k: float(v) for k, v in acts.items()},
            "log10_iap": {p.name: _log(float(p.iap(acts))) for p in MINERAL_PHASES},
            # the twin's own saturation index (its IAP over its own Ksp(T)), beside the databases' native SIs
            "log10_si": {p.name: (_log(float(p.iap(acts))) - math.log10(p.ksp(t_k))
                                  if _log(float(p.iap(acts))) is not None else None) for p in MINERAL_PHASES},
            "fe_hs2_share_of_fe": (float(spec["species_M"].get("Fe(HS)2", 0.0)) / liquor.Fe2 if liquor.Fe2 else None)}


def _log(x: float):
    return math.log10(x) if x > 0 and math.isfinite(x) else None


def _iaps(acts: dict) -> dict:
    from qsdsan_engine_mcp.models.thermodynamics import MINERAL_PHASES

    return {p.name: _log(float(p.iap(acts))) for p in MINERAL_PHASES}


def run_matched(inputs: dict, phreeqc: PhreeqcBinary) -> dict:
    """Implementation parity: the matched database, at the twin's pH (activities, IAPs) and by charge balance (pH)."""
    from puran_mcp_support.chemistry.madm1_speciation import ION_PAIRS

    text = matched_database(inputs["pk"], ION_PAIRS)
    t_c = inputs["T_K"] - 273.15
    totals = inputs["totals_molal"]
    with tempfile.TemporaryDirectory(prefix="matched-db-") as tmp:
        db = Path(tmp) / "matched.dat"
        db.write_text(text)
        rows = phreeqc.run(db, "#SOLUTIONS#\n" + _solution_block(1, t_c, inputs["pH"], totals) + "END\n"
                           + _solution_block(2, t_c, "charge", totals),
                           {"activities": list(MATCHED_ION.values()), "molalities": ["Na+"]})
    fixed, cb = rows[0], rows[1]
    acts = {ion: (_la(fixed, species) or 0.0) for ion, species in MATCHED_ION.items()}
    ionic = fixed["mu"]
    log_g_na = math.log10(_la(fixed, "Na+") / fixed["m_Na+"]) if fixed.get("m_Na+", 0) > 0 else None
    root = math.sqrt(ionic)
    a_phreeqc = (-log_g_na / (root / (1 + root) - 0.3 * ionic)) if log_g_na is not None else None
    return {"database_sha256": hashlib.sha256(text.encode()).hexdigest(), "database_text": text,
            "ion_activities": acts, "log10_iap": _iaps(acts), "ionic_strength": ionic, "charge_balance_pH": cb["pH"],
            # PHREEQC's water activity reaches OH- only (H2O = OH- + H+); the shared model takes a_w 1
            "davies_A_phreeqc": a_phreeqc, "water_activity": _la(fixed, "H2O")}


def run_reference(inputs: dict, database: Path, phreeqc: PhreeqcBinary) -> dict:
    """Reference sensitivity on one unmodified database: VFAs as custom acids in BOTH runs (review defect 4)."""
    m = inputs["totals_molal"]
    names = {"Na": "Na", "K": "K", "Cl": "Cl", "Ca": "Ca", "Mg": "Mg", "Fe": "Fe(2)", "Fx": "Fe(3)", "Al": "Al",
             "Su": "S(6)", "Nx": "N(-3)", "Px": "P", "Cb": "C(4)", "Sx": "S(-2)"}
    totals = {**{names[k]: v for k, v in m.items() if k in names},
              **{f"V{a}": m.get(f"V{a}", 0.0) for a in _VFAS}}
    vfa_def = "".join(
        f"SOLUTION_MASTER_SPECIES\nV{a} V{a}- 0 V{a} 1\nSOLUTION_SPECIES\nV{a}- = V{a}-\n log_k 0\n"
        f"V{a}- + H+ = HV{a}\n log_k {inputs['pk'][a]:.15g}\n" for a in _VFAS)
    native_names = NATIVE_PHASES.get(database.name, {})
    t_c = inputs["T_K"] - 273.15
    rows = phreeqc.run(database, vfa_def + "#SOLUTIONS#\n" + _solution_block(1, t_c, inputs["pH"], totals) + "END\n"
                       + _solution_block(2, t_c, "charge", totals),
                       {"activities": list(REFERENCE_ION.values()), "si": list(native_names.values()),
                        "molalities": ["Fe(HS)2"]})
    fixed, cb = rows[0], rows[1]
    acts = {ion: (_la(fixed, species) or 0.0) for ion, species in REFERENCE_ION.items()}
    fe_total = m.get("Fe", 0.0)
    native = {phase: (fixed.get(f"si_{name}") if fixed.get(f"si_{name}", -999) > -999 else f"not carried: {name}")
              for phase, name in native_names.items()}
    native.update({phase: "not carried by this database" for phase in NATIVE_NOT_CARRIED.get(database.name, ())})
    return {"database": str(database), "database_sha256": sha256(database), "ionic_strength": fixed["mu"],
            "ion_activities": acts, "log10_iap": _iaps(acts), "native_si": native, "charge_balance_pH": cb["pH"],
            # review defect 6: Fe(HS)2's share of the iron, computed, never quoted from memory
            "fe_hs2_share_of_fe": (fixed.get("m_Fe(HS)2", 0.0) / fe_total) if fe_total > 0 else None}


def compare(inputs: dict, matched: dict) -> dict:
    """Twin minus matched PHREEQC: every free ion (OH- after PHREEQC's water activity), every registry IAP, pH."""
    ions, missing = {}, []
    for ion in MATCHED_ION:
        tw, ph = inputs["activities"].get(ion, 0.0), matched["ion_activities"].get(ion, 0.0)
        if tw <= 0 and ph <= 0:
            continue                                           # an ion the state does not carry (Fe3+, Al3+ at 0)
        if not (tw > 0 and ph > 0):
            missing.append(ion)
            continue
        d = math.log10(tw) - math.log10(ph)
        if ion == "OH-" and matched.get("water_activity"):
            d += math.log10(matched["water_activity"])
        ions[ion] = d
    iaps = {}
    for phase, tw in inputs["log10_iap"].items():
        ph = matched["log10_iap"].get(phase)
        if tw is None and ph is None:
            continue
        if tw is None or ph is None:
            missing.append(phase)
            continue
        iaps[phase] = tw - ph
    return {"delta_log10_activity": ions, "delta_log10_iap": iaps,
            "delta_pH": inputs["pH"] - matched["charge_balance_pH"], "missing": missing,
            "delta_davies_A": (inputs["davies_A"] - matched["davies_A_phreeqc"]
                               if matched.get("davies_A_phreeqc") is not None else None)}


def gate(result: dict, *, ion_tol: float = ION_TOLERANCE_LOG, ph_tol: float = PH_TOLERANCE,
         comparison: str = "matched_comparison") -> tuple[int, list]:
    """(exit code, findings): 0 PASS, 1 FAIL (a difference over its tolerance), 2 incomplete (a state, an ion or a
    registry phase missing, or a non-finite value). Every registry phase is required; none is skipped. ``comparison``:
    ``matched_comparison`` (the twin as it runs) or ``matched_comparison_on_phreeqc_A`` (the twin on PHREEQC's A)."""
    from qsdsan_engine_mcp.models.thermodynamics import MINERAL_PHASES

    findings, code = [], 0
    states = result.get("states") or {}
    if not states:
        return 2, ["no states"]
    for sid, entry in states.items():
        cmp_ = entry.get(comparison)
        if not cmp_:
            findings.append(f"{sid}: no matched comparison")
            code = 2
            continue
        if cmp_.get("missing"):
            findings.append(f"{sid}: missing {cmp_['missing']}")
            code = 2
        for phase in MINERAL_PHASES:
            if entry["twin"]["log10_iap"].get(phase.name) is not None and phase.name not in cmp_["delta_log10_iap"]:
                findings.append(f"{sid}: registry phase {phase.name} not compared")
                code = 2
        values = [("pH", cmp_.get("delta_pH"), ph_tol)]
        values += [(f"ion {k}", v, ion_tol) for k, v in cmp_["delta_log10_activity"].items()]
        values += [(f"IAP {k}", v, ion_tol) for k, v in cmp_["delta_log10_iap"].items()]
        for what, v, tol in values:
            if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
                findings.append(f"{sid}: {what} is not finite ({v!r})")
                code = 2
            elif abs(v) > tol:
                findings.append(f"{sid}: {what} {v:+.6f} beyond {tol}")
                code = max(code, 1) if code != 2 else 2
    return code, findings


def run(checkpoints: Path, output: Path, phreeqc_path: Path, reference_dir: Path = REFERENCE_DB_DIR) -> dict:
    from qsdsan_engine_mcp.models.madm1 import ModifiedADM1
    from qsdsan_engine_mcp.utils.simulate_madm1 import get_validated_components

    cmps = get_validated_components()
    proc = ModifiedADM1(components=cmps)
    params = proc.rate_function._params
    phreeqc = PhreeqcBinary(phreeqc_path)
    result = {"checkpoints": str(checkpoints), "checkpoints_sha256": sha256(checkpoints),
              "phreeqc": {"path": str(phreeqc.path), "sha256": phreeqc.sha256, "version": phreeqc.version()},
              "reference_databases": {db: {"path": str(reference_dir / db), "sha256": sha256(reference_dir / db)}
                                      for db in REFERENCE_DBS},
              "tolerances": {"ion_log10": ION_TOLERANCE_LOG, "pH": PH_TOLERANCE}, "states": {}}
    for line in Path(checkpoints).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sid, ck = row["scope"], row["content"]["checkpoint"]
        ad = ck["units"]["AD"]
        values = dict(zip(ad["component_ids"], ad["state"]))
        params["T_op"] = float(ck["streams"]["Influent"]["thermo"]["T"])
        state = np.zeros(len(cmps) + 5)
        for i, cid in enumerate(cmps.IDs):
            state[i] = float(values.get(cid, 0.0))
        twin = twin_inputs(state, cmps, params)
        matched = run_matched(twin, phreeqc)
        # the same parity with the twin solved on PHREEQC's own Debye-Hueckel A (the one activity-model input PHREEQC
        # takes from its own water properties and cannot be given): what remains is the implementation
        twin_a = twin_inputs(state, cmps, params, davies_a=matched["davies_A_phreeqc"])
        matched_a = run_matched(twin_a, phreeqc)
        entry = {"T_K": twin["T_K"], "twin": twin, "matched": {k: v for k, v in matched.items() if k != "database_text"},
                 "matched_comparison": compare(twin, matched),
                 "twin_on_phreeqc_A": twin_a,
                 "matched_comparison_on_phreeqc_A": compare(twin_a, matched_a), "reference": {}}
        for db in REFERENCE_DBS:
            ref = run_reference(twin, reference_dir / db, phreeqc)
            ref["delta_log10_iap_common_convention"] = {
                p: (twin["log10_iap"][p] - v if None not in (twin["log10_iap"].get(p), v) else None)
                for p, v in ref["log10_iap"].items()}
            ref["delta_pH_charge_balance"] = twin["pH"] - ref["charge_balance_pH"]
            entry["reference"][db] = ref
        result["states"][sid] = entry
    output.write_text(json.dumps(result, indent=1, default=str))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m utils.phreeqc_parity")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--checkpoints", required=True, type=Path)
    r.add_argument("--output", required=True, type=Path)
    r.add_argument("--reference-db-dir", type=Path, default=REFERENCE_DB_DIR)
    r.add_argument("--phreeqc", type=Path, required=True, help="the PHREEQC executable (recorded with its sha256)")
    g = sub.add_parser("gate")
    g.add_argument("result", type=Path)
    g.add_argument("--on-phreeqc-a", action="store_true",
                   help="gate the twin solved on PHREEQC's own Debye-Hueckel A (the implementation alone)")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        run(args.checkpoints, args.output, args.phreeqc, args.reference_db_dir)
        return 0
    code, findings = gate(json.loads(args.result.read_text()),
                          comparison="matched_comparison_on_phreeqc_A" if args.on_phreeqc_a else "matched_comparison")
    for f in findings:
        print(f)
    print({0: "PASS", 1: "FAIL", 2: "INCOMPLETE"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
