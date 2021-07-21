import json
import os
from collections import Counter
from time import sleep

from logpass.logpass import brigadir_l, brigadir_p
from moduls.read_write.get_session_vk_api import get_session_vk_api

vkapp = get_session_vk_api(brigadir_l, brigadir_p)
group_name = 'Малмыж Инфо'
group_id = 158787639
persons = []
limit = 1000
for i in range(20):
    offset = i * limit
    get_persons = vkapp.groups.getMembers(group_id=group_id, offset=offset, fields='city', v=5.92)['items']
    persons.extend(get_persons)
    print(20 - i)
    sleep(1)

deactivated = 0
city = []
local_pers = 0
city_raion = 'МалмыжСавалиКалининоСтарый ИрюкТат-Верх-ГоньбаАкбатыревоШишинер' \
             'РожкиАджимНовая СмаильМари-МалмыжМалый КитякПоречке КитякКокуй' \
             'Большой КитякКинерьБольшой СатнурАрыкКонстантиновкаЗахватаево' \
             'ПлотбищеЛазаревоКаксинвайМелетьСтарый БурецБольшая ШабанкаРусский Турек' \
             'РальникиБольшой РойНовый БурецГоньбаПукшинерьСмаильНовый ИрюкНовая Тушка' \
             'Удмурт КитякНовый БуртекАзнакаевоМарсПерескокиПорезНослыСтарый Пукшинер' \
             'АлдаровоЧетвертое отделение психоневрологического диспансера' \
             'Большой ПорекМалая ШабанкаПостниковоСтарый БуртекКаменный КлючКуженерка'
city_oblast = 'КировВятские ПоляныУржумКильмезьКрасная ПолянаСосновкаДонауровоВерхошижемье' \
              'НолинскКотельничСоветскВяткаСлободскойЛебяжьеОмутнинскНемаДаровскойПреображенка' \
              'ЛузаЗуевкаВахруши (пгт)Малая КильмезьМурыгиноМурашиУниСредняя ТоймаЮрьяКикнур' \
              'КирсСанчурскСуна'
city_tatarstan = 'КазаньБалтасиНабережные ЧелныКукморНуринерИннополисШеморданМамадыш' \
                 'АрскНижнекамскАльметьевскЦипьяЗеленодольскБугульмаБольшой Кукмор' \
                 'ЕлабугаЯнгуловоЧутайЧебоксарыТатарстанКизнерБогатые Сабы'
city_raion_counter = 0
city_oblast_counter = 0
city_tatarstan_counter = 0
city_other_counter = 0
for pers in persons:
    if 'deactivated' in pers:
        deactivated = deactivated + 1
        continue
    if 'city' in pers:
        city.append(pers['city']['title'])
        if pers['city']['title'] in city_raion:
            city_raion_counter = city_raion_counter + 1
        elif pers['city']['title'] in city_oblast:
            city_oblast_counter = city_oblast_counter + 1
        elif pers['city']['title'] in city_tatarstan:
            city_tatarstan_counter = city_tatarstan_counter + 1
        else:
            city_other_counter = city_other_counter + 1
    else:
        city.append('None')

sort_sity = dict(Counter(city))
len_gorod = len(sort_sity)
sort_sity = {k: v for k, v in sorted(sort_sity.items(), key=lambda item: item[1], reverse=True)}
result = [group_name, 'Всего подписчиков - ' + str(len(persons)), 'Мертвые души - ' + str(deactivated),
          'Количество городов - ' + str(len_gorod), 'Подписички с Малмыжского района - ', str(city_raion_counter),
          'Подписички с Кировской области - ', str(city_oblast_counter),
          'Подписички с Татарстана - ', str(city_tatarstan_counter),
          'Подписички с других городов (живые) - ', str(city_other_counter),
          'Города:']
for k, v in sort_sity.items():
    result.extend([str(v) + '-' + str(k)])


with open(os.path.join(str(group_id) + '.json'), 'w', encoding='utf-8') as f:
    f.write(json.dumps(result, indent=2, ensure_ascii=False))
