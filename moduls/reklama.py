import random

from moduls.read_write.change_lp import change_lp
from config import bases, fbase, conf
from moduls.read_write.get_json import getjson
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.write_json import writejson
from moduls.utils.clear_copy_history import clear_copy_history


def reklama(prefix_base):
    base = getjson(bases + prefix_base + fbase)
    vkapp = get_session_vk_api(change_lp(prefix_base))
    glav = -163580976
    zam = -172650802
    dvorniki = -171276826
    ruletka = [glav, glav, glav, glav, glav, glav, glav, glav,
               zam, zam, zam, zam,
               dvorniki]
    random.shuffle(ruletka)
    shut = random.choice(ruletka)
    ruletka = get_msg(vkapp, shut, 0, 50)
    random.shuffle(ruletka)
    for i in range(20):
        shut = random.choice(ruletka)
        shut = clear_copy_history(shut)
        shut = ''.join(map(str, ('wall', shut['owner_id'], '_', shut['id'])))
        if shut not in base['links']['reklama']:
            break

    id_group = conf[base['prefix']]['post_group']['key'] * -1
    try:
        vkapp.wall.repost(object=shut, group_id=id_group)
        base['links']['reklama'].append(shut)
        while len(base['links']['reklama']) > 20:
            del base['links']['reklama'][0]
        writejson(bases + base['prefix'] + fbase, base)
        return True
    except:
        return False


if __name__ == '__main__':
    reklama('t')
