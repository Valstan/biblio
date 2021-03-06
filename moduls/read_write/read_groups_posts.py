import time

from config import conf
from moduls.read_write.get_msg import get_msg


def readposts(vkapp, base, name, count):
    posts = []
    for group in conf[base['prefix']][name].values():
        try:
            posts += get_msg(vkapp, group, 0, count)
        except:
            pass
        time.sleep(1)
    return posts


if __name__ == '__main__':
    pass
