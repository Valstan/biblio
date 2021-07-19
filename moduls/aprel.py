from config import bases, fbase
from moduls.read_write.get_json import getjson
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.write_json import writejson
from moduls.utils.clear_copy_history import clear_copy_history


def aprel(prefix_base):
    base = getjson(bases + prefix_base + fbase)

    vkapp = get_session_vk_api(base['id']['l'], base['id']['p'])
    aprel_id = -144647350
    msgs = get_msg(vkapp, aprel_id, 0, 10)
    msg_link = []

    for msg in msgs:
        msg = clear_copy_history(msg)
        msg_link = ''.join(map(str, ('wall', msg['owner_id'], '_', msg['id'])))
        if msg_link not in base['aprel_links']:
            break

    if msg_link:
        id_group = base['id']['post_group']['key'] * -1
        try:
            vkapp.wall.repost(object=msg_link, group_id=id_group)
            base['aprel_links'].append(msg_link)
            while len(base['aprel_links']) > 5:
                del base['aprel_links'][0]
            writejson(bases + base['prefix'] + fbase, base)
            return True
        except:
            pass

    return False


if __name__ == '__main__':
    pass
