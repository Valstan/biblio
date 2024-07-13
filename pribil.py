import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import DateFormatter

# Укажите путь к вашему файлу
file_path = 'pribil.txt'

try:
    # Открываем файл в режиме чтения
    with open(file_path, 'r', encoding='utf-8') as file:
        # Читаем содержимое файла в переменную
        data = file.read()

except FileNotFoundError:
    print(f"Файл не найден: {file_path}")
    exit()
except IOError:
    print(f"Ошибка при чтении файла: {file_path}")
    exit()

# Преобразуем данные в список строк
lines = data.split('\n')

# Инициализируем списки для хранения информации
dates = []
profits = []

# Парсим данные
for i in range(0, len(lines)):
    if "#" in lines[i]:
        for ii in range(0, 2):
            if i + 1 + ii + 1 < len(lines) and lines[i + 1 + ii + 1] != "--":
                try:
                    profit = float(lines[i + 1 + ii + 1])
                    if profit > 0:
                        for iii in range(0, 2):
                            if i + 1 + ii + 1 + iii + 1 < len(lines) and "Продать" in lines[i + 1 + ii + 1 + iii + 1]:
                                sell_date = lines[i + 1 + ii + 1 + iii + 1][7:12]
                                dates.append(sell_date)
                                profits.append(profit)
                except ValueError:
                    continue

# Создаем DataFrame
df = pd.DataFrame({'Date': dates, 'Profit': profits})

# Преобразуем даты в формат datetime
df['Date'] = pd.to_datetime(df['Date'], format='%m-%d')

# Группируем данные по датам и суммируем прибыль
daily_profit = df.groupby('Date')['Profit'].sum().reset_index()

# Сортируем данные по датам
daily_profit = daily_profit.sort_values('Date')

# Отображаем таблицу с прибылью по датам
print(daily_profit)

# Создаем график
plt.figure(figsize=(12, 6))
plt.plot(daily_profit['Date'], daily_profit['Profit'], marker='o')

# Настраиваем оси
plt.xlabel('Date')
plt.ylabel('Profit')
plt.title('Daily Profit Visualization')

# Форматируем даты на оси X
plt.gca().xaxis.set_major_formatter(DateFormatter('%m-%d'))
plt.gcf().autofmt_xdate()  # Rotate and align the tick labels

# Добавляем сетку
plt.grid(True, linestyle='--', alpha=0.7)

# Показываем график
plt.tight_layout()
plt.show()