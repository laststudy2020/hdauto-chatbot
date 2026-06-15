from pypdf import PdfReader
reader = PdfReader('manuals/MR-J4-A_기술자료집.pdf')
text = reader.pages[0].extract_text()
print(repr(text[:300]) if text else 'TEXT_EMPTY')