"""Conservative request-only extraction, not a natural-language trust classifier."""
import re
from dataclasses import dataclass, field

from agent_workbench.router.intent import effort_hits


WINDOW_SIZE = 320
OVERLAP = 80
MAX_WINDOWS = 24
MAX_INPUT = 32768
REFERENCE = re.compile(r'^\s*(?:#{1,6}\s*)?(?:(?:以下|下面)(?:是|为)?\s*)?(?:参考(?:资料|材料|内容|文本)?|原文|引用|背景材料|reference(?:s| material| text)?|context|source text)\s*[:：]', re.I)
REQUEST = re.compile(r'^\s*(?:#{1,6}\s*)?(?:我的(?:问题|要求)|本次(?:问题|要求)|请回答|问题|要求|my (?:request|question)|request|question)\s*[:：]', re.I)
REPORTED = re.compile(r'^(?:他|她|他们|作者|文中|原文|对方)(?:说|要求|提到|指出)|^(?:he|she|they|the author)\s+(?:said|says|asked|wrote)', re.I)
AMBIGUOUS = re.compile(r'(?:之前|上次|原先|先前).{0,20}(?:要求|让你)|(?:不要|无需|不用|不必|并非|不是|别).{0,10}(?:详细|深入|简短|简单|快速|全面)|(?:not|never|don.t|without)\s+(?:\w+\s+){0,2}(?:detailed|deep|brief|quick|thorough)|previously (?:asked|requested)', re.I)
HISTORY = re.compile(r'(?:之前|上次|原先|先前).{0,20}(?:要求|让你)|previously (?:asked|requested)', re.I)
CURRENT = re.compile(r'这次|本次|现在|改为|\bthis time\b|\bnow\b|\binstead\b', re.I)
NEGATED = re.compile(r'^(?:(?:这次|本次|现在|也|同时)\s*)?(?:请)?(?:不要|无需|不用|不必|别|并非要|不是要)\s*|^(?:please\s+)?(?:do not|don.t|never)\s+', re.I)
TRANSLATION = re.compile(r'^(?:请)?(?:把|将)?.{0,50}(?:翻译|translate).{0,35}[:：]', re.I)
CONSTRAINT = re.compile(
    r'^(?:请)?(?:(?:不(?:调用|使用|用)|无需(?:调用|使用)?|不要(?:调用|使用)?)(?:任何)?工具|'
    r'(?:不超过|最多|限于|控制在|限制在|字数不超过)\s*\d+\s*(?:字|词|字符|行|段)(?:以内)?|'
    r'(?:用|分成|分|以)\s*[一二三四五六七八九十\d]+\s*(?:段|句|行|字|词)(?:话)?(?:说明|回答|概括|呈现)?|'
    r'(?:不要|不用)写太长|'
    r'(?:do not|don.t|without) (?:use |using |call )?(?:any )?tools|'
    r'(?:at most|within|no more than|under) \d+ (?:words|characters|sentences)|'
    r'keep (?:the answer|the response|it) (?:under|within) \d+ (?:words|characters))\s*[。.!?！？]?$'
    , re.I)


@dataclass
class RequestIntent:
    windows: list[str] = field(default_factory=list)
    excluded: set[str] = field(default_factory=set)
    reason: str | None = None


def request_windows(text: str) -> tuple[list[str], str | None]:
    intent = request_intent(text)
    return intent.windows, intent.reason


def request_intent(text: str) -> RequestIntent:
    if len(text) > MAX_INPUT:
        return RequestIntent(reason='routing_input_limit')
    # Recognized marked blocks are excluded as whole spans, including unclosed tags.
    text = re.sub(r'<(reference|context|source|document|quote|untrusted_text)\b[^>]*>.*?(?:</\1\s*>|\Z)', '\n', text, flags=re.I | re.S)
    text = re.sub(r'<!--.*?(?:-->|\Z)', '\n', text, flags=re.S)
    lines, fence, reference = [], None, False
    excluded = set()
    filtered = False
    for line in text.splitlines():
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            delimiter = marker[1]
            if fence is None:
                fence = delimiter
            elif delimiter[0] == fence[0] and len(delimiter) >= len(fence):
                fence = None
            continue
        if fence or re.match(r'^\s*>', line) or line.startswith(('    ', '\t')):
            continue
        if REFERENCE.match(line):
            reference = True
            continue
        request = REQUEST.match(line)
        if request:
            reference = False
            line = line[request.end():]
        if reference:
            continue
        # Inline code and paired quotes are evidence, not triggering instructions.
        line = re.sub(r'`[^`]*`|“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', '', line)
        if REPORTED.search(line.strip()):
            continue
        translation = TRANSLATION.match(line.strip())
        if translation:
            line = line.strip()[:translation.end()]
        if HISTORY.search(line):
            current = CURRENT.search(line, HISTORY.search(line).end())
            if not current:
                filtered = True
                continue
            line = line[current.end():]
        if line.strip():
            lines.append(line.strip())
    if not lines:
        return RequestIntent(reason='ambiguous_request' if filtered else 'empty_or_context_only')
    windows = []
    for line in lines:
        for sentence in re.split(r'(?<=[。！？!?；;])\s*|(?<=\.)\s+', line):
            if not sentence.strip():
                continue
            clauses = []
            for clause in re.split(r'[，,]|而是|但(?:是)?', sentence):
                clause = clause.strip()
                if not clause:
                    continue
                if CONSTRAINT.fullmatch(clause):
                    filtered = True
                    continue
                negation = NEGATED.match(clause)
                if negation and not clause.startswith(('不用展开', '无需展开')):
                    positive = clause[negation.end():].strip('。.!?！？ ')
                    rejected = effort_hits(positive)
                    if re.search(r'简单(?:地)?下结论|快速(?:地)?下结论', positive):
                        rejected.add('fast')
                    if rejected:
                        excluded.update(rejected)
                        filtered = True
                        continue
                    if not positive:
                        filtered = True
                        continue
                if AMBIGUOUS.search(clause):
                    return RequestIntent(excluded=excluded, reason='ambiguous_request')
                clauses.append(clause)
            sentence = '，'.join(clauses)
            if not sentence:
                continue
            for start in range(0, len(sentence), WINDOW_SIZE - OVERLAP):
                window = sentence[start:start + WINDOW_SIZE]
                if window.strip():
                    windows.append(window)
                if len(windows) > MAX_WINDOWS:
                    return RequestIntent(reason='routing_input_limit')
                if start + WINDOW_SIZE >= len(sentence):
                    break
    return RequestIntent(windows=windows, excluded=excluded,
                         reason=None if windows else 'constraints_only' if filtered else 'empty_or_context_only')
