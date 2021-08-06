from logpass.logpass import valstan_l, valstan_p
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.utils.clear_copy_history import clear_copy_history

vkapp = get_session_vk_api(valstan_l, valstan_p)
new_posts = get_msg(vkapp, -158787639, 0, 20)
sample_clear = []
for sample in new_posts:
    sample_clear.append(clear_copy_history(sample))
print('Конец')


