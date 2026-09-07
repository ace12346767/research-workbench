# Threshold Calibration Protocol

Frozen before grid execution, 2026-09-07.

Scope: restrained-v1, 80 calibration messages only. Reuse score-diagnostics.json from the installed baseline. Do not inspect validation inputs until selection.json is written.

1. Verify corpus and implementation hashes and 80 baseline predictions before searching.
2. Fixed grid: min_score = 0.520 through 0.960 in steps of 0.005; min_margin = 0.000 through 0.050 in steps of 0.001. Include baseline 0.520/0.030.
3. Preserve parsing, exclusions, top-3 aggregation, conflicting-window abstention, examples, prompts and labels. No per-tier thresholds or exceptions.
4. Eligible candidates have zero negative false triggers and zero wrong-tier triggers on calibration. Maximize correctly triggered positives. Break ties by the greatest worst-case correct-trigger count in a 3x3 one-grid-step neighborhood (neighbors with any false/wrong trigger count as zero), then by higher margin and higher score.
5. Export every combination and neighborhood. Freeze one selection before validation. If no improvement over baseline, report the limitation instead of forcing a change.
6. Verify selected thresholds on real production E5/router for the same 80 messages. This is a replay, not new independent evidence.
7. Run the 20 reserved messages once through a source-native isolated desktop with only the two thresholds overridden in memory. An installed binary cannot accept these experimental thresholds without rebuilding; do not claim this is a new installed-release test.
8. Validation acceptance: zero negative false triggers, zero wrong-tier triggers, no runtime failures, and positive recall above the calibration baseline 7/48. Report each tier even if aggregate passes; this is a small internal holdout, not external generalization evidence. Do not retune after seeing validation.
9. Cross-check exact user texts, route injection, manual reasoning policy, renderer errors and database history. Close only test processes. Leave user settings and installation unchanged.

Artifacts: calibrate.py (cached replay and selection), selection.json, grid.csv, calibration-results.json, native-launch.py/native-ui.cjs, validation-results.json, history-audit.json, REPORT.md.
