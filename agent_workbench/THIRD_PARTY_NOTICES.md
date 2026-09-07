# Third-Party Distribution Notes

AgentWorkbench original code is licensed under the MIT License in LICENSE, as selected by the project owner on 2026-09-07. That selection does not relicense any dependency or model asset. In particular, it does not remove PyMuPDF's AGPL/commercial licensing requirements from a bundled distribution.

Generated for the AgentWorkbench Demo dependency set on 2026-09-04.

| Component | License / distribution note |
|---|---|
| FastAPI, Starlette, Pydantic, Uvicorn, HTTPX | Permissive open-source licenses; include the installed distributions' license files in release review. |
| NumPy | BSD-3-Clause with bundled dependency notices. |
| ONNX Runtime | MIT. |
| Hugging Face Tokenizers | Apache-2.0. |
| keyring | MIT. |
| pywebview | BSD-3-Clause. |
| Microsoft Edge WebView2 Runtime | The standard Windows installer embeds the Microsoft Evergreen Bootstrapper, used only with consent when the system runtime is missing. The archived 0.4.7 offline package contains the Standalone Installer (x64). Microsoft software terms apply; this runtime is not an open-source component of AgentWorkbench. Source, publisher-signature verification and SHA-256 are recorded in the release notes. |
| python-docx 1.2.0, openpyxl 3.1.5, et-xmlfile | MIT. Word/Excel extraction; no Office automation. |
| xlrd 2.0.2 | BSD-3-Clause. Legacy Excel read support. |
| Pillow 12.3.0 | MIT-CMU and bundled dependency notices. |
| lxml, defusedxml | BSD-3-Clause and PSF respectively; preserve installed license texts. |
| Lucide 0.468.0 | ISC; pinned offline browser bundle and upstream license in assets/web/vendor. |
| Three.js 0.180.0 | MIT; renderer, OrbitControls and exact LICENSE are vendored in assets/web/vendor/three. Runtime performs no CDN downloads. |
| markdown-it 15.0.1 | MIT; vendored browser bundle and upstream LICENSE in assets/web/vendor. |
| DOMPurify 3.4.14 | Apache-2.0 OR MPL-2.0; both upstream license texts included in assets/web/vendor. |
| PyInstaller | GPLv2-or-later with the PyInstaller bootloader exception. |
| multilingual-e5-small model assets | Review and preserve the upstream model license and attribution before external distribution. |
| PyMuPDF | AGPL 3.0 or Artifex commercial license. Closed-source distribution requires an appropriate commercial license or a compatible replacement. |

This file is an engineering inventory, not legal advice. A release must bundle the exact license files from the versions actually shipped.

The V7 browser dependency manifest records npm archive SHA-512 integrity and SHA-256 for the shipped bundles and license files. Scripts/vendor_web_dependencies.py reproduces these pinned artifacts; the application does not download JavaScript at runtime. Playwright is a development-only verification dependency, not part of the installed desktop runtime.
