from config import delete_word, delete_bad_simbol


def correct_txt(msg):
    for i in delete_word:
        msg['text'] = msg['text'].replace(i, '')
    for i in range(5):
        msg['text'] = msg['text'].strip(delete_bad_simbol)

    return msg


if __name__ == '__main__':
    pass
