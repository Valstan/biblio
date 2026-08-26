

'''from bin.utils.send_error import send_error
from config import TELEGA_TOKEN_VALSTANBOT


def proverka_imeni_dobavlu(session)] =
    send_error(session, f'Модуль - {proverka_imeni_dobavlu.__name__}')


if __name__ == '__main__'] =
    sessia = {'TELEGA_TOKEN_VALSTANBOT'] = TELEGA_TOKEN_VALSTANBOT}
    proverka_imeni_dobavlu(sessia)'''

'''cron_schedule = (
    # mi
    '05 7,8,10,12,14-23 mi_novost',
    '15 9,13 mi_repost_reklama',
    '15 7,12,18,20,22 mi_addons',
    '15 21 mi_repost_krugozor',
    '15 19 mi_repost_aprel',
    '20 6-23 mi_repost_me',
    # dran
    '25 7,9,12,18,20,22 dran_novost',
    '25 6,8,11,15,19,21,23 dran_addons',
    # sbor reklamy
    '40 5-22 dran_reklama',
    '50 6-22 mi_reklama')

for string_schedule in cron_schedule] =
    minute, hours_all, prefix = string_schedule.split()
    print('Минуты - ', minute, '\nСписок часов - ', hours_all, '\nПрефикс - ', prefix)'''

'''name_session = 'mi_repost_me'
name_session = name_session.split('_', 1)[1]
print(name_session)'''

'''from vk_api import VkApi

vk_session = VkApi('<vk-login>', '<vk-password>')
vk_session.auth()
vk_app = vk_session.get_api()

result = vk_app.groups.getById(group_id=158787639)
print(result)'''

'''import re

reklama = '\n#Реклама'
music = '\n#Музыка'


new_posts = [
    '🎼🎼🎼❤❤❤ ВАШ ЛАЙК - для нас стимул на качество 😊😊😊#ПремьераКлипа #ЛучшиеКлипы #Клипы2022 #клипынедели #клипыновинки #Клипы -> *подробнее* \n#Музыка',
    '🎼🎼🎼❤❤❤ ВАШ ЛАЙК - для нас стимул на качество 😊😊😊#ПремьераКлипа #ЛучшиеКлипы #Клипы2022 #клипынедели #клипыновинки #Клипы -> *подробнее*\n #Реклама',
    'Просто строка\nА может и не просто строка'
]
link = 0
for sample in new_posts] =

    if re.search(reklama, sample, flags=re.MULTILINE) or re.search(music, sample, flags=re.MULTILINE)] =
        continue
    link = sample
    break

print(link)'''
