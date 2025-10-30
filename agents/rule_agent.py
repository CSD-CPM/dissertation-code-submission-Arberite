from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict
import sys
import csv
from pathlib import Path
import json



BASE_DIR = Path(__file__).resolve().parents[1]  # project root (…/multi_agent_system)
REPORTS_DIR = BASE_DIR / "data" / "out" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Transcript result code groups ---
PASS_CODES = {"P", "PC", "PR", "S", "SR"}        # pass or compensated pass routes
FAIL_CODES = {"F", "FR", "FD", "R"}              # fail routes
NEUTRAL_CODES = {"I", "D", "RA", "NM", "WD", "T", "V", "AU"}  # in-progress/administrative
ALL_KNOWN_CODES = PASS_CODES | FAIL_CODES | NEUTRAL_CODES

def rint(x: float) -> int:
    """Round to nearest integer 0..100 with safe clamp."""
    return max(0, min(100, int(round(x))))

def clamp_mark(level: str, program: str, mark: int) -> int:
    """Cap to pass mark after reassessment when used for certain calculations."""
    pm = pass_mark(level, program)
    return min(mark, pm)

def pass_mark(level: str, program: str) -> int:
    """
    UCC:
      - C/I/H -> 40
      - M     -> 50
    PCC:
      - M-level -> 50
      - H-level -> 40 (H-level in PCC are pass/fail)
    """
    L = level.upper()
    if program == "UCC":
        if L in ("C", "I", "H"): return 40
        if L == "M": return 50
    elif program == "PCC":
        if L == "M": return 50
        if L == "H": return 40
    return 40

def classification_band(mark: int) -> str:
    """UCC bands."""
    if 70 <= mark <= 100: return "First class"
    if 60 <= mark <= 69:  return "Upper-second class"
    if 50 <= mark <= 59:  return "Lower-second class"
    if 40 <= mark <= 49:  return "Third class"
    return "Below honours classification range"

def is_marginal_fail(mark: int, program: str, level: str) -> bool:
    pm = pass_mark(level, program)
    return (mark < pm) and (pm - mark <= 10)

def is_outright_fail(mark: int, program: str, level: str, pass_fail: bool) -> bool:
    """margin >10 OR any fail on pass/fail."""
    pm = pass_mark(level, program)
    if pass_fail:
        return mark < pm
    return (mark < pm) and ((pm - mark) > 10)

# -------------------------------
# DATA MODELS
# -------------------------------

@dataclass
class Module:
    name: str
    stage: int
    level: str                  # "C","I","H","M"
    credits: int
    is_pass_fail: bool = False
    is_non_compensatable: bool = False
    is_cpm: bool = False        # PCC CPM
    attempt1_mark: Optional[int] = None
    took_reassessment: bool = False
    attempt2_mark: Optional[int] = None
    result_code: Optional[str] = None

    def rounded_marks(self):
        m1 = None if self.attempt1_mark is None else rint(self.attempt1_mark)
        m2 = None if self.attempt2_mark is None else rint(self.attempt2_mark)
        return m1, m2

@dataclass
class StudentRecord:
    program: str  # "UCC" or "PCC"
    modules: List[Module] = field(default_factory=list)
    pcc_wishes_to_proceed_to_cpm: Optional[bool] = None

    # Summary/override inputs ---
    # UCC shortcuts
    ucc_stage2_avg_override: Optional[int] = None
    ucc_stage3_avg_override: Optional[int] = None
    ucc_all_awarded_override: Optional[bool] = None
    ucc_final_award_mark_override: Optional[int] = None  # single final number (optional)

    # PCC shortcuts
    pcc_programme_avg_override: Optional[int] = None
    pcc_cpm_mark_override: Optional[int] = None


# EXPLANATION LOGGER
class Explainer:
    def __init__(self):
        self.steps: List[str] = []
    def add(self, s: str): self.steps.append(s)
    def extend(self, items: List[str]): self.steps.extend(items)
    def flush(self) -> str:
        text = "\n".join(self.steps); self.steps.clear(); return text

# CORE ENGINE
def load_modules_from_csv(path: str, rec: StudentRecord) -> Tuple[int, List[str]]:
    """
    Load module rows from a CSV into the current StudentRecord.
    Returns (count_imported, warnings).

    Expected header names (case-insensitive, extra columns ignored):
      module_name, name, module, code            -> Module name (first non-empty among these)
      stage                                      -> 1/2/3
      level                                      -> C/I/H/M
      credits, credit                            -> int
      pass_fail, is_pass_fail                    -> y/n, true/false, 1/0
      non_compensatable, is_non_compensatable    -> y/n, true/false, 1/0
      cpm, is_cpm                                 -> y/n, true/false, 1/0
      attempt1, attempt1_mark, mark              -> 0-100 or blank
      reassessed, took_reassessment              -> y/n, true/false, 1/0
      attempt2, attempt2_mark                    -> 0-100 or blank
    """
    warnings = []
    p = Path(path)
    if not p.exists():
        return 0, [f"File not found: {path}"]


    # normalize header mapping
    def norm(s): return (s or "").strip().lower()

    def parse_bool(v):
        if v is None: return False
        v = str(v).strip().lower()
        return v in ("y", "yes", "true", "1")

    def parse_int_or_none(v):
        if v is None: return None
        sv = str(v).strip()
        if sv == "": return None
        try:
            return int(float(sv))
        except:
            return None

    loaded = 0
    with p.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return 0, ["CSV appears to have no header row."]
        header = [norm(h) for h in reader.fieldnames]

        # convenience finders
        def get(row, *aliases):
            for a in aliases:
                if a in row and row[a] not in (None, ""):
                    return row[a]
            return None

        for raw_row in reader:
            # lowercase keys for robust lookups
            row = {norm(k): v for k, v in raw_row.items()}

            name = get(row, "module_name", "name", "module", "code") or "Module"
            stage = parse_int_or_none(get(row, "stage"))
            level = (get(row, "level") or "").strip().upper() or "H"
            credits = parse_int_or_none(get(row, "credits", "credit"))
            is_pass_fail = parse_bool(get(row, "pass_fail", "is_pass_fail"))
            is_noncomp = parse_bool(get(row, "non_compensatable", "is_non_compensatable"))
            is_cpm = parse_bool(get(row, "cpm", "is_cpm"))
            a1 = parse_int_or_none(get(row, "attempt1", "attempt1_mark", "mark"))
            took_re = parse_bool(get(row, "reassessed", "took_reassessment"))
            a2 = parse_int_or_none(get(row, "attempt2", "attempt2_mark"))
            result_code = (get(row, "mark_scale_result") or "").strip().upper()

            # minimal validation
            if stage not in (1, 2, 3):
                warnings.append(f"{name}: invalid stage '{stage}', skipped.")
                continue
            if level not in ("C", "I", "H", "M"):
                warnings.append(f"{name}: invalid level '{level}', defaulted to 'H'.")
                level = "H"
            if credits is None or credits <= 0:
                warnings.append(f"{name}: invalid credits '{credits}', skipped.")
                continue

            rec.modules.append(Module(
                name=name,
                stage=stage,
                level=level,
                credits=credits,
                is_pass_fail=is_pass_fail,
                is_non_compensatable=is_noncomp,
                is_cpm=is_cpm if rec.program == "PCC" else False,
                attempt1_mark=a1,
                took_reassessment=took_re,
                attempt2_mark=a2,
                result_code=result_code
            ))
            loaded += 1

    return loaded, warnings


class RulesEngine:
    """Implements the UCC & PCC rules with post-reassessment compensation."""

    def __init__(self, record: StudentRecord, explain: bool = False):
        self.rec = record
        self.explain = explain
        self.X = Explainer()

    # ---------- Shared helpers ----------
    def _best_attempt_mark_for_calc(self, m: Module, context: str) -> Optional[int]:
        m1, m2 = m.rounded_marks()
        L = m.level.upper(); P = self.rec.program
        if m1 is None and m2 is None: return None
        best_raw = m1 if (m2 is None or (m1 is not None and m1 >= m2)) else m2

        if P == "UCC":
            if context in ("for_credit","for_compensation","for_classification","for_stage_average"):
                return clamp_mark(L, P, best_raw)
            return best_raw

        # PCC
        if context == "for_credit":
            return clamp_mark(L, P, best_raw)
        if context == "for_compensation":
            return m2 if m2 is not None else m1  # PCC comp after reassessment uses latest
        if context in ("for_merit_distinction","for_stage_average","for_award_mean"):
            return clamp_mark(L, P, best_raw)
        return best_raw

    def _module_status(self, m: Module) -> Dict[str, bool]:
        m1, _ = m.rounded_marks()
        if m1 is None:
            # No mark yet - not passed, not failed, just unmarked
            return {
                "passed": False,
                "marginal_fail": False,
                "outright_fail": False,
                "failed": False,      
                "not_marked": True    
            }
        pm = pass_mark(m.level, self.rec.program)
        marginal = is_marginal_fail(m1, self.rec.program, m.level)
        outright = is_outright_fail(m1, self.rec.program, m.level, m.is_pass_fail)
        passed = (m1 >= pm) if not m.is_pass_fail else (m1 >= pm)
        failed = not passed
        return {
            "passed": passed,
            "marginal_fail": marginal and failed,
            "outright_fail": outright and failed,
            "failed": failed,
            "not_marked": False
    }

    # ---------- UCC helpers ----------

    def _ucc_compensation_possible_for_stage(self, stage: int) -> Tuple[bool, List[Module]]:
        stage_mods = [m for m in self.rec.modules if m.stage == stage]
        if not stage_mods: return (True, [])
        failed_credits = 0; has_outright = False; elig=[]
        for m in stage_mods:
            st = self._module_status(m)
            if st["failed"]:
                if is_outright_fail(rint(m.attempt1_mark or 0), "UCC", m.level, m.is_pass_fail):
                    has_outright = True
                if (not m.is_pass_fail) and (not m.is_non_compensatable):
                    failed_credits += m.credits; elig.append(m)
        if self.explain: self.X.add(f"UCC compensation check Stage {stage}: failed_credits={failed_credits}, outright={has_outright}")
        if failed_credits > 40 or has_outright: return (False, [])
        numer=denom=0
        for m in stage_mods:
            mk = self._best_attempt_mark_for_calc(m,"for_stage_average")
            if mk is None: continue
            numer += mk*m.credits; denom += m.credits
        stage_mean = rint(numer/denom) if denom else 0
        if self.explain: self.X.add(f"UCC Stage {stage} mean (rounded)={stage_mean}")
        return (stage_mean >= 40, elig)

    def _ucc_stage_mean(self, stage: int) -> int:
        # Overrides take precedence
        if stage == 2 and self.rec.ucc_stage2_avg_override is not None:
            return int(self.rec.ucc_stage2_avg_override)
        if stage == 3 and self.rec.ucc_stage3_avg_override is not None:
            return int(self.rec.ucc_stage3_avg_override)

        # Fall back to computing from modules
        mods = [m for m in self.rec.modules if m.stage == stage and not m.is_pass_fail]
        numer = denom = 0
        for m in mods:
            mk = self._best_attempt_mark_for_calc(m, "for_stage_average")
            if mk is None:
                continue
            numer += mk * m.credits
            denom += m.credits
        return rint(numer / denom) if denom else 0

    def _ucc_borderline_hit(self, unrounded: float) -> bool:
        for b in (40,50,60,70):
            if (unrounded >= b-2) and (unrounded < b): return True
        return False

    def _ucc_award_and_classification(self, awarded: Dict[str,bool]) -> Dict[str,str]:
        out={}

        # If user provided a single final award mark, use it 
        if self.rec.ucc_final_award_mark_override is not None:
            award = int(self.rec.ucc_final_award_mark_override)
            out["award_mark_main"] = str(award)
            out["classification"]   = classification_band(award)
            out["award"]            = "Honours Bachelor’s degree" if award >= 40 else "No award yet"
            if self.explain:
                self.X.add("Used UCC final award mark override (boundary review and 39.5 floor skipped).")
            return out

        st3_aw = sum(m.credits for m in self.rec.modules if m.stage == 3 and awarded.get(m.name, False))
        honours_ok = (st3_aw >= 120)

        # If user says all Stage 3 credits are awarded, accept it in override mode
        if self.rec.ucc_all_awarded_override is True:
            honours_ok = True
        if self.explain: self.X.add(f"UCC Stage 3 credits awarded {st3_aw}/120 (override_all_awarded={self.rec.ucc_all_awarded_override})")

        if honours_ok:
            s2 = self._ucc_stage_mean(2); s3 = self._ucc_stage_mean(3)
            if self.explain: self.X.extend([f"S2 mean={s2}", f"S3 mean={s3}"])
            unrounded = (2*s2 + 3*s3)/5.0; award = rint(unrounded)
            out["award_mark_main"] = f"{award} (unrounded {unrounded:.2f})"

            if self._ucc_borderline_hit(unrounded):
                alt11 = rint((s2+s3)/2.0); alt12 = rint((s2+2*s3)/3.0)
                main_band = classification_band(award)
                # choose the best band
                rank = {"Below honours classification range":0, "Third class":1, "Lower-second class":2, "Upper-second class":3, "First class":4}
                best = max([main_band, classification_band(alt11), classification_band(alt12)], key=lambda b: rank[b])
                out["classification"] = best
            else:
                out["classification"] = classification_band(award)

            # 39.5 floor → Third (we express as 39.5 in output)
            if unrounded < 39.5:
                out["award_mark_main"]="39.5 (floor)"
                out["classification"]="Third class (floor)"
            out["award"]="Honours Bachelor’s degree"
            return out

        # Ordinary degree check: ≥60 H-level credits at Stage 3
        h60 = sum(m.credits for m in self.rec.modules if m.stage==3 and m.level.upper()=="H" and awarded.get(m.name,False))
        if self.explain: self.X.add(f"Ordinary check: Stage 3 H credits = {h60}")
        if h60 >= 60:
            out["award"]="Ordinary Bachelor’s degree"; return out
        out["award"]="No award yet (insufficient credits)"; return out

    # ---------- PCC helpers ----------

    def _pcc_merit_distinction(self) -> Optional[Dict[str,str]]:
        # Override path
        if self.rec.pcc_programme_avg_override is not None:
            mean = int(self.rec.pcc_programme_avg_override)
            cpm_marks = []
            if self.rec.pcc_cpm_mark_override is not None:
                cpm_marks = [int(self.rec.pcc_cpm_mark_override)]
            cpm_ok60 = all(mk >= 60 for mk in cpm_marks) if cpm_marks else True
            cpm_ok70 = all(mk >= 70 for mk in cpm_marks) if cpm_marks else True
            if self.explain:
                self.X.extend([
                    f"(override) overall mean={mean}",
                    f"(override) CPM≥60? {cpm_ok60} | CPM≥70? {cpm_ok70}"
                ])
            if mean>=70 and cpm_ok70: return {"classification":"Distinction","award_mark_main":str(mean)}
            if mean>=60 and cpm_ok60: return {"classification":"Merit","award_mark_main":str(mean)}
            return None

        # Module-derived path
        numer=denom=0; cpm_marks=[]
        for m in self.rec.modules:
            if m.is_pass_fail: continue
            mk = self._best_attempt_mark_for_calc(m,"for_award_mean")
            if mk is None: continue
            numer += mk*m.credits; denom += m.credits
            if m.is_cpm: cpm_marks.append(mk)
        mean = rint(numer/denom) if denom else 0
        cpm_ok60 = all(mk>=60 for mk in cpm_marks) if cpm_marks else True
        cpm_ok70 = all(mk>=70 for mk in cpm_marks) if cpm_marks else True
        if self.explain: self.X.extend([f"overall mean={mean}", f"CPM≥60? {cpm_ok60} | CPM≥70? {cpm_ok70}"])
        if mean>=70 and cpm_ok70: return {"classification":"Distinction","award_mark_main":str(mean)}
        if mean>=60 and cpm_ok60: return {"classification":"Merit","award_mark_main":str(mean)}
        return None

    def _pcc_award(self, awarded: Dict[str,bool]) -> Dict[str,str]:
        out={}
        taught=[m for m in self.rec.modules if not m.is_cpm]
        cpm=[m for m in self.rec.modules if m.is_cpm]
        taught_aw = sum(m.credits for m in taught if awarded.get(m.name,False))
        cpm_aw = sum(m.credits for m in cpm if awarded.get(m.name,False))
        total_aw = taught_aw + cpm_aw
        req_taught = sum(m.credits for m in taught)
        req_cpm = sum(m.credits for m in cpm)
        requires_cpm = (req_cpm>0)
        masters_ok = (taught_aw>=req_taught) and (not requires_cpm or cpm_aw>=req_cpm)
        if self.explain: self.X.extend([f"taught_awarded={taught_aw}/{req_taught}", f"CPM required={requires_cpm}, cpm_awarded={cpm_aw}/{req_cpm}"])
        if masters_ok:
            out["award"]="Masters"
            md = self._pcc_merit_distinction()
            if md: out.update(md)
            return out
        if total_aw >= 60 and total_aw < 120:
            out["award"]="Postgraduate Certificate (PGCert)"; return out
        if taught_aw >= 120 and (self.rec.pcc_wishes_to_proceed_to_cpm is False):
            out["award"]="Postgraduate Diploma (PGDip)"; out["reason"]="120 taught credits and not proceeding to CPM"; return out
        if total_aw >= 120:
            out["award"]="Postgraduate Diploma (PGDip)"; out["reason"]="≥120 credits but Masters criteria not met"; return out
        out["award"]="No award yet (insufficient credits)"; return out

    # ---------- CREDIT AWARD PIPELINE ----------
    

    def award_credits_with_compensation_and_reassessment(self) -> Dict[str,bool]:
        awarded = {m.name: False for m in self.rec.modules}
        # 0) Honor transcript result codes first
        for m in self.rec.modules:
            code = (m.result_code or "").upper()
            if code in PASS_CODES:
                awarded[m.name] = True
                if self.explain:
                    self.X.add(f"{m.name}: PASS by transcript code ({code}).")
            elif code in FAIL_CODES:
                # Explicit fail code → ensure not awarded here; later steps may still try,
                # but we keep it explicit in the explanation.
                if self.explain:
                    self.X.add(f"{m.name}: FAIL by transcript code ({code}).")

        # 1) Direct pass by mark (don’t override earlier PASS by code)
        for m in self.rec.modules:
            if awarded[m.name]:
                continue
            mk = self._best_attempt_mark_for_calc(m, "for_credit")
            if mk is None:
                continue
            if mk >= pass_mark(m.level, self.rec.program):
                awarded[m.name] = True
                if self.explain:
                    self.X.add(f"{m.name}: PASS by mark (≥ pass).")

        # 2) Compensation (pre-reassessment)
        if self.rec.program=="UCC":
            for st in (1,2,3):
                can, elig = self._ucc_compensation_possible_for_stage(st)
                if can:
                    for m in elig:
                        if (not awarded[m.name]) and (not m.is_pass_fail) and (not m.is_non_compensatable):
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS by UCC compensation (stage {st}).")
        else:
            taught=[m for m in self.rec.modules if not m.is_cpm]
            failed_credits=0; has_outright=False; cand=[]
            for m in taught:
                st=self._module_status(m)
                if st["failed"]:
                    if is_outright_fail(rint(m.attempt1_mark or 0),"PCC",m.level,m.is_pass_fail): has_outright=True
                    if (not m.is_pass_fail) and (not m.is_non_compensatable): failed_credits+=m.credits; cand.append(m)
            if self.explain: self.X.add(f"PCC taught: failed_credits={failed_credits}, outright={has_outright}")
            if failed_credits<=40 and not has_outright:
                numer=denom=0
                for m in taught:
                    mk=self._best_attempt_mark_for_calc(m,"for_stage_average")
                    if mk is None: continue
                    numer += mk*m.credits; denom += m.credits
                stage_mean = rint(numer/denom) if denom else 0
                if self.explain: self.X.add(f"PCC taught mean={stage_mean}")
                if stage_mean>=50:
                    for m in cand:
                        if not awarded[m.name]:
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS by PCC compensation (taught).")

        # 3) Reassessment (within limits)
        if self.rec.program=="UCC":
            for st in (1,2,3):
                st_mods=[m for m in self.rec.modules if m.stage==st]
                failed_credits = sum(m.credits for m in st_mods if not awarded[m.name] and not m.is_pass_fail)
                limit = 80 if st==1 else 60
                if self.explain: self.X.add(f"UCC Stage {st} reassess limit: {failed_credits}<= {limit}?")
                if failed_credits<=limit:
                    for m in st_mods:
                        if not awarded[m.name] and m.took_reassessment and m.attempt2_mark is not None:
                            mk = self._best_attempt_mark_for_calc(m,"for_credit")
                            if mk is not None and mk >= pass_mark(m.level,"UCC"):
                                awarded[m.name]=True
                                if self.explain: self.X.add(f"{m.name}: PASS by reassessment (UCC).")
        else:
            taught=[m for m in self.rec.modules if not m.is_cpm]
            failed_credits = sum(m.credits for m in taught if not awarded[m.name] and not m.is_pass_fail)
            if self.explain: self.X.add(f"PCC taught reassess limit: {failed_credits}<=60?")
            if failed_credits<=60:
                for m in taught:
                    if not awarded[m.name] and m.took_reassessment and m.attempt2_mark is not None:
                        mk = self._best_attempt_mark_for_calc(m,"for_credit")
                        if mk is not None and mk >= pass_mark(m.level,"PCC"):
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS by reassessment (PCC).")
            # CPM special
            for m in self.rec.modules:
                if not m.is_cpm: continue
                m1,_=m.rounded_marks()
                if m1 is None: continue
                pm = pass_mark(m.level,"PCC")
                if is_marginal_fail(m1,"PCC",m.level):
                    if m.took_reassessment and m.attempt2_mark is not None:
                        mk = clamp_mark(m.level,"PCC", rint(m.attempt2_mark))
                        if mk >= pm:
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS CPM after capped reassessment.")

        # 4) Post-reassessment compensation pass
        if self.rec.program=="UCC":
            for st in (1,2,3):
                can, elig = self._ucc_compensation_possible_for_stage(st)
                if can:
                    for m in elig:
                        if (not awarded[m.name]) and (not m.is_pass_fail) and (not m.is_non_compensatable):
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS by UCC compensation AFTER reassessment.")
        else:
            taught=[m for m in self.rec.modules if not m.is_cpm]
            failed_credits=0; has_outright=False; cand=[]
            for m in taught:
                if not awarded[m.name]:
                    st=self._module_status(m)
                    if st["outright_fail"]: has_outright=True
                    if (not m.is_pass_fail) and (not m.is_non_compensatable):
                        failed_credits+=m.credits; cand.append(m)
            if (not has_outright) and failed_credits<=40:
                numer=denom=0
                for m in taught:
                    mk=self._best_attempt_mark_for_calc(m,"for_stage_average")
                    if mk is None: continue
                    numer += mk*m.credits; denom += m.credits
                taught_mean=rint(numer/denom) if denom else 0
                if taught_mean>=50:
                    for m in cand:
                        if not awarded[m.name]:
                            _ = self._best_attempt_mark_for_calc(m,"for_compensation")
                            awarded[m.name]=True
                            if self.explain: self.X.add(f"{m.name}: PASS by PCC compensation AFTER reassessment.")
        return awarded

    # ---------- PUBLIC ENTRY POINTS ----------

    def decide(self) -> Dict[str,str]:
        res={}
        awarded = self.award_credits_with_compensation_and_reassessment()
        if self.rec.program=="UCC":
            using_overrides_only = (
                not self.rec.modules and
                (self.rec.ucc_stage2_avg_override is not None or
                 self.rec.ucc_stage3_avg_override is not None or
                 self.rec.ucc_final_award_mark_override is not None or
                 self.rec.ucc_all_awarded_override is True)
            )
            if using_overrides_only:
                res["progression_stage1_to_2"] = "N/A (override mode)"
                res["progression_stage2_to_3"] = "N/A (override mode)"
            else:
                res["progression_stage1_to_2"] = "YES" if self._progression_ok(1,awarded) else "NO"
                res["progression_stage2_to_3"] = "YES" if self._progression_ok(2,awarded) else "NO"
            res.update(self._ucc_award_and_classification(awarded))
        else:
            res.update(self._pcc_award(awarded))
        if self.explain: res["explanation"]=self.X.flush()
        else: self.X.steps.clear()
        return res

    def _progression_ok(self, stage:int, awarded:Dict[str,bool])->bool:
        if stage not in (1,2): return True
        have = sum(m.credits for m in self.rec.modules if m.stage==stage and awarded.get(m.name,False))
        if self.explain: self.X.add(f"UCC Stage {stage} progression credits {have}/120")
        return have>=120

    # --- Extra helpers for the CLI ---
    def per_module_status_summary(self) -> List[str]:
        awarded = self.award_credits_with_compensation_and_reassessment()
        lines = []
        for m in self.rec.modules:
            code = (m.result_code or "").upper()
            pm = pass_mark(m.level, self.rec.program)
            m1, m2 = m.rounded_marks()

            # detect by-mark vs by-code
            by_code_pass = awarded.get(m.name, False) and code in PASS_CODES
            by_mark = (
                not by_code_pass and
                (m1 is not None) and
                clamp_mark(m.level, self.rec.program, (m2 if (m2 and m2 >= m1) else m1)) >= pm and
                (not m.is_pass_fail or m1 >= pm)
            )

            if awarded.get(m.name, False):
                if by_code_pass:
                    label = f"PASS (by code {code})"
                elif by_mark:
                    label = "PASS (by mark/reassessment)" if not (m2 and m2 >= pm and (m1 is None or m2 >= m1)) else "PASS (by reassessment)"
                else:
                    label = "PASS (by compensation)"
            else:
                if m1 is None:
                    label = "Not yet marked"
                    continue
                else:
                    label = "FAIL (marginal)" if is_marginal_fail(m1, self.rec.program, m.level) else (
                            "FAIL (outright)" if is_outright_fail(m1, self.rec.program, m.level, m.is_pass_fail) else "FAIL")
                if code in FAIL_CODES:
                    label = f"{label} (code {code})"
            lines.append(f"- {m.name}: {label}")
        return lines

    def ucc_classification_only(self) -> Tuple[bool, Dict[str,str]]:
        """Return (eligible, result_dict) for UCC classification only."""
        if self.rec.program != "UCC": return (False, {"error":"Classification applies to UCC (Bachelors). Use /merit for PCC."})
        awarded = self.award_credits_with_compensation_and_reassessment()
        st3_aw = sum(m.credits for m in self.rec.modules if m.stage==3 and awarded.get(m.name,False))
        if st3_aw < 120 and not self.rec.ucc_all_awarded_override:
            return (False, {"message": f"Not eligible yet: Stage 3 credits awarded {st3_aw}/120."})
        return (True, self._ucc_award_and_classification(awarded))

    def pcc_merit_only(self) -> Dict[str,str]:
        """Merit/Distinction prediction for PCC (if you want just that)."""
        if self.rec.program != "PCC":
            return {"error":"Merit/Distinction applies to PCC (Masters). Use /classification for UCC."}
        md = self._pcc_merit_distinction()
        return md or {"classification":"No merit/distinction (thresholds not met)"}

# -------------------------------
# SIMPLE CHAT LOOP (CLI)
# -------------------------------

HELP_TEXT = """
Commands:
  /help                    Show this help
  /mode quick              Decision-only answers
  /mode explain            Decision + why
  /reset                   Reset all data (programme kept)
  /summary                 List entered modules
  /status                  PASS/FAIL per module (with route)
  /classification          UCC honours classification (if eligible)
  /merit                   PCC merit/distinction check
  /decide                  Full decision (progression/award/classification)
  /set ucc avgs            Enter UCC Stage-2 & Stage-3 averages (+ all-credits flag)
  /set ucc final           Enter a single UCC final award mark (no boundary review)
  /set pcc summary         Enter PCC programme average (+ optional CPM mark)
  /set pcc proceed         Set PCC intent to proceed to CPM (yes/no)
  /show overrides          Show current override values
  /clear overrides         Clear all overrides
  /loadcsv <file>          Import modules from a CSV (see header expectations)
  /exit                    Quit

Flow:
  1) Pick programme: UCC (Undergraduate) or PCC (Masters)
  2) Add modules (press Enter on an empty prompt)
     OR set summary overrides with the /set commands above
  3) Optional: /status or /classification (UCC) or /merit (PCC)
  4) /decide for full outcome
"""

def prompt_bool(msg: str) -> bool:
    while True:
        x = input(msg + " [y/n]: ").strip().lower()
        if x in ("y","yes"): return True
        if x in ("n","no"):  return False
        print("Please answer y/n.")

def prompt_int(msg: str, minv: int=None, maxv: int=None, allow_blank=False) -> Optional[int]:
    while True:
        x = input(msg + ": ").strip()
        if allow_blank and x == "": return None
        try:
            v = int(x)
            if (minv is not None and v < minv) or (maxv is not None and v > maxv):
                print(f"Enter an integer between {minv} and {maxv}."); continue
            return v
        except:
            print("Enter a valid integer.")

def prompt_choice(msg: str, choices: List[str]) -> str:
    C=[c.upper() for c in choices]
    while True:
        x = input(msg + f" {choices}: ").strip().upper()
        if x in C: return x
        print(f"Choose one of {choices}.")

def prompt_mark_code() -> Optional[str]:
    # Allowed codes per your table
    LEGAL = {"P","PC","PR","F","FR","FD","R","S","SR","I","D","RA","NM","WD","T","V","AU",""}
    while True:
        x = input("Transcript result code (P/PC/PR/F/FR/FD/R/S/SR/I/D/RA/NM/WD/T/V/AU) — leave blank if none: ").strip().upper()
        if x in LEGAL:
            return x or None
        print("Please enter a valid code from the list (or leave blank).")

def input_module(program: str) -> Module:
    name = input("Module name: ").strip() or "Module"
    stage = prompt_int("Stage (1/2/3)", 1, 3)
    level = prompt_choice("Level", ["C","I","H","M"])
    credits = prompt_int("Credits", 1, 200)
    is_pass_fail = prompt_bool("Is this a pass/fail module?")
    is_noncomp = prompt_bool("Is this a non-compensatable module?")
    is_cpm = False
    if program == "PCC":
        is_cpm = prompt_bool("Is this the Capstone Project Module (CPM)?")
    a1 = prompt_int("First-attempt mark (0–100, rounded used). Leave blank if not yet available", 0, 100, allow_blank=True)
    took_re = prompt_bool("Did you take a reassessment for this module?")
    a2 = None
    if took_re:
        a2 = prompt_int("Reassessment mark (0–100)", 0, 100)
    code = prompt_mark_code()
    return Module(
        name=name, stage=stage, level=level, credits=credits,
        is_pass_fail=is_pass_fail, is_non_compensatable=is_noncomp, is_cpm=is_cpm,
        attempt1_mark=a1, took_reassessment=took_re, attempt2_mark=a2,result_code=code
    )

def main():
    print("Welcome to the University Regulations Rule Based Program!")
    last_csv_path: Path | None = None
    print("Pick a mode to start:")
    print("  1) Quick answers")
    print("  2) Explain my answers")
    mode = "quick"
    while True:
        x = input("Choose 1 or 2: ").strip()
        if x == "1": mode = "quick"; break
        if x == "2": mode = "explain"; break
        print("Please choose 1 or 2.")

    print("\nWhich programme do you need?")
    print("  UCC = Bachelors (Undergraduate CITY College)")
    print("  PCC = Masters (Postgraduate CITY College)")
    while True:
        prog = input("Type UCC or PCC: ").strip().upper()
        if prog in ("UCC","PCC"): break
        print("Please type UCC or PCC.")

    rec = StudentRecord(program=prog)
    print("\nType /help for commands. Press Enter on an empty line to add a module.")
    while True:
        cmd = input("\n> ").strip()
        if cmd == "/help":
            print(HELP_TEXT)

        elif cmd == "/mode quick":
            mode="quick"; print("Mode set to QUICK.")

        elif cmd == "/mode explain":
            mode="explain"; print("Mode set to EXPLAIN.")

        elif cmd == "/reset":
            rec = StudentRecord(program=prog); print("All data reset. (Programme kept)")

        elif cmd == "/summary":
            if not rec.modules:
                print("No modules yet.")
            else:
                print("Modules entered:")
                for i,m in enumerate(rec.modules,1):
                    m1,m2 = m.rounded_marks()
                    print(f"  {i}. {m.name} | Stage {m.stage} | Level {m.level} | {m.credits}cr"
                          f" | pass/fail={m.is_pass_fail} | non-comp={m.is_non_compensatable} | CPM={m.is_cpm}"
                          f" | attempt1={m1} | reassessed={m.took_reassessment} | attempt2={m2}")
            if rec.program=="PCC" and rec.pcc_wishes_to_proceed_to_cpm is not None:
                print(f"  PCC proceed to CPM? {rec.pcc_wishes_to_proceed_to_cpm}")

        elif cmd == "/status":
            if not rec.modules:
                print("No modules yet.")
            else:
                engine = RulesEngine(rec, explain=(mode=="explain"))
                for line in engine.per_module_status_summary():
                    print(line)

        elif cmd == "/classification":
            engine = RulesEngine(rec, explain=(mode=="explain"))
            ok, result = engine.ucc_classification_only()
            if not ok:
                msg = result.get("message") or result.get("error") or "Not eligible."
                print(msg)
            else:
                print("=== UCC Classification ===")
                for k,v in result.items():
                    print(f"{k}: {v}")

        elif cmd == "/merit":
            engine = RulesEngine(rec, explain=(mode=="explain"))
            result = engine.pcc_merit_only()
            print("=== PCC Merit/Distinction ===")
            for k,v in result.items():
                print(f"{k}: {v}")

        elif cmd == "/decide":
            if rec.program=="PCC" and rec.pcc_wishes_to_proceed_to_cpm is None:
                rec.pcc_wishes_to_proceed_to_cpm = prompt_bool("Do you wish to proceed to CPM?")
            engine = RulesEngine(rec, explain=(mode=="explain"))
            result = engine.decide()
            print("\n=== RESULT ===")
            for k,v in result.items():
                if k != "explanation": print(f"{k}: {v}")
            if "explanation" in result:
                print("\n--- Why (explanation) ---")
                print(result["explanation"])
            try:
                save_choice = input("\nSave this /decide output to JSON? [y/n]: ").strip().lower()
                if save_choice == "y":
                    payload = {
                        "_program": rec.program,
                        "_input_csv": (last_csv_path.name if last_csv_path else None),
                        **{k: v for k, v in result.items()}
                    }
                    # filename logic
                    if last_csv_path:
                        stem = last_csv_path.stem
                    else:
                        from datetime import datetime
                        stem = f"decision_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    out_path = REPORTS_DIR / f"{stem}.{rec.program}.decision.json"
                    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    print(f"✓ Saved JSON report → {out_path}")
            except Exception as e:
                print(f"[save error] {e}")

        elif cmd.startswith("/loadcsv"):
            parts = cmd.split(maxsplit=1)
            if len(parts) < 2:
                print("Usage: /loadcsv path/to/file.csv")
            else:
                path = parts[1].strip().strip('"').strip("'")
                count, warns = load_modules_from_csv(path, rec)
                last_csv_path = Path(path)
                print(f"Imported {count} module rows from {path}.")
                if warns:
                    print("Warnings:")
                    for w in warns:
                        print(" -", w)

        # ------ NEW: override commands ------
        elif cmd == "/set ucc avgs":
            if rec.program != "UCC":
                print("This shortcut is for UCC.")
            else:
                s2 = prompt_int("Enter Stage 2 average (0–100)", 0, 100)
                s3 = prompt_int("Enter Stage 3 average (0–100)", 0, 100)
                all_aw = prompt_bool("Have all Stage 3 credits been awarded?")
                rec.ucc_stage2_avg_override = s2
                rec.ucc_stage3_avg_override = s3
                rec.ucc_all_awarded_override = all_aw
                rec.ucc_final_award_mark_override = None  # avoid ambiguity
                print("UCC stage averages set.")

        elif cmd == "/set ucc final":
            if rec.program != "UCC":
                print("This shortcut is for UCC.")
            else:
                final_mark = prompt_int("Enter FINAL award mark (0–100)", 0, 100)
                rec.ucc_final_award_mark_override = final_mark
                # clear S2/S3 overrides to avoid ambiguity
                rec.ucc_stage2_avg_override = None
                rec.ucc_stage3_avg_override = None
                rec.ucc_all_awarded_override = None
                print("UCC final award mark set (boundary review not applied in this mode).")

        elif cmd == "/set pcc summary":
            if rec.program != "PCC":
                print("This shortcut is for PCC.")
            else:
                avg = prompt_int("Enter programme average (0–100)", 0, 100)
                cpm = prompt_int("Enter CPM mark (0–100) — leave blank if unknown", 0, 100, allow_blank=True)
                rec.pcc_programme_avg_override = avg
                rec.pcc_cpm_mark_override = cpm if cpm is not None else None
                print("PCC summary set.")

        elif cmd == "/set pcc proceed":
            if rec.program != "PCC":
                print("This applies to PCC only.")
            else:
                rec.pcc_wishes_to_proceed_to_cpm = prompt_bool("Do you wish to proceed to CPM?")
                print(f"PCC proceed to CPM: {rec.pcc_wishes_to_proceed_to_cpm}")

        elif cmd == "/show overrides":
            print("=== Overrides ===")
            print(f"UCC S2 avg: {rec.ucc_stage2_avg_override}")
            print(f"UCC S3 avg: {rec.ucc_stage3_avg_override}")
            print(f"UCC all-awarded: {rec.ucc_all_awarded_override}")
            print(f"UCC final award mark: {rec.ucc_final_award_mark_override}")
            print(f"PCC programme avg: {rec.pcc_programme_avg_override}")
            print(f"PCC CPM mark: {rec.pcc_cpm_mark_override}")
            print(f"PCC proceed to CPM: {rec.pcc_wishes_to_proceed_to_cpm}")

        elif cmd == "/clear overrides":
            rec.ucc_stage2_avg_override = None
            rec.ucc_stage3_avg_override = None
            rec.ucc_all_awarded_override = None
            rec.ucc_final_award_mark_override = None
            rec.pcc_programme_avg_override = None
            rec.pcc_cpm_mark_override = None
            print("All overrides cleared.")

        elif cmd == "/exit":
            print("Goodbye!"); sys.exit(0)

        else:
            if cmd == "":
                print("Adding a new module...")
                m = input_module(rec.program)
                rec.modules.append(m)
                print(f"Added: {m.name}")
            else:
                print("Unknown command. Type /help, or press Enter to add a module.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye!")