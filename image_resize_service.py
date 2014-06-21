import os.path as op
import io
import tempfile

from functools import wraps
from flask import Flask, render_template, send_file, request, redirect, url_for, Response, jsonify
from werkzeug.exceptions import NotFound
from PIL import Image
from werkzeug.utils import secure_filename


app = Flask(__name__)
config = op.join(app.root_path, 'production.cfg')
app.config.from_pyfile(config)
__storage = None


def _storage():
    """returns access to the storage (save_image(), get() and exists())"""
    global __storage
    if not __storage:
        if app.config['STORAGE'] == 'FILESYSTEM':
            from storage.filesystem import FileImageStorage

            __storage = FileImageStorage(app.config['FILESYSTEM_STORAGE_SOURCE_DIR'],
                                         app.config['FILESYSTEM_STORAGE_RESIZED_DIR'])
        elif app.config['STORAGE'] == 'APPENGINE':
            from storage.appengine_datastore import DatastoreImageStorage

            __storage = DatastoreImageStorage()
    return __storage


def _calc_size(target_size, image):
    """
    calculates the new image size such that it fits into the target_size bounding box and the proportions are still ok.
    :param target_size: a tuple containing (max_width, max_height) as ints. the bounding box.
    :param image: a PIL or Pillow image
    :return: a tuple containing the new width and height as ints.
    """
    max_width, max_height = target_size
    width, height = image.size
    if max_width >= width and max_height >= height:
        return image.size  # no resize needed
    scale = min(max_width / float(width), max_height / float(height))
    return int(width * scale), int(height * scale)


def _resize_image(project, name, extension, size):
    """
    resizes the image identified by project, name and extension and saves it in the storage.
    :param project: a valid project. YOU NEED TO HAVE CHECKED THAT THIS PROJECT EXISTS
    :param name: the image name (prefix without dimension or extension)
    :param extension: the file extension
    :param size: the name of the new size as a string (e.g "large", "small" depends on your .cfg file)
    """
    if not _storage().exists(project, name, extension):
        raise NotFound()
    im = Image.open(_storage().get(project, name, extension))
    im = im.resize(_calc_size(app.config['PROJECTS'][project]['dimensions'][size], im))
    _storage().save_image(project, name, extension, im, size)
    # not cool, appengines PIL version needs a tempfile and flasks send_file has trouble with it.: tempfile -> BytesIO
    ret_file = tempfile.TemporaryFile()
    im.save(ret_file, 'JPEG')
    ret_file.seek(0)
    return io.BytesIO(ret_file.read())


def _serve_image(project, name, size, extension):
    if not project in app.config['PROJECTS'] \
            or (size and not size in app.config['PROJECTS'][project]['dimensions']):
        raise NotFound()
    if not _storage().exists(project, name, extension, size):
        resized_file = _resize_image(project, name, extension, size)
        return send_file(resized_file, mimetype='image/jpeg')
    return send_file(_storage().get(project, name, extension, size), mimetype='image/jpeg')


def _check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    if not request.form['project'] in app.config['PROJECTS']:
        return False
    correct_user, correct_pass = app.config['PROJECTS'][request.form['project']]['auth']
    return username == correct_user and password == correct_pass


def _authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials and a valid PROJECT in your request', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not _check_auth(auth.username, auth.password):
            return _authenticate()
        return f(*args, **kwargs)

    return decorated


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/img/<project>/<name>.<extension>', methods=['GET'])
def serve_original_image(project, name, extension):
    return _serve_image(project, name, None, extension)


@app.route('/img/<project>/<name>@<size>.<extension>', methods=['GET'])
def serve_resized_image(project, name, size, extension):
    """
    serves the url /img/project/name@size.extension e.g /img/demo_project/welcome@small.jpg
    either returns an image of mimetype image/jpeg or a NotFound() 404 error
    :param project: a project that exists in your production.cfg file
    :param name: the image name
    :param size: the dimension. should be configured in your config file
    :param extension: the image file extension
    :return: an image or a 404 error
    """
    return _serve_image(project, name, size, extension)


@app.route('/uploadform', methods=['GET'])
def upload_form():
    return render_template('upload.html')


def json_response(content, http_statuscode=200):
    resp = jsonify(content)
    resp.status_code = http_statuscode
    return resp

@app.route('/upload', methods=['POST'])
@requires_auth
def upload_image():
    uploaded_file = request.files['file']
    project = request.form['project']
    if not project in app.config['PROJECTS']:
        # as long as this function is @requires_auth protected this cannot happen
        # you cannot login with a wrong project. code stays to protect uploads anyway.
        return json_response({
            'status': 'fail',
            'message': 'project is not configured!'
        }, 500)
    if uploaded_file:
        filename, extension = secure_filename(uploaded_file.filename).rsplit('.', 1)
        if not extension.lower() in app.config['ALLOWED_EXTENSIONS']:
            return json_response({
                'status': 'fail',
                'message': 'unsupported image file extension. check ALLOWED_EXTENSIONS'
            }, 500)
        try:
            uploaded_file.seek(0)
            im = Image.open(uploaded_file)
            _storage().save_image(project, filename, extension, im)
            return json_response({
                'status': 'ok',
                'url': url_for('serve_original_image', project=project, name=filename, extension=extension)
            })
        except IOError:
            return json_response({
                'status' : 'fail',
                'message' : 'your uploaded binary data does not represent a recognized image format.'
            }, 500)
    else:
        return json_response({
            'status': 'fail',
            'message': 'no image uploaded!'
        }, 500)


if __name__ == '__main__':
    if app.config['STORAGE'] == 'APPENGINE':
        raise Exception('cannot use flask dev server and app engine storage! check production.cfg')
    app.run()