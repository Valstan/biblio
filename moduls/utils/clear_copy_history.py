def clear_copy_history(msg):
    if 'copy_history' in msg:
        msg = msg['copy_history'][0]
    return msg
