import json
import os
from collections import Counter
from datetime import datetime

from logpass.logpass import brigadir_l, brigadir_p
from moduls.read_write.get_session_vk_api import get_session_vk_api

vkapp = get_session_vk_api(brigadir_l, brigadir_p)
group_name = 'Малмыж Инфо'
group_id = -158787639
current_date = datetime.now().date()
current_time = datetime.now().time()

msgs = vkapp.wall.get(group_id=group_id, count=100, offset=0, v=5.131)['items']

likes = sorted(msgs, key=lambda k: k['likes']['count'], reverse=True)
print(likes)

# sort_sity = {k: v for k, v in sorted(sort_sity.items(), key=lambda item: item[1], reverse=True)}
'''result = [group_name, 'Всего подписчиков - ' + str(len(persons)), 'Мертвые души - ' + str(deactivated),
          'Количество городов - ' + str(len_gorod), 'Малмыжский район - ', str(city_raion_counter),
          'Кировская область - ', str(city_oblast_counter),
          'Татарстан - ', str(city_tatarstan_counter),
          'Другие города (живые) - ', str(city_other_counter),
          'Города (живых+мертвых):']
for k, v in sort_sity.items():
    result.extend([str(v) + '-' + str(k)])


with open(os.path.join(group_name + '_' + str(group_id) + '_' + str(current_date) +
                       '-' + str(current_time.hour) + '-' + str(current_time.minute) + '.json'),
          'w', encoding='utf-8') as f:
    f.write(json.dumps(result, indent=2, ensure_ascii=False))'''
