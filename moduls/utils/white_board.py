from PIL import Image


def white_board(img, width, height):
    white_img = Image.new('RGB', (width, height), color='white')

    x = int((white_img.size[0] - img.size[0]) / 2)
    y = int((white_img.size[1] - img.size[1]) / 2)

    white_img.paste(img, (x, y))
    return white_img
