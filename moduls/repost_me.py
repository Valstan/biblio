from config import bases, fbase, conf, repost_accounts
from moduls.read_write.change_lp import change_lp
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.get_json import getjson
from moduls.read_write.write_json import writejson


def post_me(prefix_base):
    base = getjson(bases + prefix_base + fbase)

    for i in repost_accounts:
        if 'repost' not in base['links'][i]:
            base['links']['repost'][i] = []
        vkapp = get_session_vk_api(change_lp(i))
        new_posts = get_msg(vkapp, conf['m']['post_group']['key'], 0, 15)
        sample_template_repost = ''
        for sample in new_posts:
            sample_template_repost = ''.join(map(str, ('wall', sample['owner_id'], '_', sample['id'])))
            if sample_template_repost not in base['links']['repost'][i]:
                if conf['m']['podpisi']['heshteg']['reklama'] not in sample['text'] and \
                        conf['m']['podpisi']['heshteg']['music'] not in sample['text']:
                    break
            sample_template_repost = ''
        if sample_template_repost:
            try:
                vkapp.wall.repost(object=sample_template_repost)
            except:
                pass
            base['links']['repost'][i].append(sample_template_repost)
            while len(base['links']['repost'][i]) > 20:
                del base['links']['repost'][i][0]
    writejson(bases + prefix_base + fbase, base)


if __name__ == '__main__':
    pass
