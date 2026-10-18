"""Private file storage: MEDIA_URL se serve nahi hota, sirf permission check wale view se download hota hai."""
import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateStorage(FileSystemStorage):
    """Files settings.PRIVATE_ROOT mein rakhta hai (MEDIA_ROOT ke bahar), aur koi public URL nahi deta."""

    @property
    def base_location(self):
        return settings.PRIVATE_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None
