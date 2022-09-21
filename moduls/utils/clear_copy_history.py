def clear_copy_history(msg):
    if 'copy_history' in msg:
        new_msg = msg['copy_history'][0]
        if 'likes' in msg:
            new_msg['likes'] = msg['likes']
        if 'views' in msg:
            new_msg['views'] = msg['views']
        msg = new_msg

    return msg
