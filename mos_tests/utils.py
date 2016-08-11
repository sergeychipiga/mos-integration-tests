"""
Utils.

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

import os
import tempfile
import uuid

from mos_tests.steps import step


@step
def generate_ids(prefix=None, postfix=None, count=1, length=None):
    """Generate unique identificators, based on uuid.

    Arguments:
        - prefix: prefix of uniq ids, default is None.
        - postfix: postfix of uniq ids, default is None.
        - count: count of uniq ids, default is 1.
        - length: length of uniq ids, default is not limited.

    Returns:
        - generator of uniq ids.
    """
    for _ in range(count):
        uid = str(uuid.uuid4())
        if prefix:
            uid = '{}-{}'.format(prefix, uid)
        if postfix:
            uid = '{}-{}'.format(uid, postfix)
        if length:
            uid = uid[0:length]
        yield uid


@step
def generate_files(prefix=None, postfix=None, folder=None, count=1, size=1024):
    """Generate files with unique names.

    Arguments:
        - prefix: prefix of uniq ids, default is None.
        - postfix: postfix of uniq ids, default is None.
        - folder: folder to create uniq files.
        - count: count of uniq ids, default is 1.
        - size: size of uniq files, default is 1Mb.

    Returns:
        - generator of files with uniq names.
    """
    folder = folder or tempfile.mkdtemp()
    if not os.path.isdir(folder):
        os.makedirs(folder)

    for uid in generate_ids(prefix, postfix, count):
        file_path = os.path.join(folder, uid)

        with open(file_path, 'wb') as f:
            f.write(os.urandom(size))

        yield file_path
