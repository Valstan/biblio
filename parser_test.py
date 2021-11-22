from logpass.logpass import login, password
from moduls.read_write.get_session_vk_api import get_session_vk_api

vkapp = get_session_vk_api(login, password)

group = -158787639,
offset = 0,
count = 30
msgs = vkapp.wall.get(owner_id=group, count=count, offset=offset, v=5.102)['items']
pass
print('конец')
