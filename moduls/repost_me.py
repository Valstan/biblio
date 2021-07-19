from config import bases, fbase
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.read_write.get_json import getjson
from moduls.read_write.write_json import writejson


def post_me():
    base = getjson(bases + 'post_me_' + fbase)
    vkapp = get_session_vk_api(base['l'], base['p'])
    new_posts = get_msg(vkapp, base['read_group'], 10, 10)
    sample_template_repost = ''
    for sample in new_posts:
        sample_template_repost = ''.join(map(str, ('wall', sample['owner_id'], '_', sample['id'])))
        if sample_template_repost not in base['template_repost']:
            if '#ОбъявленияМалмыж' not in sample['text']:
                break
        sample_template_repost = ''
    if sample_template_repost:
        try:
            vkapp.wall.repost(object=sample_template_repost)
        except:
            pass
        base['template_repost'].append(sample_template_repost)
        while len(base['template_repost']) > 100:
            del base['template_repost'][0]
        writejson(bases + 'post_me_' + fbase, base)


if __name__ == '__main__':
    post_me()
