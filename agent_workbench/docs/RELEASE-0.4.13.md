# 0.4.13 Release Evidence

Date: 2026-09-07. Product: AgentWorkbench. Target: Windows x64 standard installer.

## Changes

- Default semantic score/margin: 0.835 / 0.013, shared by the ranking function, constructor and production factory.
- Existing examples, guidance prompts, rules and reasoning linkage are unchanged.
- Chinese project documentation, 19 UI/native screenshots, source-only GitHub export and pinned ONNX download helper.
- The installed application was not overwritten during this work.

## Installer

- File: AgentWorkbench-Setup.exe
- Product version: 0.4.13
- Bytes: 142487246 (about 142.5 MB decimal / 135.9 MiB)
- SHA-256: FA980C54E19FC492138238A22FD513EC71A1DA8C0C9E13CA2E5FA196BF6AA962
- Standard package: bundled Python application, local E5 and browser assets; system WebView2 reused, consented Bootstrapper if absent.
- Not an offline all-prerequisites installer; not a clean-machine certification; no application Authenticode signing claim.

## Verification

| Check | Result |
| --- | --- |
| Python tests | 318 passed |
| Frontend tests | 43 passed, 0 failed |
| Native WebView2 regression | 34 checks passed on second run; renderer interaction errors empty |
| Packaged data/restart checks | 10 passed |
| Packaged tray/single-instance checks | 5 passed |
| Documentation UI capture | 17 WebView2 images, no renderer errors; PDF page loaded; orbit drag changed 3D pixels |
| Additional native dialog capture | 2 images from native regression |
| Standalone source ZIP extraction | 318 Python tests passed and app --smoke returned ok from the extracted repository root |

The source extraction check reused the existing Python dependency environment and copied the exact verified ONNX weight into the extracted checkout (the ZIP itself excludes it). It verifies source layout and isolation from the parent learning project, not a fresh pip install or a complete first-time network download.

The first native regression did not pass: a close-preference replacement received WinError 5 (access denied), leaving a native error dialog. Its report was retained; after acknowledging the dialog, the test recorded a failed reset-preference assertion. A fresh serial run passed all 34 checks without changing product code. The transient access-denied cause has not been conclusively isolated and remains a Windows file-access risk; this release does not claim it was fixed.

Internal evidence paths (excluded from the public source archive):

- .tmp/v30-build.log
- .tmp/v30-native.log and .tmp/desktop-v15-5b38khmk/report.json (first failure)
- .tmp/v30-native-r2.log and .tmp/desktop-v15-lu3gz5up/report.json (passed rerun)
- .tmp/packaged-v15-zpjgowze/report.json
- .tmp/packaged-tray-7j8dcubf/report.json

## Routing Evidence Boundary

Calibration: 25/48 positive triggers and 0/32 negative false triggers. Reserved set: 5/12 positive triggers and 2/8 negative false triggers. User accepted the candidate as a product tradeoff after seeing these results. Original labels and reports were not rewritten; the original zero-false-trigger holdout criterion did not pass.

## Screenshot Provenance

The documentation capture uses a real source-native WebView2 desktop of this version with separate data and synthetic PDFs, manually populated classifications and an explicitly labeled offline response fixture. It is not a recording of automatic LLM paper classification or a model-quality benchmark. Images do not contain actual API keys or private user history. Native dialog images come from the isolated native regression.

## Publishing

Use the source ZIP as a clean standalone repository root. It contains a root README and the Python package under agent_workbench/, excludes personal experiment drivers and managed data, and includes a source manifest. Installers belong in release assets. The project owner subsequently selected MIT for original code on 2026-09-07; the source ZIP includes a root LICENSE. Third-party dependencies keep their own terms. This source/documentation license update did not rebuild the installer identified above.
