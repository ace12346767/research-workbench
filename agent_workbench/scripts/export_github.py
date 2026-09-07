"""Create a source-only archive without the parent learning repo or private data."""
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git','.venv','node_modules','.tmp','build','dist','output','__pycache__','.pytest_cache','runs'}
SECRET = re.compile(r'(?:sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def main():
    out=ROOT/'output';out.mkdir(exist_ok=True)
    target=out/'AgentWorkbench-GitHub-Source.zip'
    entries={}
    for directory, dirs, files in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in SKIP and not d.startswith('runtime-data') and not (Path(directory)/d).is_symlink()]
        for name in files:
            source=Path(directory)/name
            rel=source.relative_to(ROOT)
            if source.is_symlink() or source.suffix in {'.onnx','.pyc','.sqlite3','.download'} or name.startswith('.env'):
                continue
            if rel.parts[:2]==('scripts','validation'):
                continue
            if source.stat().st_size >= 100*1024*1024:
                raise RuntimeError(f'Oversized source asset: {rel}')
            blob=source.read_bytes()
            if source.suffix in {'.py','.js','.cjs','.json','.md','.txt','.ps1','.html','.css','.iss','.spec'}:
                if SECRET.search(blob.decode('utf-8',errors='replace')):
                    raise RuntimeError(f'Credential-shaped content needs review: {rel}')
            entries['agent_workbench/'+rel.as_posix()]=blob
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    def rebase(match):
        destination=match.group(1)
        return ']('+('agent_workbench/'+destination if not re.match(r'\w+://|#',destination) else destination)+')'
    entries['README.md']=re.sub(r'\]\(([^)]+)\)',rebase,readme).encode('utf-8')
    entries['LICENSE']=(ROOT/'LICENSE').read_bytes()
    entries['.gitignore']=b'.env\n.env.*\n!.env.example\n__pycache__/\n*.pyc\n'
    entries['.env.example']=b'OPENAI_BASE_URL=https://api.example.com/v1\nOPENAI_MODEL=your-model-id\nOPENAI_API_KEY=\n'
    for name,blob in list(entries.items()):
        if name.endswith('.md'):
            text=blob.decode('utf-8',errors='replace')
            for link in re.findall(r'\]\(([^)]+)\)',text):
                if re.match(r'\w+://|#',link):continue
                resolved=os.path.normpath(str(Path(name).parent/link.split('#')[0])).replace('\\','/')
                if not (resolved in entries or any(k.startswith(resolved.rstrip('/')+'/') for k in entries)):
                    # Historical docs mention omitted internal evidence; public READMEs must resolve.
                    if name in {'README.md','agent_workbench/README.md'}:
                        raise RuntimeError(f'Broken public README link: {name}: {link}')
    manifest={name:{'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()} for name,blob in entries.items()}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,blob in sorted(entries.items()):archive.writestr(name,blob)
        archive.writestr('SOURCE-MANIFEST.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name,metadata in manifest.items():assert hashlib.sha256(archive.read(name)).hexdigest()==metadata['sha256']
    report={'archive':target.name,'files':len(entries)+1,'bytes':target.stat().st_size,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'excluded':['parent learning project','private env/data/logs','build/runtime dependencies','ONNX weight','personal validation scripts'],
            'secret_scan':'No matching credential/private-key signatures; heuristic, not a complete security guarantee.',
            'readme_links':'all local README links resolve within archive'}
    (out/'github-export-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
