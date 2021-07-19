import json
import os
from collections import Counter
from time import sleep

from moduls.read_write.get_session_vk_api import get_session_vk_api

vkapp = get_session_vk_api("79229005910", "Nitro@1941")
group_name = 'Обо всем г.Малмыж'
group_id = 89083141
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
for pers in persons:
    if 'deactivated' in pers:
        deactivated = deactivated + 1
    if 'city' in pers:
        city.append(pers['city']['title'])
sort_sity = dict(Counter(city))
len_gorod = len(sort_sity)
sort_sity = {k: v for k, v in sorted(sort_sity.items(), key=lambda item: item[1], reverse=True)}
result = [group_name, 'Всего подписчиков - ' + str(len(persons)), 'Мертвые души - ' + str(deactivated),
          'Количество городов - ' + str(len_gorod), 'Города:']
for k, v in sort_sity.items():
    result.extend([str(v) + '-' + str(k)])


with open(os.path.join(str(group_id) + '.json'), 'w', encoding='utf-8') as f:
    f.write(json.dumps(result, indent=2, ensure_ascii=False))
