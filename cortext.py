import re

text_list = ['Пажалуста принесите\nводы и идите спать. анон\nАдмину шоколадку',
             'Привет. Анон. Назовите все!!!\nЕсли нет ,то ананимна, что делать-то???!)',
             'Анонимно пожалста запустите ракету неанон не анон приехали',
             'ананимна блядь) напишите ганон',
             'анонимно ананимно неанон канон',
             'канонинзированный (взаимно (Аноним',
             'анон приехал Анон',
             'Неанон брат не анон...наверно не анонимно и не ананимна']

pattern_anon = r"(\b|не|не )ан[оа]н(\b|\S+)"
del_anon = re.compile(pattern_anon, re.IGNORECASE)

for text in text_list:
    print(text)
    text = re.sub(r'\n', ' ', text)
    print(text)
    text = del_anon.sub(' ', text)
    print(text)
    text = re.sub(r'\s+', ' ', text)
    print(text)
    text = re.sub(r'^ | $', '', text)

    for bad_words in [
        "Админ разместите объявление, спасибо. ",
        "Админ пропусти",
        "админу добра",
        "админ ",
        "Админу",
        "пожалуйста",
        "пожалуйсто",
        "пожалуста",
        "()",
        "(, )",
        "( )"
    ]:
        text = text.replace(bad_words, ' ')
    print(text)

    text = re.sub(r'\s+', ' ', text)
    print(text)
    text = re.sub(r'^ | $', '', text)
    print(text)
    print('____Следующий текст:')
