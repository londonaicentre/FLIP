# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Portability helpers for the trust Makefiles: a BSD/macOS dev host (FLIP#1609) and a host
# where the operator has Docker but no root (FLIP#1723).

# `stat` with a GNU format: `-c` on Linux, `-f` on a BSD/macOS dev host (whose octal mode is %Lp).
ifeq ($(shell stat -c %u / 2>/dev/null),)
stat_fmt = stat -f '$(subst %a,%Lp,$(1))'
else
stat_fmt = stat -c '$(1)'
endif

# The remedy every bind-mount ownership guard prints beside `sudo chown` (FLIP#1723). Setting a
# directory's owner to a container's uid needs root, so it belongs to whoever prepares the host, once;
# bring-up only checks it.
prepare_host_dirs_hint = echo "   or have the host owner run, as root: make -C trust prepare-host-dirs KIT=$(KIT)$(if $(HUB), HUB=$(HUB))"

# Who runs each container that writes a bind mount (FLIP#1731). A kit may set FLIP_UID and
# FLIP_GID to the operator's own ids: those containers then run as them, so directories the
# operator creates with mkdir are already right and nothing needs chown. Unset, each image keeps
# its own user. The prod composes carry the same defaults (`${FLIP_UID:-1000}` …), so Make and
# compose cannot disagree about who must own a directory. FLIP_GID follows FLIP_UID when the kit
# sets only one, and is exported because compose reads it from the environment.
FLIP_GID := $(or $(FLIP_GID),$(FLIP_UID))
export FLIP_GID
# trust-api, imaging-api, data-access-api and the FLARE client share one uid.
API_UID := $(or $(FLIP_UID),1000)
API_GID := $(or $(FLIP_GID),1000)
ORTHANC_UID := $(or $(FLIP_UID),999)
ORTHANC_GID := $(or $(FLIP_GID),999)
XNAT_CONTAINER_UID := $(or $(FLIP_UID),1001)
XNAT_CONTAINER_GID := $(or $(FLIP_GID),1001)
