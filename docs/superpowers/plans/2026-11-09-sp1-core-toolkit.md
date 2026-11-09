# SP1: Core Credit Toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the repository skeleton and the deterministic, LLM-free credit modeling toolkit (`modelforge_core`) that every later sub-project calls: dataset adapters, profiling, splits, WoE binning, a JSON-scorecard logistic PD model, a LightGBM challenger, calibration, and the full validation suite ending in a `ValidationReport`.

**Architecture:** One uv workspace with a single package for now, `packages/core`. Every artifact the toolkit produces is a Pydantic model defined in `schemas.py`, so SP2 can store and hash them without knowing the math. Pure functions over pandas DataFrames; no I/O except the dataset downloader; no model serialization except JSON and LightGBM's native text format.

**Tech Stack:** Python 3.12 (uv), pandas, numpy, scipy, scikit-learn, LightGBM, Pydantic v2, httpx (downloader only), pytest, hypothesis, ruff, pyright, import-linter, pre-commit, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-09-modelforge-architecture-design.md`, sections 4.1, 6, 10.3, 17, 17.1.

## Global Constraints

- Python `>=3.12,<3.13`; `.python-version` is `3.12`.
- `modelforge_core` may not import `anthropic`, `ollama`, `openai`, `httpx`, `requests`, `langgraph`, `langchain*`, or `mcp`. Single exception: `modelforge_core.datasets.download` may import `httpx`. Enforced by import-linter.
- No `pickle`, `joblib.dump`, `cloudpickle`, or `dill` anywhere outside tests. Models serialize as JSON (scorecard) or LightGBM native text.
- Canonical dataset frame columns: `obligor_id` (str), `vintage` (int), `default_flag` (int, values 0/1), then features named `f_1`..`f_n` (float64). Nothing else.
- Scores frame columns, in order: `("obligor_id", "vintage", "default_flag", "split", "score", "pd")`.
- WoE convention: `woe = ln(dist_good / dist_bad)` with additive smoothing 0.5 per bin. Higher WoE means lower risk. Logistic coefficients on WoE features are therefore expected negative.
- Default thresholds (spec 17.1): AUC OOT pass `>= 0.70`, warn `>= 0.65`; KS `>= 0.25`; train minus OOT AUC gap pass `<= 0.05`, warn `<= 0.10`; PSI/CSI pass `< 0.10`, warn `< 0.25`, else fail; binomial alpha `0.05`; Hosmer-Lemeshow p `>= 0.05`; IV kept in `[0.02, 0.5]`; correlation pruning `|r| > 0.7`; champion within `0.03` AUC of challenger.
- Every function that uses randomness takes an explicit `seed: int`.
- Commit after every task; commit messages in conventional-commit style; end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

Inputs the spec implies but no task's tests would otherwise exercise. Each line's test is pinned to the owning task below.

1. **A feature with missing values in scoring data but none in training.** `transform_woe` must not produce NaN; it maps to WoE 0.0 (neutral). Test in Task 8.
2. **Values outside the training range at scoring time.** Edge bins are open-ended, so a new minimum or maximum lands in the first or last bin. Test in Task 8.
3. **A split containing a single class** (for example a tiny OOT with no defaults). Discrimination metrics skip that split; the validation report marks the OOT tests `not_applicable` with a message instead of crashing. Tests in Tasks 11 and 13.
4. **Heavy ties in predicted PD** so that fewer than 3 calibration grades form. Hosmer-Lemeshow degrees of freedom become non-positive; the p-value is NaN and the report's `cal.hl` test is `warn` with a message. Tests in Tasks 11 and 13.
5. **A constant feature** (Taiwan's `Net Income Flag` is all ones). Binning yields one bin with IV 0, leakage flags it as `constant`, selection drops it with reason `constant`. Tests in Tasks 6 and 8.

---

## File structure

```
pyproject.toml                       workspace root; ruff, pyright, pytest, import-linter config
.python-version
Makefile
LICENSE                              MIT, "2026 Aditya Parab"
README.md                            stub: what ModelForge is, how to run lint and tests
.pre-commit-config.yaml
.github/workflows/ci.yml
examples/core_pipeline.py            end-to-end core run on the Polish dataset
packages/core/pyproject.toml
packages/core/modelforge_core/
  __init__.py                        __version__ = "0.1.0"
  schemas.py                         every artifact model (Task 2)
  standards.py                       default constants and DEFAULT_THRESHOLDS (Task 2)
  datasets/__init__.py
  datasets/base.py                   adapter protocol, LoadedDataset, sanitize_columns, validate_canonical (Task 3)
  datasets/download.py               fetch with SHA-256 pin (Task 3)
  datasets/polish.py                 UCI Polish companies bankruptcy adapter (Task 4)
  datasets/taiwan.py                 UCI Taiwanese bankruptcy adapter (Task 5)
  datasets/registry.py               name -> adapter (Task 5)
  profiling.py                       profile (Task 6)
  leakage.py                         check_leakage (Task 6)
  psi.py                             psi, psi_from_shares, psi_band, compute_psi (Task 7)
  splits.py                          make_splits, apply_split (Task 7)
  binning.py                         fit_binning, assign_bins, transform_woe (Task 8)
  selection.py                       select_features (Task 8)
  logistic.py                        fit_woe_logistic (Task 9)
  scorecard.py                       predict_pd, predict_points, to_json, from_json (Task 9)
  scoring.py                         score_frame (Task 9)
  gbm.py                             fit_gbm, predict_gbm (Task 10)
  calibration.py                     fit_calibration, apply_calibration (Task 10)
  metrics.py                         auc, gini, ks, bootstrap_ci, discrimination_metrics (Task 11)
  calibration_tests.py               calibration_tests (Task 11)
  stability.py                       stability_tests (Task 12)
  sensitivity.py                     sensitivity_analysis (Task 12)
  benchmark.py                       benchmark (Task 12)
  thresholds.py                      evaluate_thresholds (Task 13)
packages/core/tests/
  conftest.py                        make_synthetic_credit_frame fixture, hypothesis profile
  fixtures/mini.arff                 6-row ARFF with one "?" (Task 4)
  fixtures/mini_taiwan.csv           8-row CSV with the real header shape (Task 5)
  test_<module>.py                   one test file per module
  test_smoke_polish.py               slow end-to-end on real data (Task 14)
  test_no_pickle.py                  import scan (Task 1)
```

---

### Task 1: Repository skeleton and tooling

**Files:**
- Create: `pyproject.toml`, `.python-version`, `Makefile`, `LICENSE`, `README.md`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- Create: `packages/core/pyproject.toml`, `packages/core/modelforge_core/__init__.py`
- Create: `packages/core/tests/__init__.py`, `packages/core/tests/test_no_pickle.py`, `packages/core/tests/test_version.py`
- Modify: `.gitignore` (add `out/`, `data/`, `uv.lock` stays tracked)

**Interfaces:**
- Produces: `make lint`, `make format`, `make test`, `make test-fast`; package `modelforge-core` importable as `modelforge_core`; pytest markers `slow`.

- [ ] **Step 1: Write the workspace root `pyproject.toml`**

Contents to pin:

```toml
[project]
name = "modelforge-workspace"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = ["modelforge-core"]

[tool.uv.workspace]
members = ["packages/*"]

[tool.uv.sources]
modelforge-core = { workspace = true }

[dependency-groups]
dev = ["pytest>=8.2", "hypothesis>=6.100", "ruff>=0.6", "pyright>=1.1.380", "import-linter>=2.0", "pre-commit>=3.7"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "N", "W"]

[tool.pyright]
include = ["packages"]
pythonVersion = "3.12"
typeCheckingMode = "standard"

[tool.pytest.ini_options]
testpaths = ["packages/core/tests"]
addopts = "-ra"
markers = ["slow: downloads real data or trains on it; run in CI, skip locally with -m 'not slow'"]

[tool.importlinter]
root_package = "modelforge_core"

[[tool.importlinter.contracts]]
name = "core is LLM-free and network-free"
type = "forbidden"
source_modules = ["modelforge_core"]
forbidden_modules = ["anthropic", "ollama", "openai", "httpx", "requests", "langgraph", "langchain", "langchain_core", "langchain_anthropic", "langchain_ollama", "langchain_openai", "mcp"]
ignore_imports = ["modelforge_core.datasets.download -> httpx"]
```

- [ ] **Step 2: Write `packages/core/pyproject.toml`**

Hatchling build; `name = "modelforge-core"`, `version = "0.1.0"`, `requires-python = ">=3.12,<3.13"`; dependencies: `pandas>=2.2`, `numpy>=1.26`, `scipy>=1.13`, `scikit-learn>=1.5`, `lightgbm>=4.3`, `pydantic>=2.7`, `pyarrow>=16`, `httpx>=0.27`. `[tool.hatch.build.targets.wheel] packages = ["modelforge_core"]`.

- [ ] **Step 3: Write `.python-version` (`3.12`), `LICENSE` (MIT, `Copyright (c) 2026 Aditya Parab`), `README.md` stub, `.pre-commit-config.yaml` (ruff lint and ruff format hooks from `astral-sh/ruff-pre-commit`, plus `trailing-whitespace` and `end-of-file-fixer`), and append `out/` to `.gitignore`**

README stub: title, one paragraph from spec section 1, a "Status: sub-project 1 in progress" line, and the three commands `make lint`, `make test`, `make test-fast`.

- [ ] **Step 4: Write the `Makefile`**

Targets, each a one-liner over `uv run`: `lint` (ruff check, ruff format --check, pyright, lint-imports), `format` (ruff format, ruff check --fix), `test` (pytest), `test-fast` (pytest -m "not slow"), `sync` (uv sync --all-groups). Declare all as `.PHONY`.

- [ ] **Step 5: Write `packages/core/modelforge_core/__init__.py` with `__version__ = "0.1.0"` and the two tests**

```python
# tests/test_version.py
def test_version() -> None:
    import modelforge_core
    assert modelforge_core.__version__ == "0.1.0"
```

```python
# tests/test_no_pickle.py
import pathlib, re

FORBIDDEN = re.compile(r"^\s*(import pickle|from pickle|import cloudpickle|import dill|from joblib import dump|joblib\.dump)", re.M)

def test_no_pickle_in_package() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "modelforge_core"
    offenders = [p for p in root.rglob("*.py") if FORBIDDEN.search(p.read_text())]
    assert offenders == []
```

- [ ] **Step 6: Install and verify the toolchain**

Run: `uv sync --all-groups && uv run python -c "import lightgbm, sklearn, pandas; print('ok')"`
Expected: `ok`. If LightGBM fails to import on macOS with a `libomp` error, run `brew install libomp` and retry; record the requirement in the README under a "macOS note".

- [ ] **Step 7: Run lint and tests**

Run: `make lint && make test`
Expected: ruff, pyright and import-linter report no issues; 2 tests pass.

- [ ] **Step 8: Write `.github/workflows/ci.yml`**

Triggers: `pull_request` and `push` to `main`. One job `lint-test` on `ubuntu-latest`: checkout; `astral-sh/setup-uv@v5` with `enable-cache: true`; `uv python install 3.12`; `uv sync --all-groups`; `actions/cache@v4` on path `data/` with key `uci-datasets-v1`; `make lint`; `make test`.

- [ ] **Step 9: Install pre-commit hooks and commit**

Run: `uv run pre-commit install`

```bash
git add -A
git commit -m "chore: uv workspace skeleton, tooling, CI, core package stub"
```

---

### Task 2: Artifact schemas and standards

**Files:**
- Create: `packages/core/modelforge_core/schemas.py`, `packages/core/modelforge_core/standards.py`
- Test: `packages/core/tests/test_schemas.py`

**Interfaces:**
- Produces (all Pydantic v2 `BaseModel`, `model_config = ConfigDict(extra="forbid")`):
  - Type aliases: `Split = Literal["train", "val", "oot"]`, `Band = Literal["pass", "warn", "fail"]`, `Status = Literal["pass", "warn", "fail", "not_applicable"]`, `Trend = Literal["increasing", "decreasing", "none"]`.
  - Constants: `SCORES_COLUMNS: tuple[str, ...] = ("obligor_id", "vintage", "default_flag", "split", "score", "pd")`; `ID_COLUMNS: tuple[str, ...] = ("obligor_id", "vintage", "default_flag")`; `FEATURE_PATTERN = re.compile(r"^f_\d+$")`.
  - `ColumnEntry(name: str, original: str, description: str | None = None)`; `ColumnDictionary(dataset: str, columns: list[ColumnEntry])` with property `feature_names -> list[str]`.
  - `FeatureProfile(name, dtype: str, missing_share: float, n_unique: int, mean, std, min, p25, p50, p75, max: float | None)`; `ProfileArtifact(dataset: str, n_rows: int, n_features: int, target_rate: float, rows_by_vintage: dict[int, int], target_rate_by_vintage: dict[int, float], features: list[FeatureProfile])`.
  - `LeakageFlag(feature: str, kind: Literal["high_single_auc", "perfect_separation", "identifier_like", "constant"], value: float | None = None, detail: str)`; `LeakageReport(dataset: str, auc_threshold: float, duplicate_rows: int, flags: list[LeakageFlag])`.
  - `PSIEntry(feature: str, psi: float, band: Band)`; `PSIReport(base_vintages: list[int], compare_vintages: list[int], n_bins: int, entries: list[PSIEntry])`.
  - `SplitManifest(strategy: Literal["vintage", "stratified_random"], seed: int, oot_available: bool, oot_vintages: list[int], val_share: float, row_ids: dict[Split, list[str]], digests: dict[Split, str])` with method `ids(split: Split) -> list[str]` (empty list if absent).
  - `Bin(index: int, lower: float | None, upper: float | None, is_missing: bool, count: int, events: int, non_events: int, event_rate: float, woe: float, iv_contribution: float)`; `FeatureBinning(feature: str, method: Literal["quantile", "tree"], monotonic: bool, trend: Trend, iv: float, bins: list[Bin])`; `BinningTables(target: str, n_rows: int, smoothing: float, features: list[FeatureBinning])` with method `get(feature: str) -> FeatureBinning` (KeyError if absent).
  - `DroppedFeature(feature: str, reason: Literal["iv_below_min", "iv_above_max", "correlated", "constant"], detail: str)`; `FeatureSelection(iv_min: float, iv_max: float, corr_max: float, selected: list[str], dropped: list[DroppedFeature])`.
  - `PointsScaling(pdo: float = 20.0, base_score: float = 600.0, base_odds: float = 50.0)`; `ScorecardBin(lower: float | None, upper: float | None, is_missing: bool, woe: float, points: int)`; `ScorecardFeature(feature: str, coefficient: float, bins: list[ScorecardBin])`; `Scorecard(model_name: str, intercept: float, features: list[ScorecardFeature], scaling: PointsScaling, penalty: Literal["l2", "none"], c: float, n_rows: int, woe_convention: Literal["ln_good_over_bad"] = "ln_good_over_bad")`.
  - `GBMModel(model_name: str, features: list[str], params: dict[str, float | int | str | bool], num_trees: int, seed: int, model_text: str)`.
  - `CalibrationMap(method: Literal["platt", "isotonic", "none"], fitted_on: Split, platt_a: float | None = None, platt_b: float | None = None, isotonic_x: list[float] | None = None, isotonic_y: list[float] | None = None)`.
  - `SplitMetrics(split: Split, n: int, events: int, auc: float, auc_ci_low: float, auc_ci_high: float, gini: float, ks: float, ks_ci_low: float, ks_ci_high: float)`; `DiscriminationMetrics(model_name: str, score_col: str, bootstrap_samples: int, seed: int, by_split: list[SplitMetrics])` with `get(split) -> SplitMetrics | None`.
  - `GradeTest(grade: int, n: int, expected_events: float, observed_events: int, expected_pd: float, observed_rate: float, p_value: float, passed: bool)`; `CalibrationTests(split: Split, alpha: float, n_grades: int, grades: list[GradeTest], hl_statistic: float, hl_p_value: float, expected_total: float, observed_total: int, eo_ratio: float)`.
  - `StabilityTests(base_split: Split, compare_split: Split, n_bins: int, score_psi: float, score_psi_band: Band, feature_csi: list[PSIEntry])`.
  - `SignCheck(feature: str, coefficient: float, expected_sign: Literal["negative"], ok: bool)`; `MonotonicityCheck(feature: str, trend: Trend, monotone: bool)`; `BinShift(feature: str, shift: int, mean_abs_pd_delta: float, max_abs_pd_delta: float)`; `SensitivityResults(sign_checks: list[SignCheck], monotonicity: list[MonotonicityCheck], bin_shifts: list[BinShift], all_signs_ok: bool, all_monotone: bool)`.
  - `BenchmarkSplit(split: Split, champion_auc: float, challenger_auc: float, auc_delta: float, auc_delta_ci_low: float, auc_delta_ci_high: float, champion_ks: float, challenger_ks: float, ks_delta: float)`; `BenchmarkResults(champion: str, challenger: str, gap_threshold: float, decided_on: Split, by_split: list[BenchmarkSplit], champion_within_gap: bool)`.
  - `Thresholds(auc_min: float = 0.70, auc_warn: float = 0.65, ks_min: float = 0.25, gap_max: float = 0.05, gap_fail: float = 0.10, psi_warn: float = 0.10, psi_fail: float = 0.25, calibration_alpha: float = 0.05, grade_fail_share_warn: float = 0.20, hl_p_min: float = 0.05, eo_low: float = 0.80, eo_high: float = 1.25, iv_min: float = 0.02, iv_max: float = 0.50, corr_max: float = 0.70, benchmark_gap: float = 0.03)`.
  - `ValidationTest(test_id: str, name: str, status: Status, value: float | None, threshold: str, message: str, evidence: dict[str, Any] = {})`; `ValidationReport(model_name: str, thresholds: Thresholds, tests: list[ValidationTest], overall: Band)` with `get(test_id) -> ValidationTest` (KeyError if absent).
  - `TargetDefinition(event: str, horizon: str)`; `VintagePolicy(oot_vintages: list[int])`; `FeaturePolicy(exclusions: list[str] = [], max_features: int = 20, iv_min: float = 0.02, iv_max: float = 0.5, corr_max: float = 0.7)`; `Methodology(champion: Literal["woe_logistic"] = "woe_logistic", challenger: Literal["lightgbm"] = "lightgbm")`; `ModelingPlan(model_name: str, portfolio: str, target: TargetDefinition, vintage_policy: VintagePolicy, feature_policy: FeaturePolicy, methodology: Methodology, thresholds: Thresholds, regulatory_scope: list[str], intended_use: str, tier: Literal[1, 2, 3])`.
  - `standards.py`: `DEFAULT_THRESHOLDS = Thresholds()`, `DEFAULT_SEED = 42`, `DEFAULT_N_BINS = 10`, `DEFAULT_BOOTSTRAP_SAMPLES = 200`, `WOE_SMOOTHING = 0.5`, `DEFAULT_MAX_BINS = 10`, `DEFAULT_MIN_BIN_SHARE = 0.05`, `LEAKAGE_AUC_THRESHOLD = 0.9`, `PSI_EPS = 1e-6`, `DEFAULT_N_GRADES = 10`.

- [ ] **Step 1: Write the failing tests**

```python
def test_thresholds_defaults_match_spec() -> None:
    t = Thresholds()
    assert (t.auc_min, t.auc_warn, t.ks_min, t.gap_max) == (0.70, 0.65, 0.25, 0.05)
    assert (t.psi_warn, t.psi_fail, t.iv_min, t.iv_max, t.corr_max, t.benchmark_gap) == (0.10, 0.25, 0.02, 0.5, 0.7, 0.03)

def test_scorecard_round_trips_json() -> None:
    sc = Scorecard(model_name="m", intercept=-2.5, penalty="l2", c=1.0, n_rows=10, scaling=PointsScaling(),
                   features=[ScorecardFeature(feature="f_1", coefficient=-0.8,
                       bins=[ScorecardBin(lower=None, upper=1.5, is_missing=False, woe=0.3, points=31),
                             ScorecardBin(lower=1.5, upper=None, is_missing=False, woe=-0.4, points=22)])])
    assert Scorecard.model_validate_json(sc.model_dump_json()) == sc

def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PointsScaling(pdo=20, bogus=1)  # type: ignore[call-arg]

def test_validation_report_get_raises_on_unknown_id() -> None:
    report = ValidationReport(model_name="m", thresholds=Thresholds(), tests=[], overall="pass")
    with pytest.raises(KeyError):
        report.get("nope")

def test_split_manifest_ids_empty_when_absent() -> None:
    m = SplitManifest(strategy="stratified_random", seed=1, oot_available=False, oot_vintages=[], val_share=0.2,
                      row_ids={"train": ["a"], "val": ["b"]}, digests={"train": "x", "val": "y"})
    assert m.ids("oot") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_schemas.py -v`
Expected: FAIL with `ImportError` (module does not exist).

- [ ] **Step 3: Implement `schemas.py` and `standards.py` exactly as listed in Interfaces**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_schemas.py -v && make lint`
Expected: 5 passed; lint clean.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): artifact schemas and default standards"
```

---

### Task 3: Canonical dataset schema, column sanitisation, checksummed download

**Files:**
- Create: `packages/core/modelforge_core/datasets/__init__.py`, `datasets/base.py`, `datasets/download.py`
- Test: `packages/core/tests/test_datasets_base.py`, `packages/core/tests/test_download.py`

**Interfaces:**
- Produces:
  - `base.py`: `class CanonicalSchemaError(ValueError)`; `@dataclass(frozen=True) class LoadedDataset: name: str; frame: pd.DataFrame; dictionary: ColumnDictionary; oot_capable: bool`; `class DatasetAdapter(Protocol): name: str; def load(self, cache_dir: Path) -> LoadedDataset`; `def sanitize_columns(frame: pd.DataFrame, feature_columns: Sequence[str], *, dataset: str, descriptions: Mapping[str, str] | None = None) -> tuple[pd.DataFrame, ColumnDictionary]` (renames `feature_columns[i]` to `f_{i+1}`, casts them to float64, keeps the ID columns, drops everything else, orders columns ID then features); `def validate_canonical(frame: pd.DataFrame) -> None`.
  - `download.py`: `class ChecksumMismatchError(RuntimeError)` with attributes `expected`, `actual`; `def sha256_file(path: Path) -> str`; `def fetch(url: str, sha256: str, dest: Path, *, timeout: float = 120.0) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
def test_sanitize_renames_in_order_and_builds_dictionary() -> None:
    raw = pd.DataFrame({"obligor_id": ["a", "b"], "vintage": [1, 1], "default_flag": [0, 1],
                        "Attr2": [2.0, 3.0], "Attr1": [1, 2], "junk": ["x", "y"]})
    frame, dictionary = sanitize_columns(raw, ["Attr1", "Attr2"], dataset="d", descriptions={"Attr1": "net profit / total assets"})
    assert list(frame.columns) == ["obligor_id", "vintage", "default_flag", "f_1", "f_2"]
    assert frame["f_1"].dtype == "float64"
    assert dictionary.columns[0] == ColumnEntry(name="f_1", original="Attr1", description="net profit / total assets")
    assert dictionary.columns[1].description is None
    assert dictionary.feature_names == ["f_1", "f_2"]

def test_validate_canonical_rejects_bad_default_flag() -> None:
    frame = pd.DataFrame({"obligor_id": ["a"], "vintage": [1], "default_flag": [2], "f_1": [0.1]})
    with pytest.raises(CanonicalSchemaError):
        validate_canonical(frame)

def test_validate_canonical_rejects_unnamed_feature() -> None:
    frame = pd.DataFrame({"obligor_id": ["a"], "vintage": [1], "default_flag": [0], "income": [0.1]})
    with pytest.raises(CanonicalSchemaError):
        validate_canonical(frame)

def test_validate_canonical_accepts_good_frame() -> None:
    frame = pd.DataFrame({"obligor_id": ["a"], "vintage": [1], "default_flag": [0], "f_1": [0.1], "f_2": [np.nan]})
    validate_canonical(frame)
```

```python
def test_fetch_returns_cached_file_when_hash_matches(tmp_path: Path) -> None:
    dest = tmp_path / "x.bin"; dest.write_bytes(b"hello")
    expected = hashlib.sha256(b"hello").hexdigest()
    assert fetch("http://unused.invalid/x", expected, dest) == dest

def _fake_stream(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    from modelforge_core.datasets import download
    monkeypatch.setattr(download, "_stream_to", lambda url, tmp_path, timeout: tmp_path.write_bytes(payload))

def test_fetch_downloads_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stream(monkeypatch, b"payload")
    out = fetch("http://x.invalid/p", hashlib.sha256(b"payload").hexdigest(), tmp_path / "p")
    assert out.read_bytes() == b"payload" and not (tmp_path / "p.part").exists()

def test_fetch_rejects_bad_hash_and_leaves_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_stream(monkeypatch, b"payload")
    with pytest.raises(ChecksumMismatchError) as exc:
        fetch("http://x.invalid/p", "00" * 32, tmp_path / "p")
    assert exc.value.expected == "00" * 32 and exc.value.actual == hashlib.sha256(b"payload").hexdigest()
    assert not (tmp_path / "p").exists() and not (tmp_path / "p.part").exists()

def test_fetch_redownloads_when_cached_file_is_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = tmp_path / "p"; dest.write_bytes(b"truncated")
    _fake_stream(monkeypatch, b"payload")
    assert fetch("http://x.invalid/p", hashlib.sha256(b"payload").hexdigest(), dest).read_bytes() == b"payload"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_datasets_base.py packages/core/tests/test_download.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `base.py` and `download.py`**

`download.py` splits the network call into `_stream_to(url: str, tmp_path: Path, timeout: float) -> None` (httpx streaming GET, `raise_for_status`) so tests can monkeypatch it. `fetch` writes to `dest.with_suffix(dest.suffix + ".part")`, hashes, renames on success, deletes on mismatch.

`validate_canonical` rules, each a `CanonicalSchemaError` with a specific message: missing ID column; `default_flag` values outside `{0, 1}`; any non-ID column not matching `FEATURE_PATTERN`; any feature column not numeric; duplicate `obligor_id`.

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_datasets_base.py packages/core/tests/test_download.py -v && make lint`
Expected: 8 passed; `lint-imports` still passes (httpx only in `download.py`).

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): canonical dataset schema, column sanitisation, checksummed download"
```

---

### Task 4: Polish companies bankruptcy adapter

**Files:**
- Create: `packages/core/modelforge_core/datasets/polish.py`, `packages/core/tests/fixtures/mini.arff`
- Test: `packages/core/tests/test_polish.py`

**Interfaces:**
- Consumes: `fetch`, `sanitize_columns`, `validate_canonical`, `LoadedDataset`.
- Produces: `POLISH_URL = "https://archive.ics.uci.edu/static/public/365/polish+companies+bankruptcy+data.zip"`; `POLISH_SHA256 = "17377929aa0b204bbf957e56462cf827c19fe4e2ce89f27dfbc77f9ea2bb16c9"`; `POLISH_FILES = ("1year.arff", "2year.arff", "3year.arff", "4year.arff", "5year.arff")`; `POLISH_ATTRIBUTE_DESCRIPTIONS: tuple[str, ...]` (64 entries, below); `def parse_arff(path: Path) -> pd.DataFrame` (columns `Attr1`..`Attr64` as float64 with `?` as NaN, `class` as int); `class PolishBankruptcyAdapter: name = "polish_bankruptcy"; def load(self, cache_dir: Path) -> LoadedDataset`.

Dataset facts to encode in tests: archive is 8,834,471 bytes; files have attributes `@attribute Attr1 numeric` .. `Attr64`, then `@attribute class {0,1}`; missing marker `?`. Row counts per file 7027, 10173, 10503, 9792, 5910 (total 43,405); events 271, 400, 495, 515, 410 (total 2,091). File `k`year maps to `vintage = k`. `obligor_id = f"p{vintage}_{row_index:05d}"` with `row_index` the 0-based row within its file. `oot_capable = True`. Descriptions: `Attr{i}` gets `POLISH_ATTRIBUTE_DESCRIPTIONS[i-1]`.

The 64 descriptions, in order (source: UCI dataset page, CC BY 4.0):

```
net profit / total assets
total liabilities / total assets
working capital / total assets
current assets / short-term liabilities
[(cash + short-term securities + receivables - short-term liabilities) / (operating expenses - depreciation)] * 365
retained earnings / total assets
EBIT / total assets
book value of equity / total liabilities
sales / total assets
equity / total assets
(gross profit + extraordinary items + financial expenses) / total assets
gross profit / short-term liabilities
(gross profit + depreciation) / sales
(gross profit + interest) / total assets
(total liabilities * 365) / (gross profit + depreciation)
(gross profit + depreciation) / total liabilities
total assets / total liabilities
gross profit / total assets
gross profit / sales
(inventory * 365) / sales
sales (n) / sales (n-1)
profit on operating activities / total assets
net profit / sales
gross profit (in 3 years) / total assets
(equity - share capital) / total assets
(net profit + depreciation) / total liabilities
profit on operating activities / financial expenses
working capital / fixed assets
logarithm of total assets
(total liabilities - cash) / sales
(gross profit + interest) / sales
(current liabilities * 365) / cost of products sold
operating expenses / short-term liabilities
operating expenses / total liabilities
profit on sales / total assets
total sales / total assets
(current assets - inventories) / long-term liabilities
constant capital / total assets
profit on sales / sales
(current assets - inventory - receivables) / short-term liabilities
total liabilities / ((profit on operating activities + depreciation) * (12/365))
profit on operating activities / sales
rotation receivables + inventory turnover in days
(receivables * 365) / sales
net profit / inventory
(current assets - inventory) / short-term liabilities
(inventory * 365) / cost of products sold
EBITDA (profit on operating activities - depreciation) / total assets
EBITDA (profit on operating activities - depreciation) / sales
current assets / total liabilities
short-term liabilities / total assets
(short-term liabilities * 365) / cost of products sold
equity / fixed assets
constant capital / fixed assets
working capital
(sales - cost of products sold) / sales
(current assets - inventory - short-term liabilities) / (sales - gross profit - depreciation)
total costs / total sales
long-term liabilities / equity
sales / inventory
sales / receivables
(short-term liabilities * 365) / sales
sales / short-term liabilities
sales / fixed assets
```

- [ ] **Step 1: Write `tests/fixtures/mini.arff`**

A valid ARFF with `@relation mini`, attributes `Attr1 numeric`, `Attr2 numeric`, `Attr3 numeric`, `class {0,1}`, and six data rows, one containing `?` in `Attr2`, with three rows of class 1.

- [ ] **Step 2: Write the failing tests**

```python
def test_descriptions_has_64_entries_and_known_endpoints() -> None:
    assert len(POLISH_ATTRIBUTE_DESCRIPTIONS) == 64
    assert POLISH_ATTRIBUTE_DESCRIPTIONS[0] == "net profit / total assets"
    assert POLISH_ATTRIBUTE_DESCRIPTIONS[63] == "sales / fixed assets"

def test_parse_arff_reads_fixture_with_missing(fixtures: Path) -> None:
    frame = parse_arff(fixtures / "mini.arff")
    assert list(frame.columns) == ["Attr1", "Attr2", "Attr3", "class"]
    assert frame.shape == (6, 4)
    assert frame["Attr2"].isna().sum() == 1
    assert frame["class"].dtype.kind == "i" and frame["class"].sum() == 3

@pytest.mark.slow
def test_polish_load_matches_published_counts(data_dir: Path) -> None:
    ds = PolishBankruptcyAdapter().load(data_dir)
    validate_canonical(ds.frame)
    assert ds.oot_capable is True
    assert len(ds.frame) == 43405
    assert int(ds.frame["default_flag"].sum()) == 2091
    assert ds.frame.groupby("vintage").size().to_dict() == {1: 7027, 2: 10173, 3: 10503, 4: 9792, 5: 5910}
    assert ds.dictionary.feature_names == [f"f_{i}" for i in range(1, 65)]
    assert ds.dictionary.columns[0].original == "Attr1"
    assert ds.frame["obligor_id"].iloc[0] == "p1_00000"
```

`conftest.py` gains two fixtures: `fixtures` (path to `tests/fixtures`) and `data_dir` (`Path(os.environ.get("MODELFORGE_DATA_DIR", "data"))`, created if missing).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_polish.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `polish.py`**

`parse_arff` uses `scipy.io.arff.loadarff`; nominal `class` arrives as bytes and is decoded then cast to int. `load` skips both fetch and extraction when all five ARFF files already exist under `cache_dir / "polish"`; otherwise it fetches the zip to `cache_dir / "polish" / "polish.zip"` and extracts the five files there. It then parses each, adds `vintage` and `obligor_id`, concatenates, then `sanitize_columns` with `feature_columns=[f"Attr{i}" for i in range(1, 65)]` and the descriptions map, and `validate_canonical` before returning.

- [ ] **Step 5: Run tests to verify they pass (the slow one downloads 8.8 MB once into `data/`), then lint**

Run: `uv run pytest packages/core/tests/test_polish.py -v && make lint`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/core
git commit -m "feat(core): UCI Polish companies bankruptcy adapter"
```

---

### Task 5: Taiwanese bankruptcy adapter and registry

**Files:**
- Create: `packages/core/modelforge_core/datasets/taiwan.py`, `datasets/registry.py`, `packages/core/tests/fixtures/mini_taiwan.csv`
- Test: `packages/core/tests/test_taiwan.py`, `packages/core/tests/test_registry.py`

**Interfaces:**
- Produces: `TAIWAN_URL = "https://archive.ics.uci.edu/static/public/572/taiwanese+bankruptcy+prediction.zip"`; `TAIWAN_SHA256 = "c346f5ad2618cb198e7ed8306cf2f31fe3bb2ec60acdbbe1736788d50f269aac"`; `TAIWAN_FILE = "data.csv"`; `def assign_pseudo_vintages(default_flag: np.ndarray, *, n_vintages: int, seed: int) -> np.ndarray`; `class TaiwanBankruptcyAdapter: name = "taiwan_bankruptcy"; def __init__(self, *, seed: int = 42, n_pseudo_vintages: int = 5); def load(self, cache_dir: Path) -> LoadedDataset`.
- `registry.py`: `ADAPTERS: dict[str, Callable[[], DatasetAdapter]]` with keys `"polish_bankruptcy"`, `"taiwan_bankruptcy"`; `def get_adapter(name: str) -> DatasetAdapter` (KeyError listing known names); `def load_dataset(name: str, cache_dir: Path) -> LoadedDataset`.

Dataset facts: CSV has 96 columns; first is `Bankrupt?` (target); the other 95 have a leading space in the header (strip them); 6,819 rows; 220 events; column 95 (`Net Income Flag`) is constant 1. `obligor_id = f"t_{row_index:05d}"`. Pseudo-vintage: within each class, a seeded permutation assigns rows round-robin to vintages `1..n`, so class balance per vintage is even. `oot_capable = False`. Description for `f_i` is the stripped original header.

- [ ] **Step 1: Write `tests/fixtures/mini_taiwan.csv`**

Header `Bankrupt?, ROA(C) before interest and depreciation before interest, Operating Gross Margin, Net Income Flag` (note the leading spaces), eight rows, two with `Bankrupt?` = 1, `Net Income Flag` always 1.

- [ ] **Step 2: Write the failing tests**

```python
def test_pseudo_vintages_are_balanced_and_deterministic() -> None:
    y = np.array([0] * 90 + [1] * 10)
    v1 = assign_pseudo_vintages(y, n_vintages=5, seed=7)
    v2 = assign_pseudo_vintages(y, n_vintages=5, seed=7)
    assert np.array_equal(v1, v2)
    assert set(v1) == {1, 2, 3, 4, 5}
    assert all((v1[y == 1] == k).sum() == 2 for k in range(1, 6))

def test_taiwan_fixture_parses(fixtures: Path, tmp_path: Path) -> None:
    (tmp_path / "taiwan").mkdir()
    shutil.copy(fixtures / "mini_taiwan.csv", tmp_path / "taiwan" / "data.csv")   # cached file: no network
    ds = TaiwanBankruptcyAdapter(seed=1, n_pseudo_vintages=2).load(tmp_path)
    validate_canonical(ds.frame)
    assert ds.oot_capable is False
    assert ds.dictionary.feature_names == ["f_1", "f_2", "f_3"]
    assert ds.dictionary.columns[2] == ColumnEntry(name="f_3", original="Net Income Flag", description="Net Income Flag")
    assert ds.frame["f_3"].nunique() == 1

@pytest.mark.slow
def test_taiwan_load_matches_published_counts(data_dir: Path) -> None:
    ds = TaiwanBankruptcyAdapter().load(data_dir)
    assert len(ds.frame) == 6819
    assert int(ds.frame["default_flag"].sum()) == 220
    assert len(ds.dictionary.feature_names) == 95
    assert ds.frame["obligor_id"].iloc[0] == "t_00000"

def test_registry_resolves_and_rejects() -> None:
    assert get_adapter("polish_bankruptcy").name == "polish_bankruptcy"
    with pytest.raises(KeyError):
        get_adapter("nope")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_taiwan.py packages/core/tests/test_registry.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement `taiwan.py` and `registry.py`**

`load` skips fetch and extraction when `cache_dir / "taiwan" / "data.csv"` already exists; otherwise it fetches the zip to `cache_dir / "taiwan" / "taiwan.zip"` and extracts `data.csv` there. Read with `pd.read_csv`, strip whitespace from every column name, take `Bankrupt?` as `default_flag`, and pass the remaining columns in file order to `sanitize_columns` with each description equal to the stripped original name.

- [ ] **Step 5: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_taiwan.py packages/core/tests/test_registry.py -v && make lint`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/core
git commit -m "feat(core): Taiwanese bankruptcy adapter and dataset registry"
```

---

### Task 6: Profiling and leakage checks

**Files:**
- Create: `packages/core/modelforge_core/profiling.py`, `packages/core/modelforge_core/leakage.py`
- Modify: `packages/core/modelforge_core/datasets/base.py` (add `feature_columns`)
- Modify: `packages/core/tests/conftest.py` (add `make_synthetic_credit_frame`)
- Test: `packages/core/tests/test_profiling.py`, `packages/core/tests/test_leakage.py`

**Interfaces:**
- Produces: `def profile(frame: pd.DataFrame, *, dataset: str) -> ProfileArtifact`; `def check_leakage(frame: pd.DataFrame, *, dataset: str, auc_threshold: float = LEAKAGE_AUC_THRESHOLD) -> LeakageReport`; `def feature_columns(frame: pd.DataFrame) -> list[str]` (in `datasets/base.py`, returns columns matching `FEATURE_PATTERN` in frame order).
- `conftest.py`: `def make_synthetic_credit_frame(n: int = 4000, *, seed: int = 0, n_features: int = 6, vintages: tuple[int, ...] = (1, 2, 3, 4, 5), missing_share: float = 0.05) -> pd.DataFrame` exposed as fixture `synthetic` (n=4000, seed=0). Generation: `f_1..f_n ~ N(0,1)`; `logit = -2.6 + 0.8*f_1 - 0.6*f_2 + 0.4*f_3`; `default_flag ~ Bernoulli(sigmoid(logit))`; `vintage` uniform over `vintages`; `obligor_id = f"s_{i:05d}"`; 5% of `f_1` set to NaN. Expected default rate about 8 to 10 percent, AUC of the true logit about 0.75.

- [ ] **Step 1: Write the failing tests**

```python
def test_profile_counts_and_missingness(synthetic: pd.DataFrame) -> None:
    p = profile(synthetic, dataset="synthetic")
    assert p.n_rows == 4000 and p.n_features == 6
    assert sum(p.rows_by_vintage.values()) == 4000
    assert 0.05 < p.target_rate < 0.15
    f1 = next(f for f in p.features if f.name == "f_1")
    assert 0.04 < f1.missing_share < 0.06
    assert all(abs(p.target_rate_by_vintage[v] - p.target_rate) < 0.05 for v in p.rows_by_vintage)

def test_leakage_flags_target_copy_constant_and_identifier(synthetic: pd.DataFrame) -> None:
    frame = synthetic.copy()
    frame["f_7"] = frame["default_flag"].astype(float)          # perfect separation
    frame["f_8"] = 1.0                                           # constant
    frame["f_9"] = np.arange(len(frame), dtype=float)            # identifier-like
    report = check_leakage(frame, dataset="synthetic")
    kinds = {(f.feature, f.kind) for f in report.flags}
    assert ("f_7", "perfect_separation") in kinds
    assert ("f_8", "constant") in kinds
    assert ("f_9", "identifier_like") in kinds
    assert not any(f.feature in {"f_1", "f_2", "f_3"} for f in report.flags)
    assert report.duplicate_rows == 0

def test_leakage_high_auc_flag_threshold(synthetic: pd.DataFrame) -> None:
    frame = synthetic.copy()
    noise = np.random.default_rng(1).normal(scale=0.05, size=len(frame))
    frame["f_7"] = frame["default_flag"] + noise
    report = check_leakage(frame, dataset="synthetic", auc_threshold=0.9)
    assert any(f.feature == "f_7" and f.kind == "high_single_auc" and f.value is not None and f.value > 0.9 for f in report.flags)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_profiling.py packages/core/tests/test_leakage.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `profile`, `check_leakage`, `feature_columns`**

Leakage rules per feature, evaluated on non-missing rows: `constant` if `n_unique <= 1`; otherwise `single_auc = max(auc, 1 - auc)` using `sklearn.metrics.roc_auc_score` (skip the AUC rules when fewer than 2 classes remain); `perfect_separation` if `single_auc >= 1 - 1e-12`, else `high_single_auc` if `single_auc > auc_threshold`; `identifier_like` if `n_unique == n_non_missing` and every value is integral. `duplicate_rows = int(frame.duplicated(subset=feature_columns(frame)).sum())`.

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_profiling.py packages/core/tests/test_leakage.py -v && make lint`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): dataset profiling and leakage checks"
```

---

### Task 7: Population stability and splits

**Files:**
- Create: `packages/core/modelforge_core/psi.py`, `packages/core/modelforge_core/splits.py`
- Test: `packages/core/tests/test_psi.py`, `packages/core/tests/test_splits.py`

**Interfaces:**
- Produces:
  - `psi.py`: `def psi_from_shares(expected: np.ndarray, actual: np.ndarray, *, eps: float = PSI_EPS) -> float`; `def quantile_edges(values: np.ndarray, n_bins: int) -> np.ndarray` (interior edges, unique, from non-NaN values); `def bucket_shares(values: np.ndarray, edges: np.ndarray) -> np.ndarray` (shares over `len(edges) + 2` buckets: the `len(edges)+1` right-closed intervals then one NaN bucket); `def psi(expected: np.ndarray, actual: np.ndarray, *, n_bins: int = DEFAULT_N_BINS, edges: np.ndarray | None = None, eps: float = PSI_EPS) -> float`; `def psi_band(value: float, thresholds: Thresholds) -> Band`; `def compute_psi(frame: pd.DataFrame, *, base_vintages: Sequence[int], compare_vintages: Sequence[int], features: Sequence[str] | None = None, n_bins: int = DEFAULT_N_BINS, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> PSIReport`.
  - `splits.py`: `def make_splits(frame: pd.DataFrame, *, strategy: Literal["vintage", "stratified_random"], seed: int, oot_vintages: Sequence[int] = (), val_share: float = 0.2) -> SplitManifest`; `def apply_split(frame: pd.DataFrame, manifest: SplitManifest, split: Split) -> pd.DataFrame`; `def split_digest(ids: Sequence[str]) -> str` (`sha256` of `"\n".join(sorted(ids))`, hex).

- [ ] **Step 1: Write the failing tests**

```python
def test_psi_from_shares_golden() -> None:
    assert psi_from_shares(np.array([0.5, 0.5]), np.array([0.6, 0.4])) == pytest.approx(0.040547, abs=1e-6)
    assert psi_from_shares(np.array([0.5, 0.5]), np.array([0.5, 0.5])) == 0.0

def test_psi_zero_for_same_distribution_and_positive_for_shift() -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(size=20000)
    assert psi(base, rng.normal(size=20000)) < 0.01
    assert psi(base, rng.normal(loc=1.0, size=20000)) > 0.25

def test_psi_counts_missing_as_bucket() -> None:
    base = np.array([1.0, 2.0, 3.0, 4.0] * 100)
    shifted = np.array([1.0, 2.0, np.nan, np.nan] * 100)
    assert psi(base, shifted) > 0.1

def test_psi_band_thresholds() -> None:
    t = Thresholds()
    assert (psi_band(0.05, t), psi_band(0.10, t), psi_band(0.25, t)) == ("pass", "warn", "fail")

def test_compute_psi_report_shape(synthetic: pd.DataFrame) -> None:
    report = compute_psi(synthetic, base_vintages=[1, 2, 3, 4], compare_vintages=[5])
    assert [e.feature for e in report.entries] == [f"f_{i}" for i in range(1, 7)]
    assert all(e.band == "pass" for e in report.entries)
```

```python
def test_vintage_split_holds_out_oot_and_stratifies(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5], val_share=0.25)
    assert m.oot_available and m.oot_vintages == [5]
    oot = apply_split(synthetic, m, "oot")
    assert set(oot["vintage"]) == {5}
    train, val = apply_split(synthetic, m, "train"), apply_split(synthetic, m, "val")
    assert len(train) + len(val) + len(oot) == len(synthetic)
    assert abs(len(val) / (len(train) + len(val)) - 0.25) < 0.01
    assert abs(train["default_flag"].mean() - val["default_flag"].mean()) < 0.02
    assert set(train["obligor_id"]).isdisjoint(val["obligor_id"])

def test_splits_are_deterministic_and_digested(synthetic: pd.DataFrame) -> None:
    a = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    b = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    assert a == b
    assert a.digests["train"] == split_digest(a.row_ids["train"]) and len(a.digests["train"]) == 64
    assert make_splits(synthetic, strategy="vintage", seed=7, oot_vintages=[5]).row_ids["val"] != a.row_ids["val"]

def test_stratified_random_has_no_oot(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="stratified_random", seed=1)
    assert m.oot_available is False and m.ids("oot") == []

def test_vintage_split_requires_non_empty_oot(synthetic: pd.DataFrame) -> None:
    with pytest.raises(ValueError):
        make_splits(synthetic, strategy="vintage", seed=1, oot_vintages=[99])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_psi.py packages/core/tests/test_splits.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `psi.py` and `splits.py`**

PSI: `psi_from_shares` replaces zero shares with `eps` on both sides, then `sum((a - e) * ln(a / e))`. `psi` computes edges from `expected` when not given, buckets both arrays with `bucket_shares`, returns `psi_from_shares`. Stratified assignment: within each class, a `np.random.default_rng(seed)` permutation; the first `round(val_share * n_class)` go to `val`, the rest to `train`. The frame is sorted by `obligor_id` before permutation so results do not depend on input order.

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_psi.py packages/core/tests/test_splits.py -v && make lint`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): population stability index and seeded splits"
```

---

### Task 8: WoE binning and feature selection

**Files:**
- Create: `packages/core/modelforge_core/binning.py`, `packages/core/modelforge_core/selection.py`
- Test: `packages/core/tests/test_binning.py`, `packages/core/tests/test_selection.py`

**Interfaces:**
- Produces:
  - `binning.py`: `def fit_binning(frame: pd.DataFrame, features: Sequence[str], *, target: str = "default_flag", method: Literal["quantile", "tree"] = "quantile", max_bins: int = DEFAULT_MAX_BINS, min_bin_share: float = DEFAULT_MIN_BIN_SHARE, monotonic: bool = True, smoothing: float = WOE_SMOOTHING) -> BinningTables`; `def assign_bins(values: pd.Series | np.ndarray, binning: FeatureBinning) -> np.ndarray` (int bin index per row; the missing bin's index for NaN; `-1` for NaN when no missing bin exists); `def woe_lookup(binning: FeatureBinning) -> np.ndarray` (WoE per bin index); `def transform_woe(frame: pd.DataFrame, tables: BinningTables, features: Sequence[str] | None = None) -> pd.DataFrame` (copy with each feature replaced by its WoE; index `-1` maps to `0.0`).
  - `selection.py`: `def select_features(tables: BinningTables, woe_frame: pd.DataFrame, *, iv_min: float, iv_max: float, corr_max: float) -> FeatureSelection`.

Binning algorithm (the one place this plan writes an algorithm, because the tests do not determine it):

1. Take non-missing values `x` and labels `y` of the feature. Interior edges: `quantile` -> `np.unique(np.quantile(x, np.linspace(0, 1, max_bins + 1), method="higher"))` with every edge equal to `x.max()` removed. Edges are therefore actual data values; an edge equal to `x.min()` stays, because the right-closed bin `(-inf, min]` is non-empty. This gives a 0/1 feature two bins and a constant feature one. `tree` -> thresholds of `DecisionTreeClassifier(max_leaf_nodes=max_bins, min_samples_leaf=max(1, int(min_bin_share * len(x))), random_state=0)` fitted on `x.reshape(-1, 1)`, sorted.
2. Bins are right-closed intervals `(-inf, e1], (e1, e2], ..., (ek, +inf)`. A missing bin is appended (index last) only if the feature has any NaN in `frame`.
3. Small-bin merge: first, any non-missing bin with `count == 0` is merged into its right neighbour (left neighbour if it is the last bin), regardless of `min_bin_share`. Then, while any non-missing bin has `count < min_bin_share * n` and more than one non-missing bin remains, merge it with the adjacent non-missing bin whose event rate is closer.
4. Monotonic merge (when `monotonic=True`): trend direction is the sign of Spearman correlation between `x` and `y` (`increasing` if positive). While the sequence of non-missing bin event rates violates that direction and more than one non-missing bin remains, merge the first violating adjacent pair.
5. WoE with smoothing `s`: `good_i = non_events_i + s`, `bad_i = events_i + s`, `dist_good_i = good_i / sum(good)`, `dist_bad_i = bad_i / sum(bad)`, `woe_i = ln(dist_good_i / dist_bad_i)`, `iv_i = (dist_good_i - dist_bad_i) * woe_i`, `iv = sum(iv_i)` over all bins including the missing bin.
6. `trend` is `increasing` if event rates over non-missing bins are non-decreasing, `decreasing` if non-increasing, else `none`. A single non-missing bin has trend `none`.

Selection: drop `constant` when a feature has exactly one non-missing bin; then `iv_below_min` if `iv < iv_min`; `iv_above_max` if `iv > iv_max` (detail: "suspicious; possible leakage"); then greedy correlation pruning over the survivors ordered by IV descending, computing Pearson correlation on `woe_frame`; a feature is dropped as `correlated` when `|r| > corr_max` with any already-kept feature (detail names that feature and the r value).

- [ ] **Step 1: Write the failing tests**

```python
def test_woe_golden_two_bins() -> None:
    frame = pd.DataFrame({"f_1": [1, 1, 1, 1, 2, 2, 2, 2.0], "default_flag": [0, 0, 0, 1, 0, 1, 1, 1]})
    tables = fit_binning(frame, ["f_1"], max_bins=2, min_bin_share=0.0, monotonic=True)
    b = tables.get("f_1")
    assert len(b.bins) == 2 and b.trend == "increasing"
    assert b.bins[0].woe == pytest.approx(math.log(7 / 3), abs=1e-6)
    assert b.bins[1].woe == pytest.approx(-math.log(7 / 3), abs=1e-6)
    assert b.iv == pytest.approx(0.677838, abs=1e-5)
    assert (b.bins[0].count, b.bins[0].events, b.bins[1].events) == (4, 1, 3)

def test_missing_gets_its_own_bin_and_counts_sum(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    f1 = tables.get("f_1")
    assert f1.bins[-1].is_missing and f1.bins[-1].count == int(synthetic["f_1"].isna().sum())
    assert sum(b.count for b in f1.bins) == len(synthetic)
    assert not any(b.is_missing for b in tables.get("f_2").bins)

@given(seed=st.integers(0, 10_000))
@settings(max_examples=25, deadline=None)
def test_monotonic_binning_gives_monotone_event_rates(seed: int) -> None:
    frame = make_synthetic_credit_frame(2000, seed=seed, missing_share=0.0)
    for fb in fit_binning(frame, ["f_1", "f_2", "f_3"], monotonic=True).features:
        rates = [b.event_rate for b in fb.bins if not b.is_missing]
        assert rates == sorted(rates) or rates == sorted(rates, reverse=True)
        assert fb.trend in ("increasing", "decreasing")

def test_transform_woe_handles_unseen_missing_and_out_of_range(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_2"])            # f_2 has no missing in training
    scoring = pd.DataFrame({"f_2": [np.nan, 1e9, -1e9]})
    out = transform_woe(scoring, tables, ["f_2"])
    assert out["f_2"].iloc[0] == 0.0
    assert out["f_2"].iloc[1] == tables.get("f_2").bins[-1].woe
    assert out["f_2"].iloc[2] == tables.get("f_2").bins[0].woe
    assert not out["f_2"].isna().any()

def test_constant_feature_yields_single_bin_and_zero_iv(synthetic: pd.DataFrame) -> None:
    frame = synthetic.assign(f_7=1.0)
    fb = fit_binning(frame, ["f_7"]).get("f_7")
    assert len([b for b in fb.bins if not b.is_missing]) == 1 and fb.iv == pytest.approx(0.0, abs=1e-12) and fb.trend == "none"

def test_tree_method_runs_and_respects_max_bins(synthetic: pd.DataFrame) -> None:
    fb = fit_binning(synthetic, ["f_1"], method="tree", max_bins=4).get("f_1")
    assert 1 <= len([b for b in fb.bins if not b.is_missing]) <= 4
```

```python
def test_selection_drops_by_iv_constant_and_correlation(synthetic: pd.DataFrame) -> None:
    frame = synthetic.assign(f_7=1.0, f_8=synthetic["f_1"].fillna(0) + 1e-3, f_9=synthetic["default_flag"].astype(float))
    tables = fit_binning(frame, [f"f_{i}" for i in range(1, 10)])
    woe = transform_woe(frame, tables)
    sel = select_features(tables, woe, iv_min=0.02, iv_max=0.5, corr_max=0.7)
    reasons = {d.feature: d.reason for d in sel.dropped}
    assert reasons["f_7"] == "constant"
    assert reasons["f_9"] == "iv_above_max"
    # f_1 and f_8 are near-duplicates: exactly one survives, the other is dropped naming the survivor
    corr_dropped = [d for d in sel.dropped if d.reason == "correlated"]
    assert [d.feature for d in corr_dropped] in (["f_1"], ["f_8"])
    survivor = "f_8" if corr_dropped[0].feature == "f_1" else "f_1"
    assert survivor in sel.selected and survivor in corr_dropped[0].detail
    assert "f_2" in sel.selected
    assert all(reasons.get(f) == "iv_below_min" for f in ("f_4", "f_5", "f_6") if f not in sel.selected)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_binning.py packages/core/tests/test_selection.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `binning.py` and `selection.py` per the algorithm above**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_binning.py packages/core/tests/test_selection.py -v && make lint`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): WoE/IV binning with monotonic merging and feature selection"
```

---

### Task 9: WoE logistic regression to JSON scorecard, and scoring

**Files:**
- Create: `packages/core/modelforge_core/logistic.py`, `scorecard.py`, `scoring.py`
- Test: `packages/core/tests/test_logistic.py`, `packages/core/tests/test_scorecard.py`, `packages/core/tests/test_scoring.py`

**Interfaces:**
- Consumes: `BinningTables`, `transform_woe`, `assign_bins`, `SplitManifest`, `apply_split`.
- Produces:
  - `logistic.py`: `def fit_woe_logistic(frame: pd.DataFrame, tables: BinningTables, features: Sequence[str], *, model_name: str, target: str = "default_flag", penalty: Literal["l2", "none"] = "l2", c: float = 1.0, scaling: PointsScaling | None = None) -> Scorecard`. Uses `LogisticRegression(penalty="l2" or None, C=c, solver="lbfgs", max_iter=1000)` on `transform_woe(frame, tables, features)`.
  - `scorecard.py`: `def predict_pd(scorecard: Scorecard, frame: pd.DataFrame) -> np.ndarray`; `def predict_points(scorecard: Scorecard, frame: pd.DataFrame) -> np.ndarray` (int64 totals); `def to_json(scorecard: Scorecard) -> str`; `def from_json(text: str) -> Scorecard`; `def bin_index(values: pd.Series | np.ndarray, bins: Sequence[ScorecardBin]) -> np.ndarray` (same semantics as `assign_bins`, over scorecard bins).
  - `scoring.py`: `def score_frame(frame: pd.DataFrame, manifest: SplitManifest, predict: Callable[[pd.DataFrame], np.ndarray], *, calibration: CalibrationMap | None = None) -> pd.DataFrame` returning exactly `SCORES_COLUMNS`, one row per obligor present in the manifest, `score` = raw model probability, `pd` = calibrated probability (equals `score` when `calibration` is None). In Task 9 a non-None `calibration` raises `NotImplementedError("calibration arrives in Task 10")`; Task 10 replaces that branch with a call to `apply_calibration`.

Points formula (pinned; the signature does not determine it): `factor = pdo / ln(2)`, `offset = base_score - factor * ln(base_odds)`, `n = number of features`, `points_ij = round(-(coef_i * woe_ij + intercept / n) * factor + offset / n)`. Total points for an obligor is the sum over features of the points of the bin it falls in. Missing with no missing bin uses `woe = 0.0`.

- [ ] **Step 1: Write the failing tests**

```python
def test_fit_produces_negative_coefficients_and_bins(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    assert [f.feature for f in sc.features] == ["f_1", "f_2", "f_3"]
    assert all(f.coefficient < 0 for f in sc.features)
    assert len(sc.features[0].bins) == len(tables.get("f_1").bins)
    assert sc.n_rows == len(synthetic) and sc.penalty == "l2" and sc.c == 1.0

def test_scorecard_pd_matches_sklearn_probability(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    woe = transform_woe(synthetic, tables, ["f_1", "f_2"])
    logit = sc.intercept + sum(f.coefficient * woe[f.feature].to_numpy() for f in sc.features)
    expected = 1 / (1 + np.exp(-logit))
    np.testing.assert_allclose(predict_pd(sc, synthetic), expected, atol=1e-12)

def test_scorecard_json_round_trip_reproduces_pd(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    again = from_json(to_json(sc))
    assert again == sc
    np.testing.assert_allclose(predict_pd(again, synthetic), predict_pd(sc, synthetic), atol=0)

def test_points_scale_and_direction(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m", scaling=PointsScaling(pdo=20, base_score=600, base_odds=50))
    pts, pds = predict_points(sc, synthetic), predict_pd(sc, synthetic)
    assert pts.dtype.kind == "i"
    assert np.corrcoef(pts, pds)[0, 1] < -0.9
    # a pd equal to 1/51 (odds 1:50) should score about 600 points
    assert abs(float(np.interp(1 / 51, np.sort(pds), pts[np.argsort(pds)])) - 600) < 40

def test_score_frame_columns_and_splits(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2"], model_name="m")
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    scores = score_frame(synthetic, manifest, lambda f: predict_pd(sc, f))
    assert tuple(scores.columns) == SCORES_COLUMNS
    assert len(scores) == len(synthetic)
    assert set(scores["split"]) == {"train", "val", "oot"}
    assert np.array_equal(scores["score"].to_numpy(), scores["pd"].to_numpy())
    assert scores["pd"].between(0, 1).all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_logistic.py packages/core/tests/test_scorecard.py packages/core/tests/test_scoring.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `logistic.py`, `scorecard.py`, `scoring.py`**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_logistic.py packages/core/tests/test_scorecard.py packages/core/tests/test_scoring.py -v && make lint`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): WoE logistic PD model as JSON scorecard, points scaling, scoring frame"
```

---

### Task 10: LightGBM challenger model and calibration

**Files:**
- Create: `packages/core/modelforge_core/gbm.py`, `packages/core/modelforge_core/calibration.py`
- Modify: `packages/core/modelforge_core/scoring.py` (wire `apply_calibration`)
- Test: `packages/core/tests/test_gbm.py`, `packages/core/tests/test_calibration.py`

**Interfaces:**
- Produces:
  - `gbm.py`: `DEFAULT_GBM_PARAMS: dict[str, float | int | str | bool] = {"objective": "binary", "n_estimators": 300, "learning_rate": 0.05, "num_leaves": 31, "min_child_samples": 50, "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0, "n_jobs": 1, "deterministic": True, "force_row_wise": True, "verbose": -1}`; `def fit_gbm(frame: pd.DataFrame, features: Sequence[str], *, model_name: str, seed: int, target: str = "default_flag", params: Mapping[str, float | int | str | bool] | None = None, val_frame: pd.DataFrame | None = None, early_stopping_rounds: int = 30) -> GBMModel` (`LGBMClassifier(**params, random_state=seed)`; early stopping only when `val_frame` is given; `model_text = booster_.model_to_string()`; `num_trees = booster_.num_trees()`); `def predict_gbm(model: GBMModel, frame: pd.DataFrame) -> np.ndarray` via `lightgbm.Booster(model_str=model.model_text).predict(frame[model.features])`.
  - `calibration.py`: `def fit_calibration(scores: np.ndarray, y: np.ndarray, *, method: Literal["platt", "isotonic", "none"], fitted_on: Split) -> CalibrationMap`; `def apply_calibration(cal: CalibrationMap, scores: np.ndarray) -> np.ndarray`. Platt: `LogisticRegression(penalty=None, solver="lbfgs", max_iter=1000)` on `logit(clip(scores, 1e-6, 1 - 1e-6))`; store `platt_a = coef_[0, 0]`, `platt_b = intercept_[0]`; apply `sigmoid(a * logit(s) + b)`. Isotonic: `IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)`; store `X_thresholds_` and `y_thresholds_`; apply with `np.interp`. None: identity.

- [ ] **Step 1: Write the failing tests**

```python
def test_gbm_fits_exports_and_reloads_identically(synthetic: pd.DataFrame) -> None:
    feats = [f"f_{i}" for i in range(1, 7)]
    model = fit_gbm(synthetic, feats, model_name="chal", seed=42)
    assert model.num_trees == 300 and model.model_text.startswith("tree")
    again = GBMModel.model_validate_json(model.model_dump_json())
    np.testing.assert_allclose(predict_gbm(again, synthetic), predict_gbm(model, synthetic), atol=0)

def test_gbm_is_deterministic_for_a_seed(synthetic: pd.DataFrame) -> None:
    feats = ["f_1", "f_2", "f_3"]
    a, b = fit_gbm(synthetic, feats, model_name="c", seed=3), fit_gbm(synthetic, feats, model_name="c", seed=3)
    assert a.model_text == b.model_text

def test_gbm_early_stopping_uses_val(synthetic: pd.DataFrame) -> None:
    m = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    model = fit_gbm(apply_split(synthetic, m, "train"), ["f_1", "f_2", "f_3"], model_name="c", seed=1, val_frame=apply_split(synthetic, m, "val"))
    assert 1 <= model.num_trees <= 300

def test_gbm_handles_missing_natively(synthetic: pd.DataFrame) -> None:
    model = fit_gbm(synthetic, ["f_1"], model_name="c", seed=1)   # f_1 has NaN
    assert np.isfinite(predict_gbm(model, synthetic)).all()
```

```python
def test_platt_recovers_identity_on_calibrated_scores() -> None:
    rng = np.random.default_rng(0); p = rng.uniform(0.01, 0.5, 20000); y = rng.random(20000) < p
    cal = fit_calibration(p, y.astype(int), method="platt", fitted_on="val")
    assert cal.platt_a == pytest.approx(1.0, abs=0.1) and cal.platt_b == pytest.approx(0.0, abs=0.1)
    np.testing.assert_allclose(apply_calibration(cal, p), p, atol=0.03)

def test_isotonic_is_monotone_and_bounded() -> None:
    rng = np.random.default_rng(1); s = rng.uniform(size=5000); y = (rng.random(5000) < s**2).astype(int)
    cal = fit_calibration(s, y, method="isotonic", fitted_on="val")
    grid = np.linspace(0, 1, 101); out = apply_calibration(cal, grid)
    assert np.all(np.diff(out) >= -1e-12) and out.min() >= 0 and out.max() <= 1

def test_none_is_identity_and_round_trips() -> None:
    cal = fit_calibration(np.array([0.1, 0.9]), np.array([0, 1]), method="none", fitted_on="val")
    assert CalibrationMap.model_validate_json(cal.model_dump_json()) == cal
    np.testing.assert_array_equal(apply_calibration(cal, np.array([0.2, 0.7])), np.array([0.2, 0.7]))

def test_score_frame_applies_calibration(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    cal = CalibrationMap(method="platt", fitted_on="val", platt_a=1.0, platt_b=1.0)
    scores = score_frame(synthetic, manifest, lambda f: np.full(len(f), 0.1), calibration=cal)
    assert np.allclose(scores["score"], 0.1) and np.allclose(scores["pd"], 1 / (1 + np.exp(-(np.log(0.1 / 0.9) + 1.0))))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_gbm.py packages/core/tests/test_calibration.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `gbm.py`, `calibration.py`; wire `apply_calibration` into `score_frame`**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_gbm.py packages/core/tests/test_calibration.py packages/core/tests/test_scoring.py -v && make lint`
Expected: 9 passed; `test_no_pickle` still passes.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): LightGBM challenger with native text export; Platt and isotonic calibration"
```

---

### Task 11: Discrimination metrics and calibration tests

**Files:**
- Create: `packages/core/modelforge_core/metrics.py`, `packages/core/modelforge_core/calibration_tests.py`
- Test: `packages/core/tests/test_metrics.py`, `packages/core/tests/test_calibration_tests.py`

**Interfaces:**
- Produces:
  - `metrics.py`: `def auc(y: np.ndarray, s: np.ndarray) -> float`; `def gini(y, s) -> float` (`2 * auc - 1`); `def ks(y, s) -> float` (max of `tpr - fpr` over `sklearn.metrics.roc_curve`); `def bootstrap_ci(y: np.ndarray, s: np.ndarray, stat: Callable[[np.ndarray, np.ndarray], float], *, n_samples: int, seed: int, alpha: float = 0.05) -> tuple[float, float]` (percentile interval, resamples drawn with `np.random.default_rng(seed)`, a resample with one class is skipped); `def discrimination_metrics(scores: pd.DataFrame, *, model_name: str, score_col: str = "pd", bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES, seed: int = DEFAULT_SEED) -> DiscriminationMetrics` (one `SplitMetrics` per split present in `scores` that has both classes, in order train, val, oot).
  - `calibration_tests.py`: `def calibration_tests(scores: pd.DataFrame, *, split: Split, alpha: float = 0.05, n_grades: int = DEFAULT_N_GRADES) -> CalibrationTests`. Grades via `pd.qcut(pd_values, n_grades, labels=False, duplicates="drop") + 1` (grade 1 = lowest PD); `n_grades` in the result is the number actually formed. Per grade: `expected_events = sum(pd)`, `expected_pd = mean(pd)`, `p_value = scipy.stats.binomtest(observed, n, expected_pd, alternative="two-sided").pvalue`, `passed = p_value >= alpha`. Hosmer-Lemeshow: `hl = sum((O - E)^2 / (E * (1 - E / n)))` over grades, `df = g - 2`, `p = chi2.sf(hl, df)` when `df >= 1`, else `hl_p_value = nan`. `eo_ratio = expected_total / observed_total`, `inf` when `observed_total == 0`.

- [ ] **Step 1: Write the failing tests**

```python
def test_auc_gini_ks_golden() -> None:
    y = np.array([0, 0, 1, 1]); s = np.array([0.1, 0.4, 0.35, 0.8])
    assert auc(y, s) == pytest.approx(0.75) and gini(y, s) == pytest.approx(0.5) and ks(y, s) == pytest.approx(0.5)

def test_bootstrap_ci_brackets_point_estimate_and_is_seeded(synthetic: pd.DataFrame) -> None:
    y = synthetic["default_flag"].to_numpy(); s = synthetic["f_1"].fillna(0).to_numpy()
    lo, hi = bootstrap_ci(y, s, auc, n_samples=200, seed=1)
    assert lo < auc(y, s) < hi and (hi - lo) < 0.1
    assert bootstrap_ci(y, s, auc, n_samples=200, seed=1) == (lo, hi)

def test_discrimination_metrics_per_split_and_single_class_skipped(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    scores = score_frame(synthetic, manifest, lambda f: 1 / (1 + np.exp(-(-2.6 + 0.8 * f["f_1"].fillna(0) - 0.6 * f["f_2"] + 0.4 * f["f_3"]))))
    dm = discrimination_metrics(scores, model_name="true_logit", seed=0)
    assert [m.split for m in dm.by_split] == ["train", "val", "oot"]
    assert all(0.70 < m.auc < 0.82 and m.auc_ci_low < m.auc < m.auc_ci_high for m in dm.by_split)
    no_oot_events = scores.copy(); no_oot_events.loc[no_oot_events["split"] == "oot", "default_flag"] = 0
    assert discrimination_metrics(no_oot_events, model_name="x", seed=0).get("oot") is None
```

```python
def test_binomial_grades_on_perfect_calibration() -> None:
    rng = np.random.default_rng(0); p = rng.uniform(0.01, 0.4, 50000); y = (rng.random(50000) < p).astype(int)
    scores = pd.DataFrame({"obligor_id": [str(i) for i in range(50000)], "vintage": 1, "default_flag": y, "split": "val", "score": p, "pd": p})
    ct = calibration_tests(scores, split="val")
    assert ct.n_grades == 10 and ct.grades[0].grade == 1 and ct.grades[0].expected_pd < ct.grades[-1].expected_pd
    assert sum(not g.passed for g in ct.grades) <= 2
    assert ct.hl_p_value > 0.01 and 0.95 < ct.eo_ratio < 1.05

def test_binomial_detects_underprediction() -> None:
    rng = np.random.default_rng(0); p = rng.uniform(0.01, 0.2, 20000); y = (rng.random(20000) < 2 * p).astype(int)
    scores = pd.DataFrame({"obligor_id": [str(i) for i in range(20000)], "vintage": 1, "default_flag": y, "split": "val", "score": p, "pd": p})
    ct = calibration_tests(scores, split="val")
    assert sum(not g.passed for g in ct.grades) >= 8 and ct.eo_ratio < 0.6 and ct.hl_p_value < 0.001

def test_heavy_ties_collapse_grades_and_hl_is_nan() -> None:
    pdv = np.array([0.1] * 500 + [0.3] * 500); y = (np.random.default_rng(2).random(1000) < pdv).astype(int)
    scores = pd.DataFrame({"obligor_id": [str(i) for i in range(1000)], "vintage": 1, "default_flag": y, "split": "val", "score": pdv, "pd": pdv})
    ct = calibration_tests(scores, split="val")
    assert ct.n_grades < 3 and math.isnan(ct.hl_p_value)   # qcut with duplicates="drop" collapses to 1 or 2 grades
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_metrics.py packages/core/tests/test_calibration_tests.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `metrics.py` and `calibration_tests.py`**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_metrics.py packages/core/tests/test_calibration_tests.py -v && make lint`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): discrimination metrics with bootstrap CIs; binomial and Hosmer-Lemeshow calibration tests"
```

---

### Task 12: Stability, sensitivity, benchmark

**Files:**
- Create: `packages/core/modelforge_core/stability.py`, `sensitivity.py`, `benchmark.py`
- Test: `packages/core/tests/test_stability.py`, `test_sensitivity.py`, `test_benchmark.py`

**Interfaces:**
- Produces:
  - `stability.py`: `def stability_tests(scores: pd.DataFrame, frame: pd.DataFrame, tables: BinningTables, *, base_split: Split = "train", compare_split: Split, n_bins: int = DEFAULT_N_BINS, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> StabilityTests`. Score PSI: edges from `pd` deciles of the base split, PSI of compare vs base. Feature CSI: for each feature in `tables`, bucket both splits by `assign_bins` and compute `psi_from_shares` over bin-index shares. Raises `ValueError` if either split has no rows in `scores`.
  - `sensitivity.py`: `def sensitivity_analysis(scorecard: Scorecard, tables: BinningTables, frame: pd.DataFrame, *, shifts: Sequence[int] = (-1, 1)) -> SensitivityResults`. Sign check: `ok = coefficient < 0`. Monotonicity: `monotone = tables.get(feature).trend != "none"`. Bin shift: for each feature and shift `k`, move each obligor's non-missing bin index by `k`, clipped to the valid non-missing range, recompute PD with everything else fixed, report mean and max of `|pd_shifted - pd|`.
  - `benchmark.py`: `def benchmark(champion_scores: pd.DataFrame, challenger_scores: pd.DataFrame, *, champion: str, challenger: str, gap_threshold: float, bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES, seed: int = DEFAULT_SEED) -> BenchmarkResults`. Frames joined on `obligor_id`; per split with both classes: AUCs, KSs, `auc_delta = challenger - champion`, paired bootstrap CI of the delta; `decided_on = "oot"` if present else `"val"`; `champion_within_gap = auc_delta(decided_on) <= gap_threshold`.

- [ ] **Step 1: Write the failing tests**

```python
def test_stability_low_on_synthetic_and_detects_shift(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    tables = fit_binning(apply_split(synthetic, manifest, "train"), ["f_1", "f_2"])
    sc = fit_woe_logistic(apply_split(synthetic, manifest, "train"), tables, ["f_1", "f_2"], model_name="m")
    scores = score_frame(synthetic, manifest, lambda f: predict_pd(sc, f))
    st = stability_tests(scores, synthetic, tables, compare_split="oot")
    assert st.score_psi < 0.1 and st.score_psi_band == "pass" and [e.feature for e in st.feature_csi] == ["f_1", "f_2"]
    shifted = synthetic.copy(); shifted.loc[shifted["vintage"] == 5, "f_2"] += 2.0
    st2 = stability_tests(score_frame(shifted, manifest, lambda f: predict_pd(sc, f)), shifted, tables, compare_split="oot")
    assert st2.score_psi > st.score_psi and next(e for e in st2.feature_csi if e.feature == "f_2").band == "fail"

def test_stability_requires_rows_in_both_splits(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="stratified_random", seed=1)
    tables = fit_binning(synthetic, ["f_1"]); sc = fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m")
    with pytest.raises(ValueError):
        stability_tests(score_frame(synthetic, manifest, lambda f: predict_pd(sc, f)), synthetic, tables, compare_split="oot")
```

```python
def test_sensitivity_signs_monotonicity_and_shifts(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1", "f_2", "f_3"])
    sc = fit_woe_logistic(synthetic, tables, ["f_1", "f_2", "f_3"], model_name="m")
    res = sensitivity_analysis(sc, tables, synthetic)
    assert res.all_signs_ok and res.all_monotone
    assert {(b.feature, b.shift) for b in res.bin_shifts} == {(f, k) for f in ("f_1", "f_2", "f_3") for k in (-1, 1)}
    assert all(b.max_abs_pd_delta >= b.mean_abs_pd_delta > 0 for b in res.bin_shifts)

def test_sensitivity_flags_positive_coefficient(synthetic: pd.DataFrame) -> None:
    tables = fit_binning(synthetic, ["f_1"]); sc = fit_woe_logistic(synthetic, tables, ["f_1"], model_name="m")
    flipped = sc.model_copy(update={"features": [sc.features[0].model_copy(update={"coefficient": 0.5})]})
    assert sensitivity_analysis(flipped, tables, synthetic).all_signs_ok is False
```

```python
def test_benchmark_prefers_oot_and_reports_gap(synthetic: pd.DataFrame) -> None:
    manifest = make_splits(synthetic, strategy="vintage", seed=42, oot_vintages=[5])
    true_logit = lambda f: 1 / (1 + np.exp(-(-2.6 + 0.8 * f["f_1"].fillna(0) - 0.6 * f["f_2"] + 0.4 * f["f_3"])))
    weak = lambda f: 1 / (1 + np.exp(-(-2.6 + 0.8 * f["f_1"].fillna(0))))
    champ, chal = score_frame(synthetic, manifest, weak), score_frame(synthetic, manifest, true_logit)
    b = benchmark(champ, chal, champion="weak", challenger="true", gap_threshold=0.01, seed=0)
    assert b.decided_on == "oot" and [s.split for s in b.by_split] == ["train", "val", "oot"]
    oot = next(s for s in b.by_split if s.split == "oot")
    assert oot.auc_delta > 0.01 and oot.auc_delta_ci_low < oot.auc_delta < oot.auc_delta_ci_high and b.champion_within_gap is False
    same = benchmark(chal, chal, champion="a", challenger="a", gap_threshold=0.01, seed=0)
    assert same.champion_within_gap is True and all(s.auc_delta == 0 for s in same.by_split)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_stability.py packages/core/tests/test_sensitivity.py packages/core/tests/test_benchmark.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the three modules**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_stability.py packages/core/tests/test_sensitivity.py packages/core/tests/test_benchmark.py -v && make lint`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): stability (PSI/CSI), sensitivity analysis, champion-challenger benchmark"
```

---

### Task 13: Threshold evaluation to ValidationReport

**Files:**
- Create: `packages/core/modelforge_core/thresholds.py`
- Test: `packages/core/tests/test_thresholds.py`

**Interfaces:**
- Produces: `def evaluate_thresholds(*, model_name: str, thresholds: Thresholds, discrimination: DiscriminationMetrics, calibration: CalibrationTests, stability: StabilityTests, sensitivity: SensitivityResults, benchmark: BenchmarkResults, selection: FeatureSelection, tables: BinningTables, oot_available: bool) -> ValidationReport`.

Test IDs and rules (pinned). `overall` is the worst status among tests, ignoring `not_applicable`, ordered `fail > warn > pass`. `threshold` is a human-readable string such as `">= 0.70 pass, >= 0.65 warn"`. `evidence` holds the raw numbers used.

| test_id | value | rule |
|---|---|---|
| `disc.auc.oot` | OOT AUC | `not_applicable` if not `oot_available` or OOT missing from `discrimination`; pass `>= auc_min`; warn `>= auc_warn`; else fail |
| `disc.auc.val` | val AUC | same bands |
| `disc.ks.oot` | OOT KS | `not_applicable` as above; pass `>= ks_min`; else fail |
| `disc.gap.train_oot` | train AUC minus OOT AUC | `not_applicable` as above; pass `<= gap_max`; warn `<= gap_fail`; else fail |
| `cal.binomial` | share of grades with `passed == False` | pass if 0; warn if `<= grade_fail_share_warn`; else fail |
| `cal.hl` | `hl_p_value` | pass if `>= hl_p_min`; warn otherwise, including NaN (message says too few grades) |
| `cal.eo` | `eo_ratio` | pass if `eo_low <= value <= eo_high`; else warn |
| `stab.psi.score` | `score_psi` | `score_psi_band` |
| `stab.csi.max` | max feature CSI | worst band over `feature_csi` |
| `sens.signs` | count of failed sign checks | pass if 0; else fail |
| `sens.monotonic` | count of non-monotone features | pass if 0; else warn |
| `bench.gap` | `auc_delta` on `decided_on` | pass if `champion_within_gap`; else warn |
| `feat.iv_range` | max IV over `selection.selected` | fail if `> iv_max`; else pass |

- [ ] **Step 1: Write the failing tests**

Build the inputs once in a module-level helper `_pipeline(synthetic, oot_vintages=[5])` that runs splits, binning on train, selection, logistic, GBM, scoring, metrics, calibration tests (on oot), stability (compare oot), sensitivity, benchmark.

```python
def test_report_on_synthetic_is_complete_and_passes(synthetic: pd.DataFrame) -> None:
    report = evaluate_thresholds(**_pipeline(synthetic), model_name="m", thresholds=Thresholds(), oot_available=True)
    assert {t.test_id for t in report.tests} == {"disc.auc.oot", "disc.auc.val", "disc.ks.oot", "disc.gap.train_oot", "cal.binomial", "cal.hl", "cal.eo", "stab.psi.score", "stab.csi.max", "sens.signs", "sens.monotonic", "bench.gap", "feat.iv_range"}
    assert report.get("disc.auc.oot").status in ("pass", "warn") and report.get("sens.signs").status == "pass"
    assert report.overall in ("pass", "warn")

def test_oot_tests_not_applicable_without_oot(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic, strategy="stratified_random")   # stability/calibration on val
    report = evaluate_thresholds(**inputs, model_name="m", thresholds=Thresholds(), oot_available=False)
    assert all(report.get(t).status == "not_applicable" for t in ("disc.auc.oot", "disc.ks.oot", "disc.gap.train_oot"))
    assert "out-of-time" in report.get("disc.auc.oot").message.lower()
    assert report.get("disc.auc.val").status != "not_applicable"

def test_bands_and_overall_worst(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    strict = Thresholds(auc_min=0.99, auc_warn=0.98)
    report = evaluate_thresholds(**inputs, model_name="m", thresholds=strict, oot_available=True)
    assert report.get("disc.auc.oot").status == "fail" and report.overall == "fail"
    lenient = Thresholds(auc_min=0.50, auc_warn=0.40, ks_min=0.0, gap_max=1.0, psi_fail=9.0, psi_warn=8.0, hl_p_min=0.0, eo_low=0.0, eo_high=99.0, grade_fail_share_warn=1.0, benchmark_gap=1.0)
    assert evaluate_thresholds(**inputs, model_name="m", thresholds=lenient, oot_available=True).overall == "pass"

def test_hl_nan_is_warn_with_message(synthetic: pd.DataFrame) -> None:
    inputs = _pipeline(synthetic)
    inputs["calibration"] = inputs["calibration"].model_copy(update={"hl_p_value": float("nan"), "n_grades": 2})
    t = evaluate_thresholds(**inputs, model_name="m", thresholds=Thresholds(), oot_available=True).get("cal.hl")
    assert t.status == "warn" and "grades" in t.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_thresholds.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `thresholds.py` per the table**

- [ ] **Step 4: Run tests to verify they pass, then lint**

Run: `uv run pytest packages/core/tests/test_thresholds.py -v && make lint`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/core
git commit -m "feat(core): threshold evaluation into ValidationReport"
```

---

### Task 14: End-to-end example, real-data smoke test, README

**Files:**
- Create: `examples/core_pipeline.py`, `packages/core/tests/test_smoke_polish.py`
- Modify: `README.md`, `Makefile` (add `example` target)

**Interfaces:**
- Consumes: everything above.
- Produces: `examples/core_pipeline.py` CLI with `--dataset {polish_bankruptcy,taiwan_bankruptcy}` (default polish), `--oot-vintages` (default `5`), `--data-dir` (default `data`), `--out` (default `out`), `--seed` (default 42), `--max-features` (default 20). Writes `out/<dataset>/scorecard.json`, `out/<dataset>/gbm_model.txt`, `out/<dataset>/validation_report.json`, `out/<dataset>/binning_tables.json`, `out/<dataset>/column_dictionary.json`, and prints a one-line-per-test summary of the report plus overall status. For a dataset with `oot_capable=False` it uses `stratified_random` and passes `oot_available=False`.

Pipeline order inside the example (this is also the shape SP2's deterministic nodes will follow): load -> profile -> leakage (print flags) -> splits -> binning on train over all features minus leakage flags -> selection (cap at `--max-features` by IV) -> logistic on train -> GBM on train with val early stopping -> Platt calibration of the champion on val -> score both -> discrimination -> calibration tests on oot (or val) -> stability compare oot (or val) -> sensitivity -> benchmark -> thresholds.

- [ ] **Step 1: Write the smoke test**

```python
@pytest.mark.slow
def test_polish_end_to_end(data_dir: Path, tmp_path: Path) -> None:
    result = run_pipeline(dataset="polish_bankruptcy", oot_vintages=[5], data_dir=data_dir, out_dir=tmp_path, seed=42, max_features=20)
    report: ValidationReport = result.report
    assert report.get("disc.auc.oot").value is not None and report.get("disc.auc.oot").value > 0.65
    assert len(report.tests) == 13 and report.get("sens.signs").status == "pass"
    assert (tmp_path / "polish_bankruptcy" / "scorecard.json").exists()
    assert from_json((tmp_path / "polish_bankruptcy" / "scorecard.json").read_text()).model_name == "polish_bankruptcy_pd"
    assert 5 <= len(result.selection.selected) <= 20

@pytest.mark.slow
def test_taiwan_end_to_end_marks_oot_not_applicable(data_dir: Path, tmp_path: Path) -> None:
    result = run_pipeline(dataset="taiwan_bankruptcy", oot_vintages=[], data_dir=data_dir, out_dir=tmp_path, seed=42, max_features=20)
    assert result.report.get("disc.auc.oot").status == "not_applicable"
    assert result.report.get("disc.auc.val").value is not None and result.report.get("disc.auc.val").value > 0.80
```

`run_pipeline(...) -> PipelineResult` (a dataclass holding `report`, `scorecard`, `gbm`, `selection`, `tables`, `manifest`) lives in `examples/core_pipeline.py`; the test imports it by adding `examples/` to `sys.path` in the test module.

- [ ] **Step 2: Run the smoke tests to verify they fail**

Run: `uv run pytest packages/core/tests/test_smoke_polish.py -v`
Expected: FAIL with `ImportError` (no `core_pipeline` module).

- [ ] **Step 3: Implement `examples/core_pipeline.py` with `run_pipeline` and a `main()` using `argparse`; add `example` Makefile target running it**

- [ ] **Step 4: Run the smoke tests and the example**

Run: `uv run pytest packages/core/tests/test_smoke_polish.py -v && make example`
Expected: 2 passed (first run downloads both archives into `data/`); the example prints 13 test lines and an overall status, and `out/polish_bankruptcy/` contains the five files.

- [ ] **Step 5: Update the README**

Replace the stub body with: one paragraph on what the toolkit does, a "Quick start" (`uv sync --all-groups`, `make lint test`, `make example`), the dataset citations (UCI Polish companies bankruptcy data, DOI 10.24432/C5F600, CC BY 4.0; UCI Taiwanese bankruptcy prediction), the two documented proxies (bankruptcy as default, horizon files as vintages), the macOS `libomp` note if Task 1 needed it, and a "Status" line pointing at the spec and this plan.

- [ ] **Step 6: Run the full gate**

Run: `make lint && make test`
Expected: lint clean; all tests pass including the slow ones.

- [ ] **Step 7: Commit and push-ready check**

```bash
git add -A
git commit -m "feat(core): end-to-end example pipeline, real-data smoke tests, README"
git status --short
```

Expected: clean tree. The branch is ready for the whole-branch review.

---

## Self-review notes

- **Spec coverage.** Section 17.1 items map to Tasks 1 to 14: repository and tooling (1), schemas and standards (2), datasets (3 to 5), profiling and leakage (6), PSI and splits (7), binning and selection (8), logistic and scorecard and scoring (9), GBM and calibration (10), metrics and calibration tests (11), stability, sensitivity, benchmark (12), thresholds (13), smoke test and example (14). Section 10.3 no-pickle is Task 1's scan test plus the native-text export in Task 10. Section 4.1 import rules are Task 1's import-linter contract. Section 6 artifact names all appear in Task 2.
- **Type consistency.** `Scorecard`, `BinningTables`, `SplitManifest`, `CalibrationMap`, `SCORES_COLUMNS` are defined once in Task 2 and consumed by name in Tasks 8 to 14. `score_frame` takes a `predict` callable so champion and challenger share one scoring path.
- **Review Focus.** Item 1 and 2 are tested in Task 8 (`test_transform_woe_handles_unseen_missing_and_out_of_range`); item 3 in Tasks 11 and 13; item 4 in Tasks 11 and 13; item 5 in Tasks 6 and 8.
- **Deliberately open to the implementer:** exact ruff rule exclusions if a rule fights pandas idioms; helper decomposition inside modules; argparse wording; how `_pipeline` in Task 13's tests shares code with `run_pipeline` (duplication is acceptable there).
