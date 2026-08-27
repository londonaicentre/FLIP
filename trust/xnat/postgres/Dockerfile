# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

ARG PG_VERSION
FROM postgres:${PG_VERSION}

COPY XNAT.sql /docker-entrypoint-initdb.d/
COPY guard-xnat-db-passwords.sh /usr/local/bin/

# Set the correct permissions for the initialization script and entrypoint guard
RUN chmod 644 /docker-entrypoint-initdb.d/XNAT.sql && \
    chmod 755 /usr/local/bin/guard-xnat-db-passwords.sh

# Refuse to boot with placeholder/weak XNAT DB passwords (FLIP-PT-056) — the
# stock entrypoint applies them silently at first initdb. The guard execs the
# stock docker-entrypoint.sh, so CMD stays the base image's default command.
ENTRYPOINT ["guard-xnat-db-passwords.sh"]
CMD ["postgres"]
