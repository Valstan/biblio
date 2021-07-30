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

result = ["Имя группы", group_name, "Номер группы", group_id]

likes_all = 0
views_all = 0
comments_all = 0
reposts_all = 0
for i in range(1000):
    if 'likes' in msgs[i]:
        likes_all += msgs[i]['likes']['count']
    if 'views' in msgs[i]:
        views_all += msgs[i]['views']['count']
    if 'comments' in msgs[i]:
        comments_all += msgs[i]['comments']['count']
    if 'reposts' in msgs[i]:
        reposts_all += msgs[i]['reposts']['count']

likes = sorted(msgs, key=lambda k: k['likes']['count'], reverse=True)
comments = sorted(msgs, key=lambda k: k['comments']['count'], reverse=True)
reposts = sorted(msgs, key=lambda k: k['reposts']['count'], reverse=True)

msgs_views = []
for sample in msgs:
    if 'views' in sample:
        msgs_views.append(sample)
views = sorted(msgs_views, key=lambda k: k['views']['count'], reverse=True)

links = []

result.append('Лайки = ' + str(likes_all))
for sample in likes[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.append(link)
    result.append(str(sample['likes']['count']) + ' - ' + link)

result.append('Просмотры = ' + str(views_all))
for sample in views[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.append(link)
    result.append(str(sample['views']['count']) + ' - ' + link)

result.append('Комментарии = ' + str(comments_all))
for sample in comments[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.append(link)
    result.append(str(sample['comments']['count']) + ' - ' + link)

result.append('Репосты = ' + str(reposts_all))
for sample in reposts[:10]:
    link = ''.join(map(str, ('https://vk.com/wall',
                             sample['owner_id'], '_', sample['id'], ' : ', sample['text'][:60])))
    links.append(link)
    result.append(str(sample['reposts']['count']) + ' - ' + link)

result.append('Рейтинг ссылок:')
sort_links = dict(Counter(links))
result_links = {k: v for k, v in sorted(sort_links.items(), key=lambda item: item[1], reverse=True)}
for k, v in result_links.items():
    result.append(str(v) + ' - ' + k)


with open(os.path.join('РейтингПостов_' + group_name + '_' + str(group_id) + '_' + str(current_date) +
                       '-' + str(current_time.hour) + '-' + str(current_time.minute) + '.json'),
          'w', encoding='utf-8') as f:
    f.write(json.dumps(result, indent=2, ensure_ascii=False))
