from config import delete_word, delete_bad_simbol


def correct_txt(msg):
    text_lower = msg['text'].lower()
    for i in delete_word:
        sample = i.lower()
        while True:
            pos = text_lower.find(sample)
            if pos == -1:
                break
            msg['text'] = msg['text'][:pos] + msg['text'][pos + len(sample):]
            text_lower = text_lower[:pos] + text_lower[pos + len(sample):]
            pass

    for i in range(6):
        msg['text'] = msg['text'].replace('  ', ' ')
        msg['text'] = msg['text'].replace(' !', '!')
        msg['text'] = msg['text'].replace(' ?', '?')
        msg['text'] = msg['text'].replace(' ,', ',')
        msg['text'] = msg['text'].replace(' .', '.')
        msg['text'] = msg['text'].replace('..', '.')
        msg['text'] = msg['text'].replace('.,', '.')
        msg['text'] = msg['text'].replace(',.', '.')
        msg['text'] = msg['text'].replace('.!', '!')
        msg['text'] = msg['text'].replace(',!', '!')
        msg['text'] = msg['text'].strip(delete_bad_simbol)

    return msg


if __name__ == '__main__':
    pass
