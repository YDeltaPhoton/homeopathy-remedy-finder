"""Conservative, layout-aware PyMuPDF text extraction.

No image OCR is performed here. Coordinates refer to the PDF page.
Reading order is inferred and must be reviewed for complex layouts.
"""
from statistics import median

def extract_layout(page):
    raw = page.get_text('dict')
    width, height = page.rect.width, page.rect.height
    blocks = []
    for block in raw.get('blocks', []):
        if block.get('type') != 0: continue
        lines=[]; sizes=[]
        for line in block.get('lines', []):
            spans=[s for s in line.get('spans', []) if s.get('text','').strip()]
            if spans:
                lines.append(''.join(s['text'] for s in spans))
                sizes.extend(float(s.get('size',0)) for s in spans)
        text='\n'.join(lines).strip()
        if text:
            blocks.append({'text':text,'bbox':list(map(float,block['bbox'])),
                           'font_size':round(median(sizes),2) if sizes else None})
    # A central gutter is evidence, not proof, of a two-page spread.
    left=[b for b in blocks if b['bbox'][2] < width*.49]
    right=[b for b in blocks if b['bbox'][0] > width*.51]
    crossing=[b for b in blocks if b not in left and b not in right]
    split=(len(left)>=3 and len(right)>=3 and
           len(crossing)<=max(2,int(.15*len(blocks))))
    if split:
        ordered=sorted(left,key=lambda b:(b['bbox'][1],b['bbox'][0]))+sorted(right,key=lambda b:(b['bbox'][1],b['bbox'][0]))+sorted(crossing,key=lambda b:(b['bbox'][1],b['bbox'][0]))
    else:
        ordered=sorted(blocks,key=lambda b:(round(b['bbox'][1]/8),b['bbox'][0],b['bbox'][1]))
    for i,b in enumerate(ordered): b['order']=i
    return {'layout_type':'possible_spread' if split else 'unknown_or_single',
            'layout_confidence':'heuristic', 'page_width':width,'page_height':height,
            'blocks':ordered, 'reading_order_review_required':True}
