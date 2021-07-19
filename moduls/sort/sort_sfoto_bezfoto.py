import random

from config import pointer
from moduls.utils.avtortut import avtortut


def sort_sfoto_bezfoto(msg, base):
    if 'attachments' not in msg:
        if len(msg['text']) > 20 and msg['text'] not in base['bezfoto'] and msg['text'] not in base['all_bezfoto']:
            random.shuffle(pointer)
            base['bezfoto'].append(pointer[0] + ' ' + avtortut(msg))
        msg = []
    return msg, base
