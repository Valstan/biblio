import os
import traceback

from bin.utils.send_error import send_error


# Старый вариант чтения паролей из файла logpass.json
def old_change_lp(session):
    try:
        for k, v in session['logpass'].items():
            if session['name_base'] in k and session['name_session'] in k:
                session.update({"login": v[0], "password": v[1]})
                return session
    except Exception as exc:
        send_error(f'Невозможно подобрать логин и пароль, несовпадают ключи name_base и name_session с базой паролей\n'
                   f'Модуль - change_lp\nАШИПКА - {exc}\n{traceback.print_exc()}\n{session}')
    quit()


# Вариант с os.getenv
def change_lp2(session):
    try:
        ns = session['name_session']
        nb = session['name_base']
        if ns in session['arg']['public'] and nb == 'mi' or ns in session['arg']['public'] and nb == 'test':
            session.update({"login": os.getenv('VK_LOGIN_BRIGADIR'), "password": os.getenv('VK_PASSWORD_BRIGADIR')})
        elif session['name_session'] in session['arg']['public'] and session['name_base'] == 'dran':
            session.update({"login": os.getenv('VK_LOGIN_DRAN'), "password": os.getenv('VK_PASSWORD_DRAN')})
        elif session['name_session'] in session['arg']['valstan']:
            session.update({"login": os.getenv('VK_LOGIN_VALSTAN'), "password": os.getenv('VK_PASSWORD_VALSTAN')})
        elif session['name_session'] in session['arg']['instagram']:
            session.update({"login": os.getenv('INSTA_LOGIN_MI'), "password": os.getenv('INSTA_PASSWORD_MI')})
        else:
            session.update({"login": os.getenv('VK_LOGIN_BRIGADIR'), "password": os.getenv('VK_PASSWORD_BRIGADIR')})
        return session
    except Exception as exc:
        send_error(
            f'Модуль - change_lp\nАШИПКА - {exc}\n{traceback.print_exc()}\n{session}')
    quit()


if __name__ == '__main__':
    pass
