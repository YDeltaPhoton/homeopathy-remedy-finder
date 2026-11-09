"""Helpers for checking page text ordering."""
def layout_warnings(layout):
    warnings=[]
    if layout.get('layout_type')=='possible_spread':
        warnings.append('Possible two-page spread: verify left/right reading order')
    if not layout.get('blocks'):
        warnings.append('No positioned text blocks; OCR or image extraction may be needed')
    return warnings
