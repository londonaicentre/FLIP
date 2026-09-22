<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Trust host roles

One set of Ansible roles prepares any host for the trust Compose stack. A play per host type composes
them and states only what differs: [`../onprem.yml`](../onprem.yml) for a site-owned host,
[`deploy/providers/AWS/site.yml`](../../../../deploy/providers/AWS/site.yml) for the EC2 trust (which
adds its own AWS-only plays: Terraform-derived inventory, AWS CLI, SSM bastion, CloudWatch). A future
cloud play (FLIP#1213, the Azure VM) is another composition of the same roles.

| Role | Does | Varies by host type through |
| ---- | ---- | --------------------------- |
| `flip_base_packages` | apt-installs curl, unzip, tar, openssl | `flip_base_packages` |
| `flip_docker` | Docker via `geerlingguy.docker` | `flip_docker_users` — empty by default; **docker group = root**. EC2 opts `ubuntu` in (SSM-only host); on-prem never does |
| `flip_trust_dirs` | `/opt/flip`, the images trees and their per-net slices (backend-aware ownership), the XNAT bind mounts (uid 1001), optional Loki/Grafana host dirs | `flip_app_dirs`, `flip_images_base_dirs` (on-prem: one tree; EC2: one per FL kit slot), `fl_backend`, `flip_observability_volume_dirs` |
| `flip_fl_kit` | `fl_kit_dir`, then by `fl_kit_source`: `s3` syncs this host's slot out of the hub's bucket (the FLIP#965 guards — wipe first, assert the result), `precreate` lays down the tree for a hand-extracted tarball, `none` does nothing more | `fl_kit_source`, `trust_num`, `flip_aicentre_bucket`, `fl_kit_date` |
| `flip_observability_config` | copies `trust/observability/**` into the dir the production compose mounts (`/opt/flip/config/observability`) | `observability_dest_dir` |
| `flip_omop_vocab` | streams the licensed core vocabulary into the seeded cluster; bundle from the hub's S3 bucket via an instance role, so EC2-only | `vocab_s3_bucket`, `omop_postgres_*`, `omop_db_tag` |

There is no seed or restore role. Since FLIP#1187 the OMOP and Orthanc stores start empty and are seeded from the
canonical dataset by the one procedure in `trust/seed_trust.sh`: on-prem at `make -C trust up-trust`
(`ensure-seeded`), on the EC2 trust by site.yml's own seed play (it runs the procedure in throwaway
containers against the stopped stack, with guards `deploy/providers/AWS/tests/test_trust_seed_*.py`
assert over site.yml). Tags on the EC2 play are `flkit`, `data`, `seed`, `omop`, `omop-vocab`,
`bastion` — play-level tags reach every task a role brings in, and the two image-dir tasks inside
`flip_trust_dirs` carry `flkit` themselves so a re-stage lays them down again.

Role lookup: `onprem.yml` finds `./roles` beside itself; `site.yml` finds them through
`deploy/providers/AWS/ansible.cfg` (`roles_path`), which also keeps the `ansible-galaxy` install
location on the path for `geerlingguy.docker`.

Guards over this tree (all static, no host needed): `../tests/test_onprem_playbook.py` walks the
on-prem play *through* its roles (docker-group grant, per-net dir ownership, `fl_backend` default);
`trust/xnat/tests/test_data_dir_ownership.py` pins the XNAT bind-mount uid in `flip_trust_dirs` to
`trust/xnat/Makefile`; `deploy/providers/AWS/tests/test_trust_kit_staging.py` pins the FLIP#965
properties of the two S3 stages in `flip_fl_kit/tasks/s3.yml`.
