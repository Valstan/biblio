from config import conf
from moduls.read_write.get_attach import get_attach
from moduls.read_write.post_msg import post_msg


def upload_post_to_main_group(vkapp, msg, base):
    postatach = ''
    if 'attachments' in msg:
        postatach = get_attach(msg)
    try:
        post_msg(vkapp,
                 conf[base['prefix']]['post_group']['key'],
                 msg['text'],
                 postatach)
    except:
        return False
    return True
