from moduls.utils.avtortut import avtortut


def sort_sfoto_bezfoto(msg, base):
    if 'attachments' not in msg:
        if len(msg['text']) > 20 and msg['text'] not in base['bezfoto'] and msg['text'] not in base['all_bezfoto']:
            base['bezfoto'].append('&#128073; ' + avtortut(msg))
        msg = []
    return msg, base
