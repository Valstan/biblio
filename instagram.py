from InstagramAPI import InstagramAPI

user = 'malmig_info'
pwd = 'nitro1941'
InstagramAPI = InstagramAPI(user, pwd)
InstagramAPI.login()  # login
photo_path = 'uploads/insta.jpg'
caption = "📝Мичуринцы не подвели Технический осмотр зерноуборочных комбайнов в колхозе имени Мичурина проходил перед " \
          "самым выездом в поле. Главе района Э.Л.Симонову и начальнику инспекции Гостехнадзора А.П.Суслопарову " \
          "комбайны показывали руководитель И.М.Егоров, экономист Р.И.Егоров, агроном Р.Г.Волков и, конечно, " \
          "комбайнеры. Продолжение читайте в нашей газете. -> https://vk.com/wall-179280169_4207 #НовостиМалмыжа " \
          "Нажми лайк ❤ и поделись новостью с друзьями 👇"
InstagramAPI.uploadPhoto(photo_path, caption=caption)
