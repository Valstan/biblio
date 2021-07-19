import hashlib

from PIL import Image

from config import bases, fimage
from moduls.read_write.get_image import image_get
from moduls.sort.sort_black_list import sort_black_list
from moduls.utils.tesseract import tesseract


def sort_po_foto(msg, base):
    histo = ''
    if msg['attachments'][0]['type'] == 'photo' and\
            image_get(msg['attachments'][0]['photo']['sizes'][0]['url']):
        image = Image.open(bases + fimage)
        histo = image.histogram()
        hash_object = hashlib.md5(str(histo).encode())
        histo = hash_object.hexdigest()
        if histo in base['hash']:
            return [], ''
        if sort_black_list(tesseract(bases + fimage)):
            return [], histo

    return msg, histo


if __name__ == '__main__':
    pass
