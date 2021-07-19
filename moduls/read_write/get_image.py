import os
import ssl
import time
from urllib.request import urlopen

from config import bases, fimage


def image_get(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for i in range(3):
        try:
            with open(os.path.join(bases + fimage), 'wb') as img:
                img.write(
                    urlopen(url, context=ctx).read())
            return True
        except:
            pass
        time.sleep(1)
    return False


if __name__ == '__main__':
    pass
