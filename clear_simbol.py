delete_bad_simbol = ' ,\n('
delete_word = [
    'Админ разместите анонимно объявление, спасибо. ', 'Не анонимно', 'Админ пропусти', 'анонимно',
    'АНОНИМНО', 'ананимно', 'ананимна', 'аноним', 'пожалуйста', 'пожалуйсто', 'пожалуста', 'Не анон',
    'неанон', 'Анон)', 'анон', 'админу добра', 'админ', '()', '(, )', '( )'
]


def correct_txt(text):
    text_lower = text.lower()
    for i in delete_word:
        sample = i.lower()
        while True:
            pos = text_lower.find(sample)
            if pos == -1:
                break
            text = text[:pos] + text[pos + len(sample):]
            text_lower = text_lower[:pos] + text_lower[pos + len(sample):]
            pass

    for i in range(6):
        text = text.replace('  ', ' ')
        text = text.replace(' !', '!')
        text = text.replace(' ?', '?')
        text = text.replace(' ,', ',')
        text = text.replace(' .', '.')
        text = text.replace('..', '.')
        text = text.replace('.,', '.')
        text = text.replace(',.', '.')
        text = text.replace('.!', '!')
        text = text.replace(',!', '!')
        text = text.strip(delete_bad_simbol)

    return text


msg = ' Привет, меня зовут Валентин. Админ пропусти.Админ, размести' \
      ' пожалуйста пост. ананимна Куплю курицу,!!?' \
      ' недорого\nЕсли не разместишь анон то что мне делать. (админу добра) приехал домой, админ пропусти. вот. !'
msg = correct_txt(msg)
print(msg)
