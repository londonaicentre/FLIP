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

# The cluster lives in pg<major>/ under the volume, so a new major starts beside the old cluster
# rather than on top of it. The old one stays where it was, for a rollback, until
# xnat-db-remove-old-cluster deletes it (FLIP#1812).
ENV PGDATA=/var/lib/postgresql/data/pg${PG_MAJOR}

COPY XNAT.sql /docker-entrypoint-initdb.d/
# zz- runs it after XNAT.sql, which creates the role and database it restores into.
COPY restore-xnat-dump.sh /docker-entrypoint-initdb.d/zz-restore-xnat-dump.sh
COPY guard-xnat-db-data.sh guard-xnat-db-passwords.sh /usr/local/bin/

RUN chmod 644 /docker-entrypoint-initdb.d/XNAT.sql && \
    chmod 755 /docker-entrypoint-initdb.d/zz-restore-xnat-dump.sh \
              /usr/local/bin/guard-xnat-db-data.sh /usr/local/bin/guard-xnat-db-passwords.sh

# Refuse a data dir it would mishandle (FLIP#1812), then boot with placeholder/weak XNAT DB
# passwords refused (FLIP-PT-056) — the stock entrypoint applies them silently at first initdb.
# The guards exec the stock docker-entrypoint.sh, so CMD stays the base image's default command.
ENTRYPOINT ["guard-xnat-db-data.sh"]
CMD ["postgres"]
