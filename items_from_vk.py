import config
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api
from moduls.utils.clear_copy_history import clear_copy_history

session = config.session


vkapp = get_session_vk_api(session['VK_TOKEN_VALSTAN'])
new_posts = get_msg(vkapp, -9363816, 0, 20)
sample_clear = []
for sample in new_posts:
    template = clear_copy_history(sample)
    # if template['owner_id'] not in (-179037590, -162751110):
    sample_clear.append(template)
pass
print('Конец')


