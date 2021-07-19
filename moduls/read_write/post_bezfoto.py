from config import size_base_old_posts
from moduls.read_write.post_msg import post_msg


def postbezfoto(vkapp, base):
    message = ''
    for sample in base['bezfoto']:
        message += ''.join(map(str, (sample, '\n')))
    postmsg = ''.join(map(str, (base['heshteg']['bezfoto'], message)))
    postmsg = postmsg[:-1]

    post_msg(vkapp,
             base['id']['post_group']['key'],
             postmsg,
             '')

    base['all_bezfoto'].extend(base['bezfoto'])
    while len(base['all_bezfoto']) > size_base_old_posts:
        del base['all_bezfoto'][0]
    base['bezfoto'].clear()
    return base


if __name__ == '__main__':
    pass
