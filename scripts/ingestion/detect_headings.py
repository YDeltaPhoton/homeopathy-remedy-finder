"""Find standalone heading candidates using textual and layout evidence."""
import re
from collections import Counter

def heading_candidates(text, blocks=None):
    lines=text.splitlines(keepends=True)
    offsets=[]; pos=0
    for line in lines: offsets.append(pos); pos+=len(line)
    sizes=[b.get('font_size') for b in (blocks or []) if b.get('font_size')]
    baseline=sorted(sizes)[len(sizes)//2] if sizes else None
    result=[]
    for i,line in enumerate(lines):
        s=line.strip()
        if not (3<=len(s)<=90) or len(s.split())>10 or s[-1:] in ('.',',',';','?','!'):continue
        if not re.search(r'[A-Za-zÄÖÜäöüß]',s):continue
        before=i==0 or not lines[i-1].strip()
        after=i==len(lines)-1 or not lines[i+1].strip()
        # Do not require uppercase; typography is only supporting evidence.
        titlecase=bool(re.match(r'^[A-ZÄÖÜ][\wÄÖÜäöüß-]*(?:\s+[a-zA-ZÄÖÜäöüß][\wÄÖÜäöüß-]*){0,5}$',s))
        upper=s.isupper() and len(s.split())<=6
        if not (before or after or upper or titlecase):continue
        start=offsets[i]+len(line)-len(line.lstrip())
        result.append({'text':s,'start_offset':start,'end_offset':start+len(s),
                       'format_kind':'uppercase' if upper else 'mixed_case',
                       'confidence':0.5 if (before or after) else 0.3})
    counts=Counter(x['text'].casefold() for x in result)
    for item in result:
        if counts[item['text'].casefold()]>=3:
            item['repeated_heading_candidate']=True
    return result
