from bin.utils.driver_tables import load_table, save_table
from bin.utils.avtortut import avtortut


def sort_sfoto_bezfoto(session, msg):
    session['bezfoto'] = load_table('bezfoto')
    session['all_bezfoto'] = load_table('all_bezfoto')
    data_string = " ".join(session['bezfoto']['lip'] + session['all_bezfoto']['lip'])
    if 'attachments' not in msg:
        if len(msg['text']) > 20 and msg['text'] not in data_string:
            session['bezfoto']['lip'].append('&#128073; ' + avtortut(msg))
        msg = []
    save_table('bezfoto')
    return msg
