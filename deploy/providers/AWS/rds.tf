# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

############################
# RDS PostgreSQL Database
############################

resource "aws_db_subnet_group" "flip_db_subnet_group" {
  name       = "flip-db-subnet-group"
  subnet_ids = module.flip_vpc.private_subnets
}

module "flip_db" {
  source                     = "terraform-aws-modules/rds/aws"
  version                    = "~> 6.0"
  identifier                 = "flip-database"
  engine                     = "postgres"
  engine_version             = "13.22"
  auto_minor_version_upgrade = false
  instance_class             = "db.t3.micro"
  allocated_storage          = 20
  username                   = var.POSTGRES_USER
  db_name                    = var.POSTGRES_DB
  db_subnet_group_name       = aws_db_subnet_group.flip_db_subnet_group.name
  vpc_security_group_ids     = [module.rds_security_group.security_group.id]
  backup_retention_period    = 7
  skip_final_snapshot        = true
  family                     = "postgres13"
}
