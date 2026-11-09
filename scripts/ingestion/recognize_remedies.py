"""Conservative candidate classification, not clinical validation."""
import re

SECTION_WORDS={'allgemeines','kopf','augen','ohren','nase','mund','hals','magen','bauch','rücken','haut','schlaf','fieber','gemüt','extremitäten','besser','schlimmer','modalitäten','generalities','head','eyes','ears','nose','mouth','throat','stomach','abdomen','skin','sleep','fever','mind','introduction','vorwort','inhalt','index','register'}

def classify_heading(candidate, document_type='unknown'):
    text=candidate['text'].strip(); words=text.split(); norm=text.casefold().strip(' :.')
    if candidate.get('repeated_heading_candidate'):
        return 'running_header_candidate',0.2
    if norm in SECTION_WORDS:
        return 'section_candidate',0.7
    if re.search(r'\.{3,}\s*\d+$',text) or re.search(r'\s+\d{1,4}$',text):
        return 'toc_candidate',0.3
    if re.fullmatch(r'[A-ZÄÖÜ]',text):return 'chapter_candidate',0.6
    if document_type=='repertory':return 'rubric_candidate',0.4
    # No all-uppercase requirement. These are unverified candidate names.
    if 1<=len(words)<=4 and all(re.match(r'^[A-ZÄÖÜa-zäöüß]',w) for w in words) and not re.search(r'\d',text):
        return 'remedy_candidate',0.45
    return 'section_candidate',0.35
