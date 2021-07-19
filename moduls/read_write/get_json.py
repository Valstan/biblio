import json
import os


def getjson(put):
    with open(os.path.join(put), 'r', encoding='utf-8') as f:
        file = json.load(f)
        return file


if __name__ == '__main__':
    pass
