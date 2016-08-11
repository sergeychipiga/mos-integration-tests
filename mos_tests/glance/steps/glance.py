"""
Glance steps.

@author: schipiga@mirantis.com
"""

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from mos_tests.functions.common import wait
from mos_tests.steps import BaseSteps, step

__all__ = [
    'GlanceSteps'
]


class GlanceSteps(BaseSteps):
    """Glance steps."""

    @step
    def create_image(self, image_name, image_path, disk_format='qcow2',
                     container_format='bare', check=True):
        """Step to create image."""
        image = self._client.create(name=image_name, disk_format=disk_format,
                                    container_format=container_format)
        self._client.upload(image.id, open(image_path, 'rb'))

        if check:
            self.check_image_status(image, 'active', timeout=180)

        return image

    @step
    def delete_image(self, image, check=True):
        """Step to delete image."""
        self._client.delete(image.id)

        if check:
            self.check_image_presence(image, present=False, timeout=180)

    @step
    def create_images(self, image_names, image_path, disk_format='qcow2',
                      container_format='bare', check=True):
        """Step to create images."""
        images = []
        for image_name in image_names:
            image = self.create_image(image_name, image_path, disk_format,
                                      container_format, check=False)
            images.append(image)

        if check:
            for image in images:
                self.check_image_status(image, 'active', timeout=180)

        return images

    @step
    def delete_images(self, images, check=True):
        """Step to delete images."""
        for image in images:
            self.delete_image(image, check=False)

        if check:
            for image in images:
                self.check_image_presence(image, present=False, timeout=180)

    @step
    def check_image_presence(self, image, present=True, timeout=0):
        """Verify step to check image is present."""
        def predicate():
            try:
                self._client.get(image.id)
                return present
            except Exception:
                return not present

        wait(predicate, timeout_seconds=timeout)

    @step
    def check_image_status(self, image, status, timeout=0):
        """Verify step to check image status."""
        def predicate():
            image.update(self._client.get(image.id))
            return image.status.lower() == status.lower()

        wait(predicate, timeout_seconds=timeout)
