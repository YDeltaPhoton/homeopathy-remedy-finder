#!/usr/bin/env python3
"""Conservative, page-offset-preserving rule-based structuring.

Candidates are not verified facts. A low-confidence remedy heading never
silently establishes a passage-remedy relationship.
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from detect_headings import heading_candidates
from recognize_remedies import classify_heading

ROOT=Path(__file__).resolve().parents[2]
EMPTY=('rubric_remedy','symptom_terms','synonyms','symptom_rubric')

def sid(prefix,*values):
    return prefix+hashlib.sha256('\x1f'.join(map(str,values)).encode()).hexdigest()[:20]

def read_jsonl(path):
    with path.open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)

def write_jsonl(path,items):
    with path.open('w',encoding='utf-8') as f:
        for item in items:f.write(json.dumps(item,ensure_ascii=False)+'\n')

def process(folder,outroot):
    book=json.loads((folder/'book.json').read_text(encoding='utf-8'))
    pages=list(read_jsonl(folder/'cleaned_pages.jsonl'))
    out=outroot/folder.name;out.mkdir(parents=True,exist_ok=True)
    bid=book['book_id']
    raw_candidates=[]
    for page in pages:
        text=page.get('normalized_text','')
        if text.strip():
            for item in heading_candidates(text,(page.get('layout') or {}).get('blocks')):
                raw_candidates.append({**item,'pdf_page':page['pdf_page']})
    frequency=Counter(x['text'].casefold() for x in raw_candidates)
    for item in raw_candidates:
        if frequency[item['text'].casefold()]>=3:item['repeated_heading_candidate']=True
    candidates=[]; passages=[]; remedies={}; rubrics={}; links=[]; rubric_links=[]
    bypage={}
    for item in raw_candidates:bypage.setdefault(item['pdf_page'],[]).append(item)
    doc_type='repertory' if 'repertor' in (book.get('title') or '').casefold() else 'unknown'
    # Never propagate an uncertain entity across pages automatically.
    for page in pages:
        number=page['pdf_page'];text=page.get('normalized_text','')
        if not text.strip():continue
        headings=sorted(bypage.get(number,[]),key=lambda h:h['start_offset'])
        classified=[]
        for h in headings:
            kind,confidence=classify_heading(h,doc_type)
            classified.append({**h,'classification':kind,'confidence':confidence})
        # Partition at all heading candidates; all boundaries remain auditable.
        boundaries=sorted(set([0,len(text)]+[h['start_offset'] for h in classified]))
        for i,(start,end) in enumerate(zip(boundaries,boundaries[1:]),1):
            segment=text[start:end]
            if not segment.strip():continue
            pid=sid('P',bid,number,start,end)
            at_start=next((h for h in classified if h['start_offset']==start),None)
            passages.append({'passage_id':pid,'book_id':bid,'original_text':None,
                             'normalized_text':segment,'chapter':None,'page':None,
                             'pdf_page':number,'source_page_start_offset':start,
                             'source_page_end_offset':end,'source_status':'ocr_unverified',
                             'verification_status':'inferred','extraction_method':'rules',
                             'remedy_scope_status':'ambiguous'})
            if at_start:
                cid=sid('H',bid,number,start,at_start['text'])
                candidates.append({'candidate_id':cid,'passage_id':pid,'book_id':bid,
                                   'pdf_page':number,'text':at_start['text'],
                                   'classification':at_start['classification'],
                                   'confidence':at_start['confidence'],
                                   'start_offset':at_start['start_offset'],
                                   'end_offset':at_start['end_offset'],
                                   'verification_status':'needs_review',
                                   'extraction_method':'rules'})
                if at_start['classification']=='remedy_candidate':
                    rid=sid('M',bid,at_start['text'].casefold())
                    remedies.setdefault(rid,{'remedy_id':rid,'canonical_name':at_start['text'],
                        'source_book_id':bid,'verification_status':'inferred',
                        'evidence_candidate_id':cid})
                    # Candidate alone does not establish the whole passage's ownership.
                elif at_start['classification']=='rubric_candidate':
                    rid=sid('R',bid,at_start['text'].casefold())
                    rubrics.setdefault(rid,{'rubric_id':rid,'rubric_name':at_start['text'],
                        'parent_rubric_id':None,'verification_status':'inferred',
                        'evidence_candidate_id':cid})
    tables={'books':[book],'passages':passages,'heading_candidates':candidates,
            'remedies':list(remedies.values()),'rubrics':list(rubrics.values()),
            'passage_remedy':links,'passage_rubric':rubric_links}
    for name in EMPTY:tables[name]=[]
    for name,items in tables.items():write_jsonl(out/f'{name}.jsonl',items)
    report={'book_id':bid,'passages':len(passages),'heading_candidates':len(candidates),
            'inferred_remedies':len(remedies),'inferred_rubrics':len(rubrics),
            'inferred_passage_remedy_links':len(links),
            'warning':'Candidate headings are unverified. No passage-remedy links are assigned without reliable scope evidence.'}
    (out/'structure_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'{folder.name}: {len(passages)} passages, {len(remedies)} remedy candidates, {len(candidates)} headings; links withheld pending review')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,default=ROOT/'data/processed')
    parser.add_argument('--out',type=Path,default=ROOT/'data/structured_v2')
    args=parser.parse_args()
    if not args.input.exists():parser.error(f'Input not found: {args.input}')
    folders=[args.input] if (args.input/'cleaned_pages.jsonl').is_file() else sorted(x for x in args.input.iterdir() if x.is_dir())
    for folder in folders:
        if (folder/'cleaned_pages.jsonl').is_file():process(folder,args.out)

if __name__=='__main__':main()
