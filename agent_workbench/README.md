# Research Workbench

**本地优先的 Windows 桌面科研助手：连接文献阅读、知识积累与研究工作区。**

将文件工具、写入审批、长期会话、本地知识检索和论文时间树放进同一个桌面工作台。面向个人研究与开发任务，重点展示 Agent 工程的状态管理、可控执行和数据可追溯性，而不是只接一个聊天 API。

仓库名为 `research-workbench`。当前 0.4.13 客户端、Python 包及安装文件沿用 `AgentWorkbench` 名称，截图中的品牌与此一致；仓库命名不改变现有安装标识或数据目录。文末提供面试 Demo 的讲解路线和工程设计说明。

**版本：0.4.13** · Windows x64 · Python + FastAPI + WebView2 · 本地 E5 / ONNX · Three.js

> 交付物是 `AgentWorkbench.exe` 和 Windows 安装包，不是只能在浏览器中打开的网站。界面使用 HTML/CSS/JavaScript，但运行在原生 pywebview/WebView2 窗口中。安装后的应用不要求用户另装 Python 或 Node.js。

![聊天与工作区](docs/screenshots/02-chat-workspace.png)

> **截图说明**：以下画面来自真实原生窗口中的 WebView2 界面，使用独立测试目录。论文为自建的合成 PDF，分类为人工填写；演示回答由离线 fixture 生成。截图展示交互与数据链路，不宣称模型回答质量、自动分类准确率或真实科研成果。未使用私人会话、API Key 或个人论文。

## 项目定位

这个 Demo 适合用来讲清楚三件事：

1. **Agent 如何受控地行动**：工具 schema、执行循环、流式事件、取消、写入审批和冲突检查。
2. **Agent 如何保留与使用信息**：SQLite 原始历史、上下文压缩 checkpoint、PDF/卡片向量检索和来源定位。
3. **如何把原型变成桌面产品**：原生窗口与托盘、单实例、离线前端资源、安装包、分层回归和真实界面验证。

动态路由是一个可选的处理偏好功能，不包装成“自动识别所有任务难度”的通用方案。论文树是时间与主题的浏览视图，不是引用关系或学术谱系推断。

## 功能总览

| 模块 | 当前能力 | 使用边界 |
| --- | --- | --- |
| 聊天 | 流式回答/推理展示、工具事件、发送与停止合一、复制回答、多会话 | 推理内容与 token usage 取决于接入端返回 |
| 模型接入 | OpenAI-compatible 接口、拉取模型列表、输入区选模型、推理强度选择 | 不保证所有兼容接口和所有模型参数一致 |
| 工作区 | 文件树、文本预览、列举/搜索/读取、精确文本替换 | 受路径和大小限制，不是任意终端执行器 |
| 权限 | 只读、审批写入、工作区自动编辑、修改审计 | 应用层防护，不是操作系统级沙箱 |
| 消息附件 | 图片、PDF、DOCX、XLSX/XLS、文本和代码；预览、拖放、粘贴图片 | 单条最多 6 个；单文件 10 MiB、合计 20 MiB；旧 DOC 不支持 |
| 上下文 | 用量展示、预算预留、自动/手动摘要压缩、历史回查 | 压缩只影响模型上下文，不删除原始会话 |
| 动态引导 | 三组触发示例与三段引导正文分开设置；允许不命中 | 基于嵌入相似度，不是独立 LLM 分类器 |
| 推理联动 | 默认保留手动档位；可开启自动映射并注入引导 | 不强制 DeepSeek max；不支持的档位不随意猜参数 |
| 知识卡片 | 手动编辑、模型提出草稿、重复检查、用户确认、索引 | 提出草稿不等于自动批准保存 |
| PDF 知识库 | 文本提取、分页分块、本地索引、检索来源、失败状态与重试 | 不含 OCR；复杂表格、公式和多栏恢复不保证完整 |
| 论文时间树 | 时间轴/主题分支、三维旋转缩放、搜索筛选、列表回退 | 未知年份保持未知，连线不是引用关系 |
| 论文分类 | 按需模型分类、可选导入后分类、字段依据、人工修订 | 分类会调用已配置模型；人工修订应保留 |
| 数据维护 | 索引审计/修复、数据目录迁移、重启恢复 | 数据迁移保留原目录，不支持两个版本同时写同一目录 |
| 桌面外壳 | 莫比乌斯图标/欢迎曲面、浅色标题栏、首次关闭询问、托盘、单实例 | Windows 为当前验证目标，不声称跨平台安装已验收 |

## 界面导览

### 1. 欢迎与聊天工作区

欢迎页的莫比乌斯曲面由 Three.js 渲染；会话开始后进入以消息和工具结果为中心的工作区。正文支持安全 Markdown、表格和代码块，长代码保持容器内滚动。

![欢迎曲面](docs/screenshots/01-welcome.png)

文件预览与写入审批是不同环节。模型调用 `edit_file` 后，先展示预期变更，再决定批准或拒绝，不通过“提示模型小心”替代执行约束。

| 文件预览 | 写入审批 |
| --- | --- |
| ![文件预览](docs/screenshots/03-file-preview.png) | ![写入审批](docs/screenshots/05-write-approval.png) |

### 2. 上下文与当前消息附件

上下文面板区分估算、容量和输出预留。附件跟随会话保存，但不会因为上传到消息就自动进入长期知识库。Word/Excel 使用内容提取，不是 Office 页面渲染，不执行宏或重算公式。图片理解要求接入模型支持视觉。

| 上下文详情 | 文件与图片附件 |
| --- | --- |
| ![上下文详情](docs/screenshots/04-context.png) | ![附件](docs/screenshots/16-attachments.png) |

### 3. 知识库与卡片

卡片保存可复用结论；模型通过 `draft_card` 提出候选及保存理由，用户查看、编辑后确认。知识库还提供索引一致性审计和受控修复。

![知识库](docs/screenshots/08-knowledge-library.png)

| 编辑卡片 | 审阅模型草稿 |
| --- | --- |
| ![卡片编辑](docs/screenshots/06-knowledge-card.png) | ![卡片草稿](docs/screenshots/07-card-draft.png) |

### 4. 论文时间树与原文阅读

每篇论文保留标题、年份、出处和主题。三维视图适合浏览，列表适合扫描；详情页可以人工修订元数据、请求模型分类、查看字段依据，或限定到该论文发起检索问答。

![论文时间树](docs/screenshots/09-paper-tree.png)

![论文列表](docs/screenshots/10-paper-list.png)

| 论文详情 | 本地 PDF 阅读 |
| --- | --- |
| ![论文详情](docs/screenshots/11-paper-detail.png) | ![PDF 阅读](docs/screenshots/12-paper-reader.png) |

### 5. 接入设置与动态引导

触发示例决定“何时使用”，引导正文决定“使用后如何提示模型”。推理强度联动是独立开关；关闭时，路由不覆盖用户手动选择。

| 模型与应用设置 | 三档示例及引导 |
| --- | --- |
| ![设置](docs/screenshots/13-settings.png) | ![动态引导设置](docs/screenshots/14-routing-settings.png) |

![路由解释](docs/screenshots/15-route-details.png)

输入区模型列表是独立弹层，不必跳回设置；可取消而不强制选择。首次关闭窗口可选择退出或隐藏到托盘，错误信息使用原生对话框并按需展开详情。

![输入区模型选择](docs/screenshots/17-model-picker.png)

| 原生关闭选择 | 原生错误详情（测试 fixture） |
| --- | --- |
| ![关闭选择](docs/screenshots/18-native-close.png) | ![错误详情](docs/screenshots/19-native-error.png) |

## 技术栈

| 层次 | 技术 | 选择原因 |
| --- | --- | --- |
| 语言 | Python 3.11（本机验证环境）、原生 JavaScript、HTML/CSS | Python 负责 Agent/解析/本地模型；轻量前端减少构建链路 |
| 桌面 | pywebview 5.x、Windows Forms/.NET、Microsoft WebView2 | 复用 Web UI，同时提供独立窗口、文件选择和托盘能力 |
| 服务 | FastAPI、Starlette、Uvicorn、Pydantic | 本机 HTTP API、结构化校验、清晰的运行时边界 |
| 模型协议 | HTTPX、OpenAI-compatible 流式接口 | 适配可配置服务端，不将产品绑定单一供应商 |
| 流式交互 | asyncio、SSE | 统一传递回答、工具、路由、上下文、审批、错误和结束状态 |
| 本地语义 | multilingual-e5-small、ONNX Runtime CPU、HF Tokenizers、NumPy | 本地 384 维嵌入，路由/检索无需单独调用远端 embedding API |
| 存储 | SQLite、Markdown 卡片、受管文件目录 | 原始历史可回查，向量与元数据同事务，便于本地分发 |
| 文档 | PyMuPDF、python-docx、openpyxl、xlrd、Pillow | PDF/Office 文本提取、PDF 页渲染和图片标准化 |
| 渲染 | Three.js、OrbitControls、Lucide、markdown-it、DOMPurify | 离线三维交互、图标与安全 Markdown |
| 交付/验证 | PyInstaller、Inno Setup 6、pytest、Node test runner、Playwright | 打包运行时，覆盖后端、DOM、原生外壳和打包后重启 |

具体依赖以 [requirements.txt](requirements.txt)、[package-lock.json](package-lock.json) 和离线资源清单为准。Node.js、Playwright 是开发/验证工具，不是安装版运行依赖。项目没有使用 React、Electron、LangChain、LangGraph 或外部向量数据库。

## 架构与代码入口

```mermaid
flowchart LR
    U[用户] --> D[原生桌面窗口 / WebView2]
    D -->|本机 HTTP| API[FastAPI]
    API --> R[ApplicationRuntime]
    R --> G[偏好路由 / 本地 E5]
    R --> L[Agent 执行循环]
    L -->|流式请求| P[兼容模型服务]
    L --> T[受控工具 / 审批]
    T --> W[工作区文件]
    T --> K[PDF 与卡片检索]
    R --> H[SQLite 会话 / Checkpoint]
    K --> V[SQLite 元数据与向量]
    R -->|SSE 事件| D
```

| 文件/目录 | 面试时重点阅读 |
| --- | --- |
| [app.py](app.py)、[bootstrap.py](bootstrap.py) | 原生窗口、本机服务启动/退出、依赖组装 |
| [runtime.py](runtime.py)、[server/](server/) | 会话级状态、互斥操作、API 与 SSE |
| [core/loop.py](core/loop.py) | 模型流解析、工具调用、取消与执行循环 |
| [core/workspace.py](core/workspace.py) | 路径边界、文件读写与审批 |
| [core/history.py](core/history.py)、[core/compaction.py](core/compaction.py) | 原始历史、摘要 checkpoint 与上下文构造 |
| [router/](router/) | 引用过滤、窗口、显式规则、语义排名与弃权 |
| [knowledge/](knowledge/) | PDF、卡片、索引一致性和论文元数据 |
| [providers/](providers/) | 模型接口与推理档位映射 |
| [assets/web/](assets/web/) | 原生窗口中的 UI、三维论文树和交互状态 |
| [tests/](tests/)、[scripts/](scripts/) | 分层回归、安装版检查与可复现截图 |

### 一轮请求如何执行

1. 界面提交原始消息及附件 ID，并立刻进入运行态；发送按钮变为停止按钮。
2. 路由只分析用户请求的投影，过滤可识别引用/代码，不把附件证据当作触发指令；原消息不被改写或丢弃。
3. 构造系统提示、可用工具、历史/摘要与当前消息；预留输出和安全余量，必要时先压缩历史上下文。
4. 模型返回文本或工具调用；工具参数经校验，写入根据权限模式进入审批。
5. 结果通过 SSE 更新页面。结束时持久化原始会话，记录路由、结束原因和提供方用量。

### 三个值得展开的设计取舍

**历史保存不等于每次全量发送。** 原始会话保存在 SQLite；发送给模型的上下文可以使用摘要 checkpoint。达到可用输入预算的约 85% 时触发压缩，另有手动入口。这里的可用输入预算已经扣除输出预留和安全余量，不是整个模型窗口的 85%。摘要生成有独立输出预算，不具备工具执行权限；它也不是知识卡片。

**知识写入与知识召回分开。** 先保存或确认来源，再做索引；向量和元数据在同一 SQLite 事务提交。PDF 导入、分类、卡片保存和索引维护有独立状态。检索是本地向量召回，不等同于大规模分布式 ANN 服务，也不保证召回片段足以支持最终回答。

**安全边界在执行层，不只在提示词。** 工作区路径检查、expected_text 匹配、审批、原子写入和修改记录提供确定性约束。引用/代码过滤是规则与行状态处理，不是完美的语义理解；整个应用也不是 OS 沙箱，不能据此宣称已完全解决提示注入。

## 动态引导：实现与真实验证

三档为简洁、标准、深入，但允许不命中。引导不负责决定任务“客观难度”，也不保证每条消息都应被分到一档。

- 示例带 `passage:` 前缀，用户窗口带 `query:` 前缀。
- E5 向量归一化，逐档取 top-3 相似度均值；同时要求第一候选得分和对第二候选的分差达标。
- 默认阈值：**相似度 0.835、分差 0.013**。多个合格窗口冲突时不注入；无命中不兜底标准档。
- 推理联动关闭时保留手动档位；打开时使用已支持模型的档位映射，不把三档直接当成通用 API 枚举。

校准使用固定 100 条内部用例：80 条调参，20 条保留验证。只调整两项全局阈值，不改标签、示例、提示词或加特例。

| 指标 | 原阈值，80 条 | 新阈值，80 条 | 新阈值，20 条保留验证 |
| --- | ---: | ---: | ---: |
| 正向正确触发 | 7/48 | 25/48 | 5/12 |
| 负向误触发 | 0/32 | 0/32 | 2/8 |
| 正向错档 | 0 | 0 | 0 |

**不把调参集成绩当成泛化能力。** 新参数没有通过最初的“保留集零误触发”标准；用户了解结果后接受了覆盖与保守性的取舍，决定采用。普通操作要求仍可能触发深入，标准档覆盖也有限。该验证不是外部盲测，不证明引导改善了回答质量。原冻结标签和结果保留。

## 快速开始

### 使用安装版

运行发布附件 `AgentWorkbench-Setup.exe`。标准包包含 Python 应用运行时、本地 E5 权重及离线前端资源；复用系统 WebView2。缺失 WebView2 时，安装器在用户同意后使用微软 Bootstrapper 联网安装。**标准包不是完整离线环境安装包。**

Windows 10 2004（build 19041）或更高版本、x64 环境是安装器约束；WebView2/.NET 要求以安装器检查为准。主程序当前未做可信代码签名，新电脑可能出现系统信誉提示。干净机器兼容性不能仅由本机打包检查证明。

首次打开可先使用离线演示；接入真实模型时，在设置中填写 Base URL、模型和 API Key。密钥交给系统 keyring，普通配置文件不存明文 Key。远端模型请求仍会将当前构造的上下文发给所选服务，不能将“本地存储”理解成“数据永不出网”。

### 从源码运行

以下命令在包含 `agent_workbench/` 的仓库根目录执行。建议使用 Python 3.11 和 Node.js 20+；前者运行应用，后者用于前端测试。Windows 还需要可用的 WebView2 Runtime。

```powershell
py -3.11 -m venv agent_workbench/.venv
$env:TEMP = "$PWD/agent_workbench/.tmp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
& agent_workbench/.venv/Scripts/python.exe -m pip install -r agent_workbench/requirements.txt
& agent_workbench/.venv/Scripts/python.exe agent_workbench/scripts/prepare_model.py
& agent_workbench/.venv/Scripts/python.exe -m agent_workbench.app
```

ONNX 权重不放进普通源码提交；下载脚本锁定 Hugging Face revision 与 SHA-256，已有权重也会校验。其余 tokenizer/config 文件随源码提供。首次下载约 118 MB，模型就绪后路由和本地检索不需要再次下载权重。

开发启动器支持进程环境或仓库根 `.env` 中的 `OPENAI_BASE_URL`、`OPENAI_MODEL`、`OPENAI_API_KEY`，也可直接在界面配置。不要提交真实 `.env`。未配置时使用离线演示，支持：

```text
/list
/read README.md
/grep checkpoint
/edit notes.txt | old text | new text
/kb retrieval
/draft Reusable decision | Keep original history. | Useful in future sessions.
```

这些斜杠命令属于确定性离线演示，不是所有真实模型必须遵守的输入语法。真实模型通过工具 schema 选择调用。

## 测试与打包

```powershell
Push-Location agent_workbench
npm ci --ignore-scripts
npm run test:web
Pop-Location
& agent_workbench/.venv/Scripts/python.exe -m pytest agent_workbench/tests -q
& agent_workbench/.venv/Scripts/python.exe -m agent_workbench.scripts.verify_desktop
& agent_workbench/build.ps1
```

构建机需安装 Inno Setup 6；浏览器回归使用 Microsoft Edge。`build.ps1 -SkipInstaller` 只生成 PyInstaller 目录版。输出位于 `agent_workbench/dist/AgentWorkbench/` 和 `agent_workbench/output/AgentWorkbench-Setup.exe`。

| 层级 | 检查什么 | 不代表什么 |
| --- | --- | --- |
| pytest | 路由、解析、状态、工具、存储、索引、压缩等契约 | 不代表所有提供方真实兼容 |
| Node + Playwright | 页面控件、Markdown 安全、尺寸与交互 | 不代表原生文件选择器已覆盖 |
| 原生 WebView2 | 桌面桥、窗口、托盘、知识流程和真实本地 E5 | fixture 压缩不代表远端摘要质量 |
| 打包后检查 | EXE 启动、数据恢复、重启、单实例和托盘 | 不等于干净电脑矩阵验收 |

本次版本对应的测试和产物记录见 [发布记录](docs/RELEASE-0.4.13.md)。文档截图可用 `scripts/capture_docs.py` 配合 `scripts/capture_docs.cjs` 重建，需要 Playwright；它们只创建独立的合成演示数据。

## 面试讲解路线（约 8 分钟）

1. **定位，1 分钟**：为什么是桌面工作台？展示工作区、聊天和文件读取，说明 Web UI 与原生外壳的关系。
2. **执行控制，2 分钟**：触发文件替换，展示 diff 与批准/拒绝；解释模型提议与确定性执行边界。
3. **记忆与知识，2 分钟**：区分原始历史、压缩摘要、知识卡片和向量索引；展示草稿确认与论文检索来源。
4. **可解释实验，2 分钟**：讲路由的两道阈值、允许弃权、80/20 划分，以及为什么保留失败结果。
5. **产品工程，1 分钟**：展示论文树/列表/阅读器，解释安装包、依赖分发、隐私边界与尚未完成的工作。

常见追问：

- **为什么不使用 Agent 框架？** 当前目标是清楚展示执行循环与状态契约，规模较小；不是证明框架无用，复杂调度仍可能需要框架。
- **为什么不是 Electron？** Python 本地解析与 E5 已经是主体，WebView2 复用系统浏览器运行时；代价是 Windows 环境依赖和跨平台验证成本。
- **为什么不用“最近八条”历史？** 持久化是数据保存策略，上下文裁剪/摘要是模型预算策略，两者不能混为一谈。
- **用户批准后文件被别人改了怎么办？** 再检查预期原文，避免静默覆盖。它不等于对任意外部文件系统竞态的完整防护。
- **相似度很高为何仍不触发？** 不同档位都可能很相似，需要看分差；但阈值只能接受/拒绝候选，不能纠正语义排名。
- **怎么证明不是只做了页面？** 指向运行时、工具、存储与压缩代码，展示回归脚本和打包后的可执行文件。

## 已知限制与后续工作

- 当前是个人桌面 Demo，不包含团队权限、多租户、云同步、远端执行集群或任意 Shell 工具。
- PDF 不带 OCR，Word/Excel 不保证原样排版；图片能力取决于模型接入。
- 路由、引用过滤与论文分类存在误判，不能把内部固定集合的命中率当成普遍能力。
- 上下文估算不一定等于提供方 tokenizer 的精确 token 数；摘要失败不能伪装成完成。
- 3D 在不支持 WebGL 的环境可回退列表，但需要更多 GPU/缩放比例/干净 Windows 环境验证。
- 源码验证工具中部分历史实验脚本保留了原本机路径，只作为内部复现实验记录；GitHub 源码包不包含这些个人环境脚本。

## GitHub 发布与许可证

- 源码与安装包分开：源码提交代码、截图、依赖清单；安装包放 GitHub Releases，不放进源码树。
- `.venv`、`node_modules`、`.tmp`、`build`、`dist`、`output`、运行数据和 `.env` 不提交。ONNX 大权重通过固定下载脚本准备。
- 不提供真实 API Key，不提交用户配置、会话数据库、上传文件、私人路径日志或测试录屏。
- 本项目原创代码采用 [MIT License](LICENSE)，版权署名为 AgentWorkbench contributors。该许可不覆盖第三方组件、模型权重或微软运行时的各自条款，不能将整个捆绑安装包简单视为仅受 MIT 约束。
- 依赖许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。特别注意 PyMuPDF 的 AGPL/商业授权选项及模型、微软运行时的各自条款；本 README 不替代许可审查。
- 本地 E5 权重转换来源为 [Xenova/multilingual-e5-small](https://huggingface.co/Xenova/multilingual-e5-small)，上游模型为 [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small)。其模型卡标注 MIT，发布仍应保留原始说明与适用许可。

**本项目的面试价值在于解释“为什么这样设计、如何验证、哪里仍然不可靠”，而不是把所有功能描述成已经达到生产级。**
