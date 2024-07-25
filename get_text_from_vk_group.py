import config
from moduls.read_write.get_msg import get_msg
from moduls.read_write.get_session_vk_api import get_session_vk_api

session = config.session


vkapp = get_session_vk_api(session['VK_TOKEN_VALSTAN'])
new_posts = get_msg(vkapp, -214834539, 0, 100)
text = ""
for sample in new_posts:
    text += sample['text']
pass
# Имя файла, в который будет сохранен текст
filename = "example.txt"

# Открываем файл в режиме записи
with open(filename, "w", encoding="utf-8") as file:
    # Записываем текст в файл
    file.write(text)

print(f"Текст успешно сохранен в файл '{filename}'")
print('Конец')


