"""Bounded imperative patterns, not a general natural-language intent classifier."""
import re


PREFIX = r'(?:^|[\s，,：:；;。.!?！？])(?:请(?:你)?|帮我|为我|我(?:希望|需要|要求)你|这次|本次|现在|please\s+)?\s*'
FAST_RE = re.compile(PREFIX + r'(?:简单回答|快速回答|简要(?:回答|说明|介绍)|简短(?:回答|说明)?|不用展开|无需展开|'
                     r'(?:只|直接)给(?:出)?结论|直接告诉(?:我)?(?:结论|答案)|只(?:给|输出)(?:最终)?(?:答案|结果)|'
                     r'be brief\b|answer briefly\b|give (?:only )?(?:a )?(?:short|concise|brief)(?: direct)? (?:answer|response)\b)', re.I)
STANDARD_RE = re.compile(PREFIX + r'(?:按(?:正常|标准|通常)(?:专业程度|详细程度|程度|专业标准)回答|'
                         r'按日常工程答疑的程度|(?:给我|给出|提供).{0,12}(?:能|可以)照着做的方案|'
                         r'(?:解释|说明|讲解|给出).{0,45}步骤.{0,20}(?:举例|例子|示例|验证|检查)|'
                         r'use normal professional diligence\b|explain .{0,35}steps .{0,30}(?:example|verify|verification)\b)', re.I)
STANDARD_MODE_RE = re.compile(PREFIX + r'(?:按(?:正常|标准|通常)(?:专业程度|详细程度|程度|专业标准)回答|按日常工程答疑的程度|use normal professional diligence\b)', re.I)
DEEP_RE = re.compile(PREFIX + r'(?:深入(?:分析|思考|研究|检查|探讨)|详细(?:分析|说明|解释)|'
                     r'全面(?:分析|检查)|严谨(?:分析|推导|论证)|仔细(?:分析|检查|推导)|逐步推导|'
                     r'(?:analy[sz]e|review|examine|investigate|think|reason|explain)\s+(?:(?:this|it)\s+)?'
                     r'(?:deeply|thoroughly|rigorously|in[ -]depth)\b)', re.I)
PATTERNS = {'fast': FAST_RE, 'standard': STANDARD_RE, 'deep': DEEP_RE}
SUBJECT_SUFFIX = re.compile(r'^\s*(?:是什么|是什么意思|指什么|一词|这个词|怎么翻译|的(?:含义|定义))')


def effort_hits(text):
    # Contrast/joining words delimit competing directives, not a last-clause-wins rule.
    text = re.sub(r'但(?:是)?|而是|同时|并且', '，', text)
    found = set()
    for tier, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            if not SUBJECT_SUFFIX.match(text[match.end():]):
                found.add(tier)
    # Asking for steps/examples also fits a deep answer; only an explicit standard
    # mode is a conflicting tier request in that case.
    if 'deep' in found and not STANDARD_MODE_RE.search(text):
        found.discard('standard')
    return found
