import pywhatkit as pwt

phone_number = "+79960452043"
msg = "Исчо тест"
hour = 13
minute = 9

pwt.sendwhatmsg(phone_number, msg, hour, minute, wait_time=25, tab_close=True, close_time=15)

