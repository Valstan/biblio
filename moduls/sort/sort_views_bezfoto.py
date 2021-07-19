def sort_views_bezfoto(msg):
    if 'views' not in msg and 'attachments' in msg:
        del msg['attachments']

    return msg
