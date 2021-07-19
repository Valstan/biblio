import json
import os


def writejson(put, file):
    with open(os.path.join(put), 'w', encoding='utf-8') as f:
        f.write(json.dumps(file, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    pass
