"""Three built Docker services share registered paths; no network or model calls.

Run from the integration root with pymupdf installed. Containers and fixtures
are isolated; existing research sessions and running services are not modified.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile

import fitz
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def probe(root: Path):
    workspace = root / 'workspace'
    registry = root / 'registry'
    registry.mkdir(mode=0o755)
    session_dir = workspace / 'registry_probe'
    folder = session_dir / 'results/manuscript'
    folder.mkdir(parents=True)
    (workspace / '.paper_registry').mkdir()
    (folder / 'article.tex').write_text('\\section{Introduction}\nEvidence\n\\section{Method}\n\\section{Results}\n')
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), 'Introduction', fontsize=16)
    page.insert_text((72, 150), 'A local document-registration check.')
    doc.save(folder / 'article.pdf')
    doc.close()

    def container(image, code=None, *, readonly=False, user=None):
        args = ['run', '--rm', '-i', '--network', 'none', '-e', 'WORKSPACE_DIR=/workdir',
                '-e', 'ENABLE_PHOENIX=false', '-e', 'WRITER_CHECK_STATE_DB_PATH=/tmp/checks.sqlite3',
                '-v', f'{workspace}:/workdir' + (':ro' if readonly else ''),
                '-v', f'{registry}:/workdir/.paper_registry' + (':ro' if user else '')]
        if user:
            args += ['--user', user]
        args += ['ai-researcher-claude-' + image, 'python']
        if code is not None:
            args += ['-c', code]
        return args

    params = StdioServerParameters(command='docker', args=container('writer-tools', readonly=True) + [
        '-m', 'holosophos.tools.writer.mcp_server', '--transport', 'stdio'])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()

            async def call(name, args):
                result = await client.call_tool(name, args)
                assert not result.isError, str(result.content)
                data = result.structuredContent
                return data.get('result', data)

            identity = {'session_id': 'registry_probe', 'agent_name': 'writer'}
            started = await call('start_writer_check', {**identity, 'tex_path': 'results/manuscript/article.tex', 'pdf_path': 'results/manuscript/article.pdf'})
            common = {**identity, 'check_run_id': started['check_run_id'], 'pass_number': 1}
            for tool in ('section_order', 'content_placement', 'paper_length'):
                await call(tool, common)
            finished = await call('finish_writer_check', {**identity, 'check_run_id': started['check_run_id'], 'outcome': 'clean'})
            assert finished['status'] == 'clean'

    review_code = '''
import asyncio
from academia_mcp.tools import review
async def fixed_model(**kwargs):
    aspect = dict(strengths=[], weaknesses=[], reasoning='Fixture', score=3)
    return review.ReviewResponse.model_validate(dict(summary='Integration fixture', quality=aspect, clarity=aspect, significance=aspect, originality=aspect, questions=[], limitations=[], overall=dict(reasoning='Fixture', score=5), confidence=dict(reasoning='Fixture', score=4), format_issues=[]))
review.llm_acall_structured = fixed_model
asyncio.run(review.review_pdf_paper('registry_probe/results/manuscript/article.pdf'))
print('Academia review saved using registration')
'''
    subprocess.run(['docker', *container('academia', review_code)], check=True, capture_output=True, text=True, timeout=60)
    ui_code = '''
from pathlib import Path
from holosophos.ui_api import list_artifacts, build_tab_summaries
from research_paper_registry import PaperRegistry
root = Path('/workdir/registry_probe')
summary = build_tab_summaries(root, list_artifacts(root))
assert summary['paper']['pdfs'] == ['results/manuscript/article.pdf'], summary['paper']
assert summary['paper']['document']['status'] == 'ready'
assert summary['review']['overall_score'] == 5, summary['review']
assert (root / 'review.md').is_file()
try:
    PaperRegistry(root.parent).register(root.name, 'forbidden')
except OSError:
    pass
else:
    raise AssertionError('UI unexpectedly wrote to registry')
print('UI reads article and review; registry is read-only')
'''
    subprocess.run(['docker', *container('ui-api', ui_code, readonly=True, user='1000:1000')], check=True, capture_output=True, text=True, timeout=60)
    print(json.dumps({'passed': True, 'services': ['writer-tools MCP stdio', 'Academia mocked reviewer', 'UI API read-only'], 'model_calls': 0}))


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='paper-registry-containers-') as directory:
        asyncio.run(probe(Path(directory)))
