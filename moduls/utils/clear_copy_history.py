from moduls.read_write.get_msg import get_msg


def clear_copy_history(vkapp, msg):
    if 'copy_history' in msg:
        msg = msg['copy_history'][0]
        msgs = get_msg(vkapp, msg['owner_id'], 0, 30)
        for i in msgs:
            if i['id'] == msg['id']:
                msg = i
                break

    return msg
