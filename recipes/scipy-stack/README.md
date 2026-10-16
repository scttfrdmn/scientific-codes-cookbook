---
tool: scipy-stack
tool_version: "NumPy 2.5.4 / SciPy 1.18.1 / pandas 3.0.6"
env: geo-ml
image: quay.io/aarchsci/geo-ml@sha256:14ef7fe13ceffac286f44cd7b576a471d13144dd3e462199e01962ac4efc008c
gap_x86_64: the aarch.science `geo-ml` env is arm64-only (manifest list has a linux/arm64 entry and no amd64, checked 2026-10-16) and aarch.* builds ARM only; no x86 build of the same env exists
spawn_version: 0.127.0
last_verified: 2026-10-16
---
# NumPy, SciPy and pandas — their own 369,149 tests on Graviton4, every one accounted for

Runs the three projects' own test suites, each the way its own wheel build tests an installed package, on one Graviton4 box. Every other Python recipe here sits on these three, so this is the foundation check.

## Run it

```bash
make stage RECIPE=scipy-stack    # once: 9 pinned wheels + scipy's companion test package
spawn task run --spec "$(make -s spec RECIPE=scipy-stack)" --wait
make ls RECIPE=scipy-stack

numpy.test(label='full', extra_argv=['-n=auto'])                     # tools/wheels/cibw_test_command.sh
scipy.test(parallel=N)                                               # scipy's wheel test, on N workers
pd.test(extra_args=['-m not clipboard and not single_cpu and not slow and not network and not db',
                    '-n N', '--no-strict-data-files'])               # then `single_cpu`, serially
```

## Make it yours

| In the recipe | Swap for | What to know |
|---|---|---|
| `scipy-tests` staged from conda-forge | `scipy.test()` on the image alone | conda-forge ships scipy's tests as a **separate package**, and this env has none: `scipy.test()` finds 0 test files. Stage the build whose `depends` names your scipy build exactly (`scipy 1.18.1 py314hb3ba6a9_1` here). |
| a copy of the three packages in `/tmp` | running in `site-packages` | the suites write beside their own test files, and `site-packages` is read-only to the image's user. The copy is checked `diff -r`-identical; the extensions link `-rpath /opt/conda/lib`, so they load the image's own libraries. |
| `meson` among the staged wheels | leaving it out | numpy's f2py tests run `meson` while being **imported**: without it, 31 test modules fail to collect instead of skipping. |
| `PYTHONHASHSEED=0` | unset | xdist aborts collection when workers hash a set-iterating parametrization differently. |
| `geo-ml` | another aarch.science env | 13 envs carry these exact versions, but a different env has different optional dependencies, so the skips and the attributed failures below must be re-derived. |

**Leave it.** The suites are the fixture and there is nothing to scale. **Widen it** by running scipy's slow tests (`label='full'`, 11,669 deselected here) or by adding the optional readers pandas skips for (numexpr, openpyxl, lxml: ~12,500 skips).

## Shape, size, cost

One task on `c8g.8xlarge` (32 vCPU / 64 GiB), TTL 60m as a **backstop** with `cost_limit` $0.80 as the real guard. numpy's large-file tests write to `/tmp`, a tmpfs of half the RAM, which is what sized the box. The suites take 33 s, 55 s, 116 s and 146 s (pandas' serial group), inside ~10 billed minutes ([layout](../../patterns/layout-and-effective-cost.md)) — these timings are not compute cost.

<details>
<summary>As shipped: 333,866 passing, 25 failures each attributed to a named cause, none to the chip</summary>

### The checks

| observable | assertion | observed |
|---|---|---|
| staged wheels + `scipy-tests` / checks | match pins | 10 of 10 / 2 of 2 |
| `scipy-tests` build | depends on **exactly** the installed scipy build | `scipy 1.18.1 py314hb3ba6a9_1` both |
| the copy | `diff -r` identical; scipy differs only by added files; imports resolve to it | yes / 1,071 files added / yes |
| BLAS | recorded (the image's OpenBLAS) | 0.3.34 pthreads, `neoversev2`, 32 threads; NumPy dispatch `SVE*` |
| **every collected test has one outcome** | **exact: 0 missing, 0 uncollected, 0 multiple** | **all four groups** |
| **summary == items + collection-time skips** | **exact** | **50,865 + 0 · 84,787 + 1 · 230,774 + 9 · 2,723 + 9** |
| **pandas' two groups partition one collection** | **exact** | **230,774 + 2,723 = 233,497** |
| collected, per group | ≥ 49,800 / 83,000 / 226,000 / 2,650 | 50,865 / 84,787 / 230,774 / 2,723 |
| **every failure attributed by its own traceback** | **exact count per cause; any other failure fails the run** | **10 + 13 + 1 + 1** |

| suite | passed | skipped | xfailed | xpassed | failed / errors | wall |
|---|---|---|---|---|---|---|
| NumPy (`label='full'`, 32 workers) | 47,754 | 1,534 | 31 | 1,536 | 0 / 10 | 33 s |
| SciPy (`not slow`, 32 workers) | 76,460 | 8,104 | 204 | 20 | **0 / 0** | 55 s |
| pandas (parallel, 32 workers) | 208,080 | 21,270 | 1,327 | 93 | 13 / 0 | 116 s |
| pandas (`single_cpu`, serial) | 1,572 | 911 | 246 | 1 | 2 / 0 | 146 s |

### The 25 failures, and why none is the chip

Each was run, not deselected, and each is matched by test id **and** by the text of its own
traceback, at its exact count — so a fix upstream or in the image makes the run fail, and the page
goes stale loudly instead of silently.

| failures | tests | cause |
|---|---|---|
| 10 errors (setup) | numpy `_core/tests/test_mem_policy.py` (9), `test_array_interface.py::test_cstruct` | they compile a C extension at test time; `meson` says `Unknown compiler(s)` — the runtime env has no C compiler |
| 13 | pandas `extension/test_arrow.py`: `test_dt_tz_localize*` (5), `test_from_sequence_of_strings_pa_array[timestamp[*, tz=US/*]]` (8) | pyarrow reads `/usr/share/zoneinfo`, and the image's Debian 13 `tzdata` dropped the legacy `US/Pacific` / `US/Eastern` links (they moved to `tzdata-legacy`). conda's own zoneinfo has them; pyarrow does not consult it, even with `TZDIR` set. A real limitation of this image for anyone using those zone names with Arrow-backed data |
| 1 | pandas `io/test_sql.py::test_con_string_import_error` | upstream: `pandasSQL_builder` now says "without version '2.0.36' or newer of 'sqlalchemy'", the test still expects "without sqlalchemy installed"; it only runs when sqlalchemy is absent |
| 1 | pandas `window/test_numba.py::TestEngine::test_numba_min_periods` | numba 0.68 cannot type pytest's rewritten `assert` inside the jitted test function (`_call_reprcompare`); the same test passes under `--assert=plain` |

pandas' own aarch64 wheel test also deselects its `fails_arm_wheels` marker. This run does not: those
42 sparse-array tests ran, and none failed.

### Skips are optional pieces, not hidden problems

**NumPy**: no Fortran compiler (881 f2py), mypy (178), Cython (112), and 10 tests asking for more
than numpy's own `NPY_AVAILABLE_MEM="4 GB"`. **SciPy**: 3,479 `SCIPY_XSLOW` tests and parameter
combinations its own tests declare inapplicable. **pandas**: optional I/O and engines the env does
not carry (numexpr ~11,600, the Excel readers, lxml, sqlalchemy, moto), and data files the conda
package strips, which `--no-strict-data-files` — upstream's own wheel-test flag — turns into skips.

### How the suites run

This is the [reference-from-tests](../../practices/reference-from-tests.md) shape at its largest:
three test suites, each run through the project's own entry point with its wheel test's arguments,
plus its CI's per-test timeout (numpy 600 s, scipy 60 s; pandas sets none, so numpy's 600 s guards
against a hang). The counting is scikit-learn's: collection in a **separate** `--collect-only`
process, every report recorded from inside pytest by `account.py`, and `suite_check.py` asserting
one outcome per collected test. Items are recorded after `-m` deselection, not before.

### Pins

| | |
|---|---|
| data | **none** — the suites carry their own |
| test runner | `pytest 9.0.3` (pandas allows `<9.1`), `pluggy 1.6.0`, `iniconfig 2.3.1`, `hypothesis 6.152.1` (numpy's CI pin), `pytest-xdist 3.8.0`, `execnet 2.1.2`, `pytest-timeout 2.4.0`, `tzdata 2026.5`, `meson 1.12.1` — pure-Python wheels, sha256-pinned in `stage-inputs.sh` |
| scipy's tests | `linux-aarch64/scipy-tests-1.18.1-py314hb3ba6a9_1.conda` from conda-forge, sha256 `046508ea…` as published |
| checks | `account.py`, `suite_check.py`, pinned by sha256 |
| image | `quay.io/aarchsci/geo-ml@sha256:14ef7fe1…` — numpy 2.5.4, scipy 1.18.1, pandas 3.0.6, pyarrow 25.0.0, numba 0.68.0, OpenBLAS 0.3.34, python 3.14.8 |

cosign-verified against `playgroundlogic/aarchsci`; the signature covers the **manifest-list**
digest (`c78a4a9e…`), so verify the tag and pin the arm64 digest. No x86 run: `geo-ml` is an
aarch.science env and has no x86 build.

### Run + verify

```sh
make stage RECIPE=scipy-stack
spawn task run --spec "$(make -s spec RECIPE=scipy-stack)" --wait
aws s3 cp "s3://$(make -s print-bucket)/runs/scipy-stack/r1/suite-summary.txt" -
```

Fails on a pin mismatch, a `scipy-tests` built for another scipy, a copy that is not identical or
that the suites do not import, OpenBLAS loading from outside the image, a collected test with no
outcome or more than one, a summary that does not equal tests plus collection-time skips, pandas'
groups not partitioning its collection, a collection under its floor, any failure not attributed
above, or an attributed count that moves. Check the bucket regardless
([exit 0 isn't proof](../../practices/container-path.md)).

### Not covered

**The slow tiers**: scipy's `slow` and `xslow` tests and pandas' `slow` marker. **The C-API tests**
that need a compiler, and f2py. **The optional engines** pandas skips for. **Threading and scaling**:
the suites run on 32 workers, but nothing here measures BLAS threads or cores, which is where an
arm64 difference would show as cost rather than correctness. **x86**, because this env has no x86 build.

</details>
