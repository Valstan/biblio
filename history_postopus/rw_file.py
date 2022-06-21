import json
import os


class RWFile:
    def __init__(self, patch, name, value=''):
        self.patch = patch
        self.name = name
        self.value = value

    def open(self):
        self.examination_dir()
        self.examination_file()
        with open(os.path.join(self.patch + self.name + '.json'), 'r', encoding='utf-8') as f:
            self.value = json.load(f)
            return self.value

    def save(self):
        self.examination_dir()
        with open(os.path.join(self.patch + self.name + '.json'), 'w', encoding='utf-8') as f:
            f.write(json.dumps(self.value, indent=2, ensure_ascii=False))

    def examination_file(self):
        if not os.path.exists(self.patch + self.name + '.json'):
            with open(os.path.join(self.patch + self.name + '.json'), 'w', encoding='utf-8') as f:
                f.write(json.dumps({"0": "0"}, indent=2, ensure_ascii=False))

    def examination_dir(self):
        if self.patch and not os.path.isdir(self.patch):
            os.makedirs(self.patch)

    def rename(self):
        os.rename(self.name + '.json', 'old_' + self.name + '.json')
