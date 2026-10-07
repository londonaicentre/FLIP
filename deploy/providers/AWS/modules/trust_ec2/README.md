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

Trust EC2 module
================

This Terraform module creates the cloud Trust host: one Ubuntu 24.04 EC2 instance in a private subnet. It
provisions the instance only. **It does not install Docker or start the trust stack** — the module sets no
`user_data`. Host provisioning (Docker, application directories under `/opt/flip`, the FL participant kit, the
observability config, seeding) is done afterwards by the Ansible play `deploy/providers/AWS/site.yml`
(`make ansible-init`), and the stack is brought up by `make deploy-trust` / `make -C trust up-trust-ec2`.

The root module calls it once, gated on `var.deploy_trust_ec2` (set false for a hub-only deployment where every
trust runs on-prem).

What the instance gets

- AMI: the current Canonical Ubuntu 24.04 image, read from the public SSM parameter. `lifecycle.ignore_changes =
  [ami]` stops a new Canonical release from replacing the host (and its data) on the next apply; `-replace`
  still launches from the current image.
- Root volume: 100 GB gp3, encrypted, deleted with the instance.
- No public IP. IMDSv2 only (`http_tokens = "required"`), hop limit 2 so a containerised AWS SDK call can still
  reach IMDS.
- The instance profile passed in `iam_instance_profile_name` (the root passes the Trust's own narrow profile:
  SSM, CloudWatch, read-only S3 on the AI Centre bucket).

Inputs

| Variable | Required | Notes |
|----------|----------|-------|
| `subnet_id` | yes | Private subnet for the instance |
| `security_group_ids` | yes | The root passes the trust security group, not the Central Hub one |
| `iam_instance_profile_name` | yes | Existing instance profile to attach |
| `key_name` | no | EC2 key pair name (the root passes `aws_key_pair.host_key`) |
| `instance_type` | no | Defaults to `t3.small`; the root passes `t3.xlarge` |
| `name_prefix` | no | Used in the `Name` tag (`trust-host-<prefix>`) |
| `AWS_REGION` | no | Declared but not used by the module |

Output: `instance_id`.

Usage (as in the root `main.tf`)

```hcl
module "trust_ec2" {
  count  = var.deploy_trust_ec2 ? 1 : 0
  source = "./modules/trust_ec2"

  name_prefix               = "trust"
  instance_type             = "t3.xlarge"
  key_name                  = aws_key_pair.host_key.key_name
  subnet_id                 = element(local.app_subnet_ids, 0)
  security_group_ids        = [module.trust_security_group.security_group.id]
  iam_instance_profile_name = aws_iam_instance_profile.trust_ec2_profile.name
}
```

Access

Connect over SSH-over-SSM (`make ssh-config`, then `ssh flip-trust`); no inbound port is open. XNAT and Orthanc
are reached through SSM port forwarding. If the stack is not running after a deploy, start with the Ansible
output from `make ansible-init` and the containers under `/opt/flip` on the host.
