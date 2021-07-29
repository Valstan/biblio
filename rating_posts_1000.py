import json
import os
from datetime import datetime
from time import sleep

from vk_api import VkApi
from collections import Counter

from logpass.logpass import valstan_l, valstan_p

vk_session = VkApi(valstan_l, valstan_p)
vk_session.auth()
vkapp = vk_session.get_api()
group_name = 'МалмыжМолодой'
group_id = -187462239
current_date = datetime.now().date()
current_time = datetime.now().time()
msgs = []
limit = 100
for i in range(10):
    offset = i * limit
    msgs += vkapp.wall.get(owner_id=group_id, count=limit, offset=offset, v=5.131)['items']
    print(offset)
    sleep(1)

result = {"Имя группы": group_name,
          "Номер группы": group_id,
          'Рейтинг лайков': {},
          'Всего лайков': 0,
          'Рейтинг просмотров': {},
          'Всего просмотров': 0,
          'Рейтинг коментов': {},
          'Всего коментов': 0,
          'Рейтинг репостов': {},
          'Всего репостов': 0,
          'Рейтинг ссылок': {}
          }

likes_all = 0
for i in range(1000):
    if 'likes' in msgs[i]:
        likes_all += msgs[i]['likes']['count']
result['Всего лайков'] = likes_all

views_all = 0
for i in range(1000):
    if 'views' in msgs[i]:
        views_all += msgs[i]['views']['count']
result['Всего просмотров'] = views_all

comments_all = 0
for i in range(1000):
    if 'comments' in msgs[i]:
        comments_all += msgs[i]['comments']['count']
result['Всего коментов'] = comments_all

reposts_all = 0
for i in range(1000):
    if 'reposts' in msgs[i]:
        reposts_all += msgs[i]['reposts']['count']
result['Всего репостов'] = reposts_all

likes = sorted(msgs, key=lambda k: k['likes']['count'], reverse=True)
comments = sorted(msgs, key=lambda k: k['comments']['count'], reverse=True)
reposts = sorted(msgs, key=lambda k: k['reposts']['count'], reverse=True)

msgs_views = []
for sample in msgs:
    if 'views' in sample:
        msgs_views.append(sample)
views = sorted(msgs_views, key=lambda k: k['views']['count'], reverse=True)

links = []
for sample in likes[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.extend([link])
    result['Рейтинг лайков'][sample['likes']['count']] = link

for sample in views[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.extend([link])
    result['Рейтинг просмотров'][sample['views']['count']] = link

for sample in comments[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.extend([link])
    result['Рейтинг коментов'][sample['comments']['count']] = link

for sample in reposts[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.extend([link])
    result['Рейтинг репостов'][sample['reposts']['count']] = link

sort_links = dict(Counter(links))
result['Рейтинг ссылок'] = {k: v for k, v in sorted(sort_links.items(), key=lambda item: item[1], reverse=True)}

with open(os.path.join('РейтингПостов_' + group_name + '_' + str(group_id) + '_' + str(current_date) +
                       '-' + str(current_time.hour) + '-' + str(current_time.minute) + '.json'),
          'w', encoding='utf-8') as f:
    f.write(json.dumps(result, indent=2, ensure_ascii=False))
