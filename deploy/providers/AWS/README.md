<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# FLIP AWS Terraform/OpenTofu and Ansible Infrastructure

Terraform/OpenTofu and Ansible Infrastructure as Code to deploy FLIP application stack to AWS.

## Prerequisites

1. **AWS CLI configured** with SSO access (see [deploy README](../../README.md))
2. **Terraform >= 1.13.1** or OpenTofu installed
3. **Python 3.12+**
4. **UV environment manager** installed via [uv installation guide](https://docs.astral.sh/uv/guides/install-python/)
5. **GitHub CLI** installed via [GitHub CLI installation guide](https://cli.github.com/)
6. **SSH key pair** created at `~/.ssh/host-aws` (see [deploy README](../../README.md))
7. **Environment files** configured: (see [deploy README](../../README.md))
   - `.env.stag` (staging) or `.env.production` (production) in project root
   - Service-specific `.env` files (see Environment Configuration section)

### Required AWS Permissions

Your AWS IAM role/user needs the following permissions for provisioning infrastructure:

- **SSM**: `ssm:GetParameter` for fetching AMI IDs
- **EC2**: Full access (VPC, instances, security groups, key pairs, Elastic IPs)
- **RDS**: `rds:CreateDBSubnetGroup`, `rds:CreateDBParameterGroup`, `rds:CreateDBInstance`
- **CloudWatch Logs**: `logs:*`
- **Secrets Manager**: Full access for storing database credentials and API secrets
- **IAM**: Create and manage roles for EC2 instances
- **Application Load Balancer**: Create and manage ALBs
- **SES**: Manage email templates (optional for email functionality)

Managed policies that cover these requirements:

- `AmazonEC2FullAccess`
- `AmazonRDSFullAccess`
- `CloudWatchLogsFullAccess`
- `SecretsManagerReadWrite`
- `IAMFullAccess`
- `ElasticLoadBalancingFullAccess`
- `AmazonSESFullAccess` (optional)

**Note**: The deployed EC2 instances use minimal IAM permissions (SSM and CloudWatch only) following the principle of least privilege.

## Deployment Workflow

### Full Stack Deployment

The complete deployment process is automated via the `full-deploy` target:

```bash
cd deploy/providers/AWS
make full-deploy PROD=stag  # For staging
# OR
make full-deploy PROD=true  # For production
```

This command executes the following steps in order:

1. **`github-login`**: Authenticate with GitHub CLI
2. **`aws-login`**: Authenticate with AWS SSO
3. **`init`**: Initialize Terraform with environment-specific S3 backend
4. **`import-all`**: Import existing AWS resources to prevent replacement
5. **`plan`**: Generate and review Terraform execution plan
6. **`apply`**: Apply infrastructure changes
7. **`ssh-config`**: Update `~/.ssh/config` with EC2 instance IPs
8. **`ansible-init`**: Configure EC2 instances with Docker and CloudWatch
9. **`deploy-centralhub`**: Deploy Central Hub services via Docker Compose
10. **`deploy-trust`**: Deploy Trust services via Docker Compose
11. **`status`**: Run comprehensive health checks

### Manual Step-by-Step Deployment

For debugging or selective deployment, run individual steps:

```bash
# 1. Login to AWS
make aws-login

# 2. Initialize Terraform (creates/configures S3 backend)
make init

# 3. Import existing resources (prevents replacement errors)
make import-all

# 4. Plan changes
make plan

# 5. Apply infrastructure
make apply

# 6. Configure SSH access
make ssh-config

# 7. Setup EC2 instances with Ansible
make ansible-init

# 8. Deploy services
make deploy-centralhub
make deploy-trust

# 9. Check status
make status
```

### Deployment to Different Environments

**Staging:**

```bash
make full-deploy PROD=stag
```

**Production:**

```bash
make full-deploy PROD=true
```

The `PROD` variable determines which environment files are loaded:

- `PROD=stag` → Uses `.env.stag`, `flip-api/.env.stag`
- `PROD=true` → Uses `.env.production`, `flip-api/.env.production`

### Destroy Infrastructure

The destroy process preserves critical resources (Cognito, Secrets, S3) while safely removing infrastructure:

```bash
make destroy
```

**What gets destroyed:**

- Trust EC2 instance
- Central Hub EC2 instance
- Application Load Balancer
- RDS database (with skip-final-snapshot)
- VPC, subnets, security groups, NAT gateway
- IAM roles and policies
- Elastic IPs

**What gets preserved:**

- Cognito User Pool and users (authentication data)
- Secrets Manager secret (FLIP_API configuration)
- S3 bucket (application data)

### Status Checking

The deployment includes a comprehensive Python-based status checker:

```bash
make status
```

This validates:

- ✅ Terraform state and outputs
- ✅ VPC, subnets, and security group configurations
- ✅ EC2 instance health (Central Hub and Trust)
- ✅ RDS database connectivity
- ✅ Secrets Manager access
- ✅ S3 bucket accessibility
- ✅ Cognito User Pool configuration
- ✅ Docker services on EC2 instances
- ✅ HTTP endpoint availability
- ✅ SSH connectivity
- ✅ CloudWatch Logs configuration

## Troubleshooting

### Quick Diagnosis

First, run the automated status check script to identify issues:

```bash
make status
```

This will automatically diagnose:

- AWS resource health
- Network connectivity
- Application endpoint availability
- Docker container status
- System resource usage

Review the output for failed checks and follow the specific troubleshooting steps below.

## Architecture

### Services

Two-Instance Setup

1. **Central Hub EC2**: Hosts the main application services
   - flip-ui (Frontend)
   - flip-api (Backend API)
   - fl-api-net-1 (Federated Learning API for Network 1)
   - fl-api-net-2 (Federated Learning API for Network 2)
   - fl-server-net-1 (Federated Learning Server for Network 1)
   - fl-server-net-2 (Federated Learning Server for Network 2)

2. **Trust EC2**: Hosts trust-related services (automatically provisioned)
   - trust-api
   - imaging-api
   - data-access-api
   - fl-client-net-1 (FL Client for Network 1)
   - fl-client-net-2 (FL Client for Network 2)
   - XNAT (medical imaging platform)
   - Orthanc (DICOM server)
   - OMOP database

| Application Component |
|----------------------|
| **Central Hub Services** |
| FLIP API ✅ |
| FLIP UI ✅ |
| FL API ✅ |
| FL Server ✅ |
| **Trust Services** |
| Trust API ✅ |
| Imaging API ✅ |
| Data Access API ✅ |
| XNAT (medical imaging) ✅ |
| Orthanc (DICOM server) ✅ |

```sh
┌─────────────────┐
│   Internet        │
└────────┬────────┘
         │
    ┌────▼────┐
    │   ALB    │
    └────┬────┘
         │
    ┌────▼──────────────────────┐
    │  Central Hub EC2             │
    │  - flip-ui               │
    │  - flip-api              │
    │  - fl-api                    │
    │  - fl-server                 │
    └───────────────────────────┘
         │
         │ Trust Network
         │
    ┌────▼─────────────────────┐
    │  Trust EC2            │
    │  - trust-api                │
    │  - imaging-api              │
    │  - data-access-api          │
    │  - XNAT                     │
    │  - Orthanc                  │
    │  - fl-client                │
    └──────────────────────────┘
```

![AWS architecture](docs/AWS.png "AWS architecture")

### Central Hub Infrastructure

- **VPC**: Custom VPC with public/private subnets
- **Central Hub EC2**: Single t3.small instance running Docker containers (UI, API, FL services)
- **Trust EC2**: Separate t3.small instance running Trust services via Docker Compose
  - Deployed using custom Terraform module (`modules/trust_ec2`)
  - Automatic Docker and Docker Compose installation via user_data
  - Automatic Docker network creation for inter-service communication
  - Optional Elastic IP for static addressing
- **ALB**: Application Load Balancer for traffic routing
- **RDS**: PostgreSQL 13.22 managed database
- **CloudWatch**: Logging and monitoring for both EC2 instances
- **Secrets Manager**: Secure storage for API secrets and database credentials
- **S3 Backend**: Remote state storage with environment-specific buckets

### Trust Infrastructure

The Trust services are deployed on separate EC2 instance(s) using the `trust_ec2` Terraform module:

- Automated Docker and Docker Compose installation
- Trust compose stack deployment via user_data script
- Automatic Docker network creation for inter-service communication
- Optional Elastic IP for static addressing

### Port configuration

| Port | Service | Status | Purpose |
|------|---------|---------|---------|
| **22** | SSH | 🟢 **OPEN** | Remote administration |
| **80** | HTTP | 🟢 **OPEN** | ALB traffic |
| **3000** | FLIP UI | 🟢 **OPEN** | Frontend application |
| **8000** | FLIP API | 🟢 **OPEN** | Backend API |
| **8001** | FL API | 🟢 **OPEN** | Federated learning API |
