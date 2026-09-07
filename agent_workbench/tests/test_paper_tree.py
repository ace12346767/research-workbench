import asyncio
import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from agent_workbench.core.models import ProviderChunk
from agent_workbench.knowledge.paper_analysis import grounded_metadata
from agent_workbench.server.api import create_app
from agent_workbench.tests.test_approval_flow import make_runtime


def pdf_bytes(text='Fixture Paper\n2024\nTest Journal\nA study of retrieval methods.'):
    with pymupdf.open() as pdf:
        page = pdf.new_page(); page.insert_text((72, 72), text)
        return pdf.tobytes()


def result():
    return {'title':'Fixture Paper','year':2024,'year_type':'published','venue':'Test Journal',
            'topic':'Retrieval','subtopic':'Dense retrieval','reason':'Studies retrieval methods',
            'evidence':{'title':{'page':1,'quote':'Fixture Paper'},'year':{'page':1,'quote':'2024'},
                        'venue':{'page':1,'quote':'Test Journal'}}}


class Classifier:
    async def stream(self, messages, tools, cancel_event):
        assert not tools
        yield ProviderChunk(type='content',delta=json.dumps(result()))
        yield ProviderChunk(type='usage',usage={'prompt_tokens':120,'completion_tokens':60})
        yield ProviderChunk(type='finish',finish_reason='stop')


def test_bibliographic_fields_require_exact_page_evidence():
    raw=result();raw['venue']='Invented Conference';raw['doi']='10.123/fake'
    output=grounded_metadata(raw,[{'page':1,'text':'Fixture Paper\n2024\nTest Journal'}])
    assert output['title']=='Fixture Paper' and output['year']==2024
    assert output['venue']=='' and output['doi']=='' and output['topic']=='Retrieval'


def test_paper_details_rendering_and_manual_edit_revision(tmp_path):
    runtime=make_runtime(tmp_path)
    with TestClient(create_app(runtime)) as client:
        data=pdf_bytes()
        response=client.post('/api/kb/papers',content=data,headers={'content-type':'application/pdf','x-filename':'paper.pdf'})
        assert response.status_code==200,response.text
        key=response.json()['paper_id']
        assert client.get(f'/api/kb/papers/{key}/content').content==data
        assert 'Fixture Paper' in client.get(f'/api/kb/papers/{key}/pages/1').json()['text']
        image=client.get(f'/api/kb/papers/{key}/pages/1?image=true')
        assert image.content.startswith(b'\x89PNG')
        assert client.get(f'/api/kb/papers/{key}/pages/0').status_code==409
        edit=client.put(f'/api/kb/papers/{key}/metadata',json={'revision':0,'metadata':{'title':'Manual title','year':2023,'topic':'Human topic'}})
        assert edit.status_code==200,edit.text
        assert edit.json()['manual_fields']==['title','topic','year']
        assert client.put(f'/api/kb/papers/{key}/metadata',json={'revision':0,'metadata':{}}).status_code==409
        runtime.papers.accept_classification(key,grounded_metadata(result(),[{'page':1,'text':'Fixture Paper 2024 Test Journal'}]),{})
        saved=client.get(f'/api/kb/papers/{key}').json()
        assert saved['metadata']['title']=='Manual title' and saved['metadata']['year']==2023
        assert saved['metadata']['topic']=='Human topic'
        assert 'title' not in saved['metadata']['evidence']
        assert client.get('/api/kb/papers/not-found/pages/1').status_code==404


def test_classification_jobs_confirm_persist_usage_and_keep_originals(tmp_path):
    async def scenario():
        runtime=make_runtime(tmp_path,Classifier())
        original=pdf_bytes();record=await runtime.import_pdf('paper.pdf',original);key=record['paper_id']
        with pytest.raises(ValueError,match='Confirm'):
            runtime.start_paper_classification(key)
        runtime.start_paper_classification(key,confirmed=True)
        await runtime.paper_classification_tasks[key]
        saved=runtime.papers.get(key)
        assert saved.classification_status=='ready',saved.classification_error
        assert saved.metadata['year']==2024 and saved.classification_usage['prompt_tokens']==120
        assert runtime.paper_path(key).read_bytes()==original
        assert runtime.conversation_snapshot()['turns']==[]
        assert runtime.paper_classification_active is None
    asyncio.run(scenario())


def test_failed_mock_and_cancelled_queue_leave_pdf_and_manual_values(tmp_path):
    async def scenario():
        runtime=make_runtime(tmp_path)
        key=(await runtime.import_pdf('paper.pdf',pdf_bytes()))['paper_id']
        runtime.start_paper_classification(key,confirmed=True)
        await runtime.paper_classification_tasks[key]
        assert runtime.papers.get(key).classification_status=='failed'
        runtime.start_paper_classification(key,confirmed=True)
        await runtime.cancel_paper_classification(key)
        assert runtime.papers.get(key).classification_status=='cancelled'
        assert runtime.paper_path(key).exists() and not runtime.paper_classification_tasks
    asyncio.run(scenario())


def test_selected_paper_search_filters_before_ranking(tmp_path):
    async def scenario():
        runtime=make_runtime(tmp_path)
        first=(await runtime.import_pdf('one.pdf',pdf_bytes('First evidence')))['paper_id']
        second=(await runtime.import_pdf('two.pdf',pdf_bytes('Second evidence')))['paper_id']
        results=await runtime.knowledge.search('evidence',source_paths=runtime.paper_sources([second]))
        assert results and all('Second' in r.text for r in results)
        assert await runtime.knowledge.search('evidence',source_paths=[])==[]
        assert runtime.paper_sources([first]) != runtime.paper_sources([second])
    asyncio.run(scenario())


def test_auto_classification_is_opt_in_and_topic_merge_is_atomic(tmp_path):
    async def scenario():
        runtime=make_runtime(tmp_path,Classifier())
        first=(await runtime.import_pdf('first.pdf',pdf_bytes()))['paper_id']
        assert not runtime.paper_classification_tasks
        runtime.config.auto_classify_papers=True
        second=(await runtime.import_pdf('second.pdf',pdf_bytes('Fixture Paper\n2024\nTest Journal\nAdditional evidence')))['paper_id']
        await runtime.paper_classification_tasks[second]
        assert runtime.papers.get(second).classification_status=='ready'
        runtime.papers.edit_metadata(first,{'topic':'Manual category'},0)
        first_slot=runtime.papers.get(first).topic_slot
        second_slot=runtime.papers.get(second).topic_slot
        assert first_slot!=second_slot
        runtime.papers.rename_topic('Manual category','Retrieval')
        assert runtime.papers.get(first).topic_slot==second_slot
        assert 'topic' in runtime.papers.get(first).manual_fields
        assert runtime.papers.get(second).topic_slot==second_slot
    asyncio.run(scenario())


@pytest.mark.parametrize('kind',['tool','truncated','invalid'])
def test_classification_rejects_unusable_responses_without_erasing_metadata(tmp_path,kind):
    class BadProvider:
        async def stream(self,messages,tools,cancel_event):
            if kind=='tool':
                from agent_workbench.core.models import ToolCall
                yield ProviderChunk(type='tool_call',tool_call=ToolCall(call_id='bad',name='edit_file',arguments={}))
            yield ProviderChunk(type='content',delta='not json' if kind=='invalid' else json.dumps(result()))
            yield ProviderChunk(type='finish',finish_reason='length' if kind=='truncated' else 'stop')
    async def scenario():
        runtime=make_runtime(tmp_path,BadProvider())
        key=(await runtime.import_pdf('paper.pdf',pdf_bytes()))['paper_id']
        runtime.papers.edit_metadata(key,{'title':'Human verified'},0)
        runtime.start_paper_classification(key,confirmed=True)
        await runtime.paper_classification_tasks[key]
        assert runtime.papers.get(key).classification_status=='failed'
        assert runtime.papers.get(key).metadata['title']=='Human verified'
        assert runtime.paper_path(key).exists()
    asyncio.run(scenario())


def test_scope_rejects_access_to_other_paper_tools_and_missing_ids(tmp_path):
    from agent_workbench.tools.registry import ToolRegistry
    async def scenario():
        runtime=make_runtime(tmp_path)
        first=(await runtime.import_pdf('first.pdf',pdf_bytes('First')))['paper_id']
        second=(await runtime.import_pdf('second.pdf',pdf_bytes('Second')))['paper_id']
        registry=runtime.build_tools([first])
        with pytest.raises(ValueError,match='outside'):
            await registry.execute('read_paper',{'paper_id':second,'page':1})
        with pytest.raises(FileNotFoundError):
            runtime.paper_sources(['missing'])
    asyncio.run(scenario())


def test_legacy_manifest_migration_preserves_original_and_backups(tmp_path):
    from agent_workbench.knowledge.papers import PaperManager
    manager=PaperManager(tmp_path/'papers');data=pdf_bytes();record,_=manager.stage('旧论文.pdf',data)
    legacy=json.loads(manager.manifest.read_text(encoding='utf-8'))
    for item in legacy:
        for key in ('metadata','manual_fields','revision','classification_status','classification_error','classification_usage','topic_slot'):
            item.pop(key)
    original=json.dumps(legacy,ensure_ascii=False).encode('utf-8');manager.manifest.write_bytes(original)
    assert manager.get(record.paper_id).metadata=={}
    manager.edit_metadata(record.paper_id,{'title':'Corrected title','topic':'Human topic'},0)
    assert (manager.papers_dir/'manifest.pre-v15.json').read_bytes()==original
    assert manager.get(record.paper_id).filename=='旧论文.pdf'
    assert manager.get(record.paper_id).topic_slot==0
