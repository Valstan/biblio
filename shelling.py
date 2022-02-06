import random
import time
from tkinter import *

root = Tk()

screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()
print(f'Максимальное разрешение вашего экрана: {screen_width}x{screen_height}')
while True:
    try:
        max_width = int(input("Введите желаемую ширину экрана: "))
        if max_width > screen_width:
            print("Ваш экран меньше запрашиваемого размера. Введите число поменьше.")
        else:
            break
    except ValueError:
        print("Вы ввели не число!")
while True:
    try:
        max_height = int(input("Введите желаемую высоту экрана: "))
        if max_height > screen_height:
            print("Ваш экран меньше запрашиваемого размера. Введите число поменьше.")
        else:
            break
    except ValueError:
        print("Вы ввели не число!")

root.geometry(f"{max_width}x{max_height}")
while True:
    try:
        row = int(input("Введите кол-во ячеек в ряду: "))
        if row > max_width // 2:
            print('Чет слишком много ячеек. Набери поменьше.')
        else:
            break
    except ValueError:
        print("Вы Ввели не число!")
while True:
    try:
        height_inp = int(input("Введите кол-во ячеек в высоту: "))
        if height_inp > max_height // 2:
            print('Чет слишком много ячеек. Набери поменьше.')
        else:
            break
    except ValueError:
        print("Вы ввели не число!")

while True:
    try:
        free_cell = int(input("Введите кол-во пустых ячеек(минимум 1): "))
        if free_cell < 1:
            print("Я же сказал, не меньше 1!")
        elif free_cell >= row * height_inp // 2:
            print("Свободных ячеек слишком много. Введите число поменьше.")
        else:
            break
    except ValueError:
        print("Вы ввели не число!")

while True:
    try:
        need_comfort = int(input("Введите необходимый уровень комфорта для одной ячейки(не меньше 1 и не больше 8): "))
        if need_comfort > 8 or need_comfort < 1:
            print("Не больше 8 и не меньше 1!!!")
        else:
            break
    except ValueError:
        print("Вы ввели не число!")

while True:
    try:
        cycle = int(input("Введите кол-во циклов пробежки"
                          "(один цикл равен полному обновлению местоположения всех некомфортно-чувствующих себя "
                          "ячеек): "))
        break
    except ValueError:
        print("Вы ввели не число!")

colors = ['green', 'red']

width = max_width // row - 1
height = max_height // height_inp - 1
print(f'Ширина: {width} | Высота: {height}')
number = 0
coordinates_y = 0

map_cells = []

for x in range(row):

    row_cell = []
    for i in range(height_inp):
        color = random.choice(colors)
        row_cell.append(color)
        number += 1
    map_cells.append(row_cell)

free_access = 0
stop_while = False
free_in_map = []


def gen_free():
    global free_access
    for y in range(len(map_cells)):
        for x in range(len(map_cells[y])):
            access = random.choice(
                [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'white', 1, 1, 1, 1, 1,
                 1, 1])
            if access == 'white':
                if map_cells[x][y] == 'white':
                    break
                free_access += 1
                map_cells[x][y] = 'white'
                free_in_map.append([x, y])
                return


while True:
    if int(free_access) != int(free_cell):
        gen_free()
    else:
        break


def gen_mapping():
    global coordinates_y
    for y in map_cells:
        coordinates_x = 0
        for x in y:
            l = Label(root, bg=x)
            l.place(x=coordinates_x, y=coordinates_y, width=width, height=height)
            coordinates_x += width + 1
        coordinates_y += height + 1


gen_mapping()
print('Генерация карты и запуск окна!')


def check_comfort(x, y):
    now = map_cells[x][y]
    left, right, down, up = False, False, False, False
    if int(x) != 0:
        left = True
    if int(x) != row - 1:
        right = True
    if int(y) != height_inp - 1:
        down = True
    if int(y) != 0:
        up = True
    comfort_now = 0
    if left:
        if map_cells[x - 1][y] == now:
            comfort_now += 1
        elif map_cells[x - 1][y] == 'white':
            pass
        else:
            comfort_now -= 1

        if up:
            if map_cells[x - 1][y - 1] == now:
                comfort_now += 1
            elif map_cells[x - 1][y - 1] == 'white':
                pass
            else:
                comfort_now -= 1
        if down:
            if map_cells[x - 1][y + 1] == now:
                comfort_now += 1
            elif map_cells[x - 1][y + 1] == 'white':
                pass
            else:
                comfort_now -= 1
    if right:
        if map_cells[x + 1][y] == now:
            comfort_now += 1
        elif map_cells[x + 1][y] == 'white':
            pass
        else:
            comfort_now -= 1

        if up:
            if map_cells[x + 1][y - 1] == now:
                comfort_now += 1
            elif map_cells[x + 1][y - 1] == 'white':
                pass
            else:
                comfort_now -= 1
        if down:
            if map_cells[x + 1][y + 1] == now:
                comfort_now += 1
            elif map_cells[x + 1][y + 1] == 'white':
                pass
            else:
                comfort_now -= 1
    if down:
        if map_cells[x][y + 1] == now:
            comfort_now += 1
        elif map_cells[x][y + 1] == 'white':
            pass
        else:
            comfort_now -= 1
    if up:
        if map_cells[x][y - 1] == now:
            comfort_now += 1
        elif map_cells[x][y - 1] == 'white':
            pass
        else:
            comfort_now -= 1
    return comfort_now


def gogogo():
    global map_cells
    global free_in_map
    for cycle_number in range(cycle):
        for row_in_map in range(len(map_cells)):
            for cell_in_map in range(len(map_cells[row_in_map])):
                if map_cells[row_in_map][cell_in_map] != 'white':
                    comfort = check_comfort(row_in_map, cell_in_map)
                    if comfort < need_comfort:
                        color = map_cells[row_in_map][cell_in_map]
                        check_free_cell = random.choice(free_in_map)
                        free_in_map.remove(check_free_cell)
                        map_cells[check_free_cell[0]][check_free_cell[1]] = color

                        free_in_map.append([row_in_map, cell_in_map])
                        map_cells[cell_in_map][row_in_map] = 'white'
                        # gen_mapping()
                        l = Label(root, bg='white')
                        l.place(x=width * cell_in_map + cell_in_map, y=height * row_in_map + row_in_map, width=width,
                                height=height)
                        l = Label(root, bg=color)
                        l.place(x=width * check_free_cell[1] + check_free_cell[1],
                                y=height * check_free_cell[0] + check_free_cell[0],
                                width=width, height=height)
                        root.update()

        print(f'{cycle_number + 1} пройден.')
    print('Все циклы пройдены!')


def test():
    wbutt3.place_forget()
    gogogo()


wbutt3 = Button(root, text="Запуск", command=test)
wbutt3.place(x=max_width // 2 - (width + 1), y=max_height // 2 - (height + 1), width=(width * 2 + 2),
             height=(height * 2 + 2))

root.mainloop()
