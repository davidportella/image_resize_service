import tempfile


class ImageStorage(object):
    """represents an image storage"""

    def exists(self, project, name, extension, size=None):
        """true if image is in this storage, false otherwise"""
        raise NotImplemented()

    def save_image(self, project, name, extension, pil_image, size=None):
        """put the given pil image to the storage"""
        f = tempfile.TemporaryFile()
        pil_image.save(f, 'JPEG')
        f.seek(0)
        self.save(project, name, extension, f.read(), size)

    def save(self, project, name, extension, binary_data, size=None):
        """saves the binary_image_data by creating or overwriting the project-name-extension-size images"""
        raise NotImplemented()

    def get(self, project, name, extension, size=None):
        """returns a filedescriptor to the image (file, or io)"""
        raise NotImplemented()