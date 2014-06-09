from functools import partial
import hashlib
import random
import unittest
import io
import sys

from PIL import Image
from werkzeug.exceptions import NotFound

from storage.filesystem import FileImageStorage

import image_resize_service as img_service


try:
    # noinspection PyUnresolvedReferences
    from google.appengine.ext import testbed

    APP_ENGINE_AVAILABLE = True
except ImportError:
    APP_ENGINE_AVAILABLE = False
    sys.stderr.write('App engine not available. Datastore tests are NOT run.\n')


class APITestCase(unittest.TestCase):
    """tests the HTTP API using the configured storage
       make sure to configure STORAGE=APP_ENGINE if you run the code in the dev_appserver or on appspot.
       otherwise you'll get readonly filesystem errors.
    """

    def setUp(self):
        img_service.app.config['TESTING'] = True
        self.app = img_service.app.test_client()
        if APP_ENGINE_AVAILABLE:
            # storage=appengine needs this
            self.testbed = testbed.Testbed()
            self.testbed.activate()
            self.testbed.init_datastore_v3_stub()

    def tearDown(self):
        if APP_ENGINE_AVAILABLE:
            self.testbed.deactivate()

    def test_index(self):
        """content doesn't matter. just make sure there is always a welcome page"""
        rv = self.app.get('/')
        self.assertEqual(rv.status_code, 200)

    def test_wrong_method(self):
        """image url only supports GET"""
        rv = self.app.post('/img/demo_project/welcome@small.jpg')
        self.assertEqual(rv.status_code, 405)  # method not allowed

    def test_invalid_project(self):
        """wrong projects return a 404 error"""
        rv = self.app.get('/img/bla/welcome@small.jpg')
        self.assertEqual(rv.status_code, 404)  # not found

    def test_invalid_size(self):
        """unsupported dimensions return a 404 error"""
        rv = self.app.get('/img/demo_project/welcome@blabla.jpg')
        self.assertEqual(rv.status_code, 404)  # not found

    def test_correct_mimetype(self):
        """make sure we get a jpeg image and it's dimension fits the configuration"""
        for dimension_name, dimension in img_service.app.config['PROJECTS']['demo_project']['dimensions'].items():
            rv = self.app.get('/img/demo_project/welcome@' + dimension_name + '.jpg')
            im = Image.open(io.BytesIO(rv.data))
            max_width, max_height = dimension
            self.assertEqual(rv.status_code, 200)
            self.assertEqual(rv.mimetype, 'image/jpeg')
            self.assertEqual(im.format, 'JPEG')
            self.assertTrue(im.size[0] <= max_width and im.size[1] <= max_height)


class FSStorageTestCase(unittest.TestCase):
    """tests the filesystem storage and therefore defines the storage API
       all storages must have the same behaviour as it's tested here.
       see DataStoreStorageTestCase below.
    """

    def setUp(self):
        self.storage = FileImageStorage(
            img_service.app.config['FILESYSTEM_STORAGE_SOURCE_DIR'],
            img_service.app.config['FILESYSTEM_STORAGE_RESIZED_DIR'])

    def tearDown(self):
        pass

    def test_available_image_exists(self):
        """storage.exists() should return true for available images"""
        self.assertTrue(self.storage.exists('demo_project', 'welcome', 'jpg'))

    def test_unavailable_image_exists_not(self):
        """storage.exists() should return false for unavailable images"""
        self.assertFalse(self.storage.exists('demo_project', 'blabla', 'png'))

    def test_get_existing_image(self):
        """get must return images of type JPEG with a valid dimension"""
        im = Image.open(self.storage.get('demo_project', 'welcome', 'jpg'))
        self.assertTrue(im.size[0] > 0 and im.size[1] > 0)  # image has any valid size
        self.assertEqual(im.format, 'JPEG')

    def test_get_missing_image(self):
        """calling get on an unavailable image should yield a 404 error"""
        with self.assertRaises(NotFound):
            self.storage.get('demo_project', 'blabla', 'png')

    def test_overwrite(self):
        """calling save() on an image identified by (project, name, extension and size) that already exists
           must overwrite the image
        """
        im = Image.open(self.storage.get('demo_project', 'welcome', 'jpg'))
        new_img = io.BytesIO()
        im.save(new_img, 'JPEG')
        new_img.seek(0)
        # save two new images in the storage
        self.storage.save('demo_project', 'overwrite_test', 'jpg', new_img.read())
        new_img.seek(0)
        self.storage.save('demo_project', 'overwrite_test', 'jpg', new_img.read(), size='imaginary_size')
        new_img.close()
        # now overwrite them with an image of another size
        new_width = 15
        new_height = 15
        resized_img_file = io.BytesIO()
        resized_img = im.resize((new_width, new_height))
        resized_img.save(resized_img_file, 'JPEG')
        resized_img_file.seek(0)
        self.assertEqual((new_width, new_height), resized_img.size)
        self.storage.save('demo_project', 'overwrite_test', 'jpg', resized_img_file.read())
        resized_img_file.seek(0)
        self.storage.save('demo_project', 'overwrite_test', 'jpg', resized_img_file.read(), size='imaginary_size')
        # check that the new image has new_width and new_height
        new_fd = self.storage.get('demo_project', 'overwrite_test', 'jpg')
        new_fd_imaginary = self.storage.get('demo_project', 'overwrite_test', 'jpg', 'imaginary_size')
        new_im = Image.open(new_fd)
        self.assertEqual((new_width, new_height), new_im.size)
        self.assertEqual(self._md5sum_from_file(new_fd), self._md5sum_from_file(resized_img_file))
        self.assertEqual(self._md5sum_from_file(new_fd_imaginary), self._md5sum_from_file(resized_img_file))
        new_fd.close()
        new_fd_imaginary.close()
        resized_img_file.close()

    def test_save_new_img(self):
        """after saving a not yet existing image it must be available in the storage """
        fd = self.storage.get('demo_project', 'welcome', 'jpg')
        im = Image.open(fd)
        im.resize((20, 30))
        resized_img = io.BytesIO()
        im.save(resized_img, 'JPEG')
        resized_img.seek(0)
        # invent a random size to make sure it does not yet exist in our storage
        size = 'xtrasmall'
        random.seed()
        while self.storage.exists('demo_project', 'welcome', 'jpg', size):
            size += str(random.randint(0, 10))
        self.assertFalse(self.storage.exists('demo_project', 'welcome', 'jpg', size))
        self.storage.save('demo_project', 'welcome', 'jpg', resized_img.read(), size)
        self.assertTrue(self.storage.exists('demo_project', 'welcome', 'jpg', size))
        new_fd = self.storage.get('demo_project', 'welcome', 'jpg', size)
        # the storage must return the same binary data that we stored
        self.assertEqual(self._md5sum_from_file(resized_img), self._md5sum_from_file(new_fd))

    def test_image_fd_is_readonly(self):
        """the filedescriptor returned by get must not allow to change the image in the storage
            either directly raise an error at write() time or allow write() calls but do not
            store it (see the different implementations in FS and Datastore Testcases)
        """
        fd = self.storage.get('demo_project', 'welcome', 'jpg')
        with self.assertRaises(IOError):
            fd.seek(0)
            fd.write(b'asdf')

    def _md5sum_from_file(self, f):
        f.seek(0)
        d = hashlib.md5()
        for buf in iter(partial(f.read, 128), b''):
            d.update(buf)
        return d.hexdigest()


if APP_ENGINE_AVAILABLE:
    """run all the storage tests on the appengine datastore too"""

    class DatastoreStorageTestCase(FSStorageTestCase):
        def setUp(self):
            super(DatastoreStorageTestCase, self).setUp()
            self.testbed = testbed.Testbed()
            self.testbed.activate()
            self.testbed.init_datastore_v3_stub()
            from storage.appengine_datastore import DatastoreImageStorage  # must be imported AFTER creating a testbed

            self.storage = DatastoreImageStorage()
            # put the demo_image into the datastore
            f = file('demo_image_dir/images/demo_project/welcome.jpg', 'r')
            self.storage.save('demo_project', 'welcome', 'jpg', f.read())
            f.close()

        def tearDown(self):
            self.testbed.deactivate()
            super(DatastoreStorageTestCase, self).tearDown()

        def test_image_fd_is_readonly(self):
            """since the appengine implementation returns a io.BytesIO it is writable
               let's just test that our writes do not end up in the datastore
            """
            # overwrite the fd with a resized image
            fd = self.storage.get('demo_project', 'welcome', 'jpg')
            im = Image.open(fd)
            new_im = im.resize((im.size[0] + 1, im.size[0] + 1))
            fd.seek(0)
            new_im.save(fd, 'JPEG')
            fd.close()
            # load the image again and compare
            fd = self.storage.get('demo_project', 'welcome', 'jpg')
            im = Image.open(fd)
            self.assertNotEqual(im.size, new_im.size)

if __name__ == '__main__':
    unittest.main()