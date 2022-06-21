from bin.utils.driver_tables import load_table
from config import session


def sort_double(msg, news_msg_list, maingroup_msg_list):
    session['bezfoto'] = load_table('bezfoto')
    session['all_bezfoto'] = load_table('all_bezfoto')
    data_string = " ".join(session['bezfoto']['lip'] +
                           session['all_bezfoto']['lip'] +
                           news_msg_list +
                           maingroup_msg_list)
    if msg['text'] in data_string:
        msg = []
    return msg
