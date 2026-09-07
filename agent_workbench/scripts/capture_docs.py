"""Launch a private native desktop with explicitly synthetic README fixtures."""
import asyncio
import json
import os
import sys
import threading
import time
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
import pymupdf
from agent_workbench.app import find_available_port, run_desktop
from agent_workbench.bootstrap import build_runtime
from agent_workbench.providers.mock import MockProvider
from agent_workbench.core.models import ProviderChunk


class ScreenshotProvider(MockProvider):
    """Presentation fixture, not a real-model quality demonstration."""
    async def stream(self, messages, tools, cancel_event):
        if messages[-1].get('role') == 'tool' and 'Architecture' in str(messages[-1].get('content')):
            yield ProviderChunk(type='content', delta='## 工作区概览\n\n已读取演示项目的 `README.md`。\n\n| 层次 | 职责 |\n| --- | --- |\n| Desktop | 原生窗口、托盘与单实例 |\n| Runtime | 会话、工具调用与审批 |\n| Knowledge | PDF 索引、卡片和检索 |\n\n### 建议的演示顺序\n\n1. 查看文件，再展示带差异预览的写入审批。\n2. 将可复用结论保存为知识卡片。\n3. 打开论文时间树，按主题和年份定位材料。\n\n> 此回答由截图专用离线 fixture 生成，仅展示实际界面，不作为模型能力证据。')
            yield ProviderChunk(type='finish', finish_reason='stop')
            return
        async for chunk in super().stream(messages, tools, cancel_event):
            yield chunk


async def seed(runtime, workspace):
    (workspace/'README.md').write_text('# Architecture Demo\n\nDesktop -> Runtime -> Knowledge\n\nThis public sample contains no personal data.\n', encoding='utf-8')
    (workspace/'settings.json').write_text('{"mode":"review","max_steps":8}\n', encoding='utf-8')
    (workspace/'notes.md').write_text('# Design notes\n\nKeep the original history. Review writes before committing.\n', encoding='utf-8')
    for i, (title, topic) in enumerate([
        ('Retrieval Foundations', '检索与记忆'), ('Grounded Answers', '检索与记忆'), ('Long Context Notes', '检索与记忆'),
        ('Tool Calling Basics', '工具与安全'), ('Reviewed File Updates', '工具与安全'), ('Traceable Agent Actions', '工具与安全'),
        ('Semantic Preferences', '路由与评测'), ('Abstention in Practice', '路由与评测'), ('Threshold Calibration', '路由与评测'),
    ]):
        doc=pymupdf.open(); page=doc.new_page()
        page.insert_text((54,72), 'DEMO / SYNTHETIC DOCUMENT', fontsize=13, color=(.1,.5,.5))
        page.insert_text((54,120), title, fontsize=24)
        page.insert_textbox(pymupdf.Rect(54,170,540,730),f'Year: {2018+i}\nSource: Demo Collection\n\nAbstract\nThis document is a generated presentation fixture, not a published research paper.\n\n1. Motivation\nReliable desktop agents need traceable state, bounded tools and clear user intent.\n\n2. Implementation\nUse local retrieval with source references. Preserve original conversation history.\n\n3. Limitations\nSynthetic examples do not demonstrate model accuracy or research findings.',fontsize=12)
        blob=doc.tobytes();doc.close()
        record=await runtime.import_pdf(f'{2018+i}-{title.replace(" ","-")}.pdf',blob)
        from agent_workbench.knowledge.paper_analysis import PaperMetadata
        meta=PaperMetadata(title=title, year=2018+i, venue='Demo Collection',topic=topic,authors=['Demo Author'],tags=['synthetic','showcase'])
        runtime.papers.edit_metadata(record['paper_id'],meta.model_dump(),0)


def main():
    (ROOT/'.tmp').mkdir(exist_ok=True)
    private=Path(tempfile.mkdtemp(prefix='docs-showcase-',dir=ROOT/'.tmp'))
    os.environ['TEMP']=os.environ['TMP']=str(private)
    from agent_workbench.config import AppConfig, ConfigRepository
    ConfigRepository(private/'data/config.json').save(AppConfig(context_window=131072,max_output_tokens=8192))
    runtime=build_runtime(data_dir=private/'data',environment={})
    runtime.provider=ScreenshotProvider()
    workspace=private/'demo-workspace';workspace.mkdir(exist_ok=True)
    runtime.set_workspace(workspace)
    asyncio.run(seed(runtime,workspace))
    port,debug=find_available_port(),find_available_port()
    os.environ['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS']=f'--remote-debugging-port={debug}'
    session={'port':port,'debug_port':debug,'pid':os.getpid(),'data_dir':str(private)}
    (ROOT/'.tmp/docs-capture-session.json').write_text(json.dumps(session),encoding='utf-8')
    print(f'Documentation desktop: {port}, CDP: {debug}',flush=True)
    def finish():
        import webview
        deadline=time.monotonic()+3600
        while time.monotonic()<deadline and not (private/'finished').exists():time.sleep(1)
        if webview.windows:
            window=webview.windows[0]
            if hasattr(window,'_desktop_tray'):window._desktop_tray.controller.exiting=True
            window.destroy()
    threading.Thread(target=finish,daemon=True).start()
    run_desktop(runtime,port)


if __name__=='__main__':main()
