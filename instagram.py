import os
import shutil

from PIL import Image, ImageDraw, ImageFont
from instabot import Bot

from logpass.logpass import insta_mi_l, insta_mi_p

path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config')
if os.path.exists(path):
    shutil.rmtree(path)

photo = 'metabo.jpg'
caption = "Шуруповерты в магазине БРИГАДИР, г.Малмыж, ул.Урицкого, 3 (в здании Сбербанка)"

im = Image.new('RGB', (1080, 1080), color='white')

tatras = Image.open(photo)
width, height = tatras.size
new_height = 1080  # Высота
new_width = int(new_height * width / height)
tatras = tatras.resize((new_width, new_height), Image.ANTIALIAS)
width, height = tatras.size
if width > 1080:
    new_width = 680  # ширина
    new_height = int(new_width * height / width)
    tatras = tatras.resize((new_width, new_height), Image.ANTIALIAS)

width, height = tatras.size
koordinat = int((1080 - width) / 2)

im.paste(tatras, (koordinat, 0))

draw_text = ImageDraw.Draw(im)
font = ImageFont.truetype("arial.ttf", size=18)
draw_text.text((10, 10), 'Малмыж Инфо', font=font)

im.save('image.jpeg')

bot = Bot()
bot.login(username=insta_mi_l, password=insta_mi_p)

#  upload a picture
bot.upload_photo('image.jpeg', caption=caption)

'''from instabot import Bot
bot = Bot()
bot.login(username="", password="")

######  upload a picture #######
bot.upload_photo("yoda.jpg", caption="biscuit eating baby")

######  follow someone #######
bot.follow("elonrmuskk")

######  send a message #######
bot.send_message("Hello from Dhaval", ['user1','user2'])

######  get follower info #######
my_followers = bot.get_user_followers("dhavalsays")
for follower in my_followers:
    print(follower)

bot.unfollow_everyone()'''

'''# конвертируем картинку
from PIL import Image, ImageDraw
img = Image.open('pic.png') # открываем PNG
img.save('pic.gif') # сохраняем как GIF

# создаем картинку, пишем в неё текст и сохраняем
from PIL import Image, ImageDraw
text = "Abracadabra :)" # готовим текст
color = (0, 100, 100) # создаем цвет
img = Image.new('RGB', (100, 100), color) # создаем изображение 
imgDrawer = ImageDraw.Draw(img)
imgDrawer.text((10, 20), text) # пишем на изображении наш текст
img.save("pic.png") # сохраняем в PNG

# проводим манипуляции с картинкой, получаем ее свойства
from PIL import Image, ImageDraw 
img = Image.open('pic.png') #открываем изображение
format = img.format #формат изображения
size = img.size #размер изображения
histogram = image.histogram() # получаем гистограмму'''
