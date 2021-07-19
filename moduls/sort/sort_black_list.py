import re

from config import delete_msg_blacklist


def sort_black_list(msg):
    msg = msg.replace('"', '')
    msg.lower()
    for sample in delete_msg_blacklist:
        sample.lower()
        if re.search(sample, msg):
            return True


if __name__ == '__main__':
    pass
