import pytesseract

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)

text = pytesseract.image_to_string(
    "test.jpg",
    lang="vie+eng"
)

print("===== KẾT QUẢ OCR =====")
print(text)
