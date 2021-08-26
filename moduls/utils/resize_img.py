from PIL import Image


def resize_img(img, new_width, new_height):

    width, height = img.size
    width = int(new_height * width / height)
    img = img.resize((width, new_height), Image.ANTIALIAS)
    width, height = img.size
    if width > new_width:
        height = int(new_width * height / width)
        img = img.resize((new_width, height), Image.ANTIALIAS)
    return img
