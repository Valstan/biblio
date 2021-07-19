def sort_double(msg, msg_list, all_posts, base):
    if msg['text'] not in all_posts and msg['text'] not in base['all_bezfoto'] and msg['text'] not in base['bezfoto'] \
                and msg['text'] not in msg_list and msg['attachments'] not in all_posts\
                and msg['attachments'] not in msg_list:
        return msg
