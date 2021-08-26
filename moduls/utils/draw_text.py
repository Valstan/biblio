from PIL import ImageDraw, ImageFont


def draw_text(img, text, vertikal, gorizont):
    draw_text_img = ImageDraw.Draw(img)
    font = ImageFont.truetype("georgia.ttf", size=20)
    draw_text_img.text((vertikal, gorizont), text, 'green', font=font)
    return img
