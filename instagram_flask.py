import os
from flask import Flask, render_template, request, redirect, url_for, send_from_directory
from werkzeug.utils import secure_filename
from InstagramAPI import InstagramAPI

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_PATH = 'uploads'
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')


@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['file']
        if file and file.filename.rsplit('.', 1)[1].lower() in ['jpg', 'jpeg']:
            filename = secure_filename(file.filename)
            file.save(os.path.join(UPLOAD_DIR, filename))
            return redirect(url_for('show', filename=filename))

    return '''
        <!doctype html>
        <title>Upload new File</title>
        <h1>Загрузка файла</h1>
        <p>Изображение должно быть в форрмате jpg(jpeg)</p>
        <p>Обратите внимание на соотношение длины и ширины фото </p>
            <form method=post enctype=multipart/form-data>
                <input type=file name=file>
                <input type=submit value=Upload>
            </form>    
    '''


@app.route('/uploads/')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route('/show/', methods=['GET', 'POST'])
def show(filename):
    full_path = '/{}/{}'.format(UPLOAD_PATH, filename)
    if request.method == 'POST':
        # При двухшаговой аудентифиукации это не сработает
        instagram = InstagramAPI("malmig_info", "nitro1941")
        instagram.login()  # login
        caption = request.form['📝Мичуринцы не подвели Технический осмотр зерноуборочных комбайнов в колхозе имени']
        photo_path = os.path.join(UPLOAD_DIR, filename)
        # вот и тот самый метод , который выполняет загрузку на Инстаграм
        instagram.uploadPhoto(photo_path, caption=caption)
        # переходим на свою страницу в инстаграм
        redirect_url = 'https://www.instagram.com/{}/'.format('malmig_info')
        return redirect(redirect_url)

    return '''
    <!doctype html>
    <title>Загрузка на инсту</title>
    <h1>Загруженный файл - ''' + full_path + '''</h1> 
    <img src="''' + full_path + '''">
    <form method="post">
      <textarea name="caption" style="width:300px;height:100px;"></textarea><br>
      <input type="submit" value="Загрузить в инстаграмм">
    </form>
    '''


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
