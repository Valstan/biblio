from moduls.read_write.change_lp import change_lp
from config import bases, fbase, conf
from moduls.read_write.get_json import getjson
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.upload_post_to_main_group import upload_post_to_main_group
from moduls.read_write.write_json import writejson
from moduls.utils.avtortut import avtortut
from moduls.utils.clear_copy_history import clear_copy_history


def krugozor(prefix_base):
    base = getjson(bases + prefix_base + fbase)
    vkapp = get_session_vk_api(change_lp(prefix_base))
    msgs = get_msg(vkapp, -168171570, 0, 50)
    for sample in msgs:
        sample = clear_copy_history(sample)
        link = ''.join(map(str, ('wall', sample['owner_id'], '_', sample['id'])))
        if link not in base['links']['krugozor']:
            sample['text'] = ''.join(map(str, (conf[base['prefix']]['podpisi']['zagolovok']['krugozor'],
                                               avtortut(sample),
                                               conf[base['prefix']]['podpisi']['heshteg']['krugozor'],
                                               conf[base['prefix']]['podpisi']['final'])))
            if upload_post_to_main_group(vkapp, sample, base):
                base['links']['krugozor'].append(link)
                while len(base['links']['krugozor']) > 50:
                    del base['links']['krugozor'][0]
                writejson(bases + base['prefix'] + fbase, base)
                break
