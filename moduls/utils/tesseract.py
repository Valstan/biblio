import cv2
import pytesseract


# pytesseract.pytesseract.tesseract_cmd = 'c:\\tesseract_orc\\tesseract.exe'

def tesseract(patch):
    img = cv2.imread(patch)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    custom_config = r'--oem 3 --psm 6'
    try:
        return pytesseract.image_to_string(img, lang='rus', config=custom_config)
    except:
        return 'пусто'
