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

This Terraform module creates a single EC2 instance and deploys the Trust stack (multiple containers) onto it using Docker Compose.

Design choices and rationale

- We deploy all containers on one EC2 instance (per your request). This is simple and mirrors local docker-compose behaviour.
- The module expects the full docker-compose YAML as a string (`compose_content`) and an optional `.env` file content (`env_content`). This keeps the module generic and avoids hardcoding service definitions.
- The EC2 `user_data` installs Docker and the Compose plugin, writes the compose file and env file, creates the required Docker networks, and starts the stack with `docker compose up -d`.

Important notes

- The compose file used locally refers to external Docker networks; the module's user_data creates matching bridge networks on the host so the compose stack can start.
- The EC2 host should be sized appropriately for the containers you will run.
- For production-grade deployments consider ECS, EKS, or other orchestration systems instead of a single EC2 host.

Usage example (call from the root module)

```hcl
module "trust_ec2" {
  source = "./modules/trust_ec2"

  name_prefix  = "trust"
  instance_type = "t3.small"
  key_name     = var.flip_keypair
  subnet_id    = element(module.flip_vpc.public_subnets, 0)
  security_group_ids = [module.alb_security_group.security_group.id]

  create_elastic_ip = true
}
```

After applying, the module outputs `instance_id` and `public_ip` so you can connect to the host.

Security and IAM

- This module does not attach instance profiles by default. If your containers need access to AWS APIs (SecretsManager, S3, etc.) you should create an IAM role / instance profile and pass it into the module (extension).

Troubleshooting

- If the stack doesn't start, SSH into the instance and inspect `/home/ubuntu/compose_trust.yml`, `/home/ubuntu/.env.development` and the command `docker compose -f compose_trust.yml up -d`.
