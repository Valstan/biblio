from config import bases, fbase, size_base_old_posts
from moduls.parser import parser
from moduls.read_write.get_json import getjson
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.upload_post_to_main_group import upload_post_to_main_group
from moduls.read_write.write_json import writejson
from moduls.sort.sort_po_foto import sort_po_foto


def main_program(name_novost, prefix_base):
    base = getjson(bases + prefix_base + fbase)
    vkapp = get_session_vk_api(base['id']['l'], base['id']['p'])
    base, msg_list = parser(vkapp, base, name_novost)
    if msg_list:
        for sample in msg_list:
            if upload_post_to_main_group(vkapp, sample, base):
                skleika = str(sample['owner_id']) + str(sample['id'])
                base['lip'].append(skleika)
                while len(base['lip']) > size_base_old_posts:
                    del base['lip'][0]
                sample, histo = sort_po_foto(sample, base)
                if histo:
                    base['hash'].append(histo)
                    while len(base['hash']) > size_base_old_posts:
                        del base['hash'][0]
                writejson(bases + base['prefix'] + fbase, base)
                return True
    writejson(bases + base['prefix'] + fbase, base)
    return False


if __name__ == '__main__':
    pass
