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

# The image a throwaway root container runs to set a bind mount's owner when the operator cannot
# chown: the trust's own omop-db (postgres, root by default), at the tag the stack runs. It comes
# from the registry the stack already pulls from; alpine would need Docker Hub, which a TRE proxy
# may refuse.
ROOT_HELPER_IMAGE = ghcr.io/londonaicentre/omop-db:$(or $(OMOP_DB_TAG),$(DOCKER_TAG))
