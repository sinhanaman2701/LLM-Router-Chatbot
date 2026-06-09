# EC2 POC Deploy

This is the fastest path to a shareable public URL for the chatbot.

It runs everything on one EC2 instance:

- `web`
- `mock-server`
- `postgres`
- `redis`

## What You Need

- An EC2 instance running Ubuntu
- Port `22` open from your IP
- Port `8000` open publicly
- Docker installed
- AWS CLI installed and authenticated

## Recommended EC2 Shape

- AMI: `Ubuntu Server 24.04 LTS`
- Instance type: `t3.small` or `t4g.small`
- Storage: `20 GiB`

## Security Group

Inbound rules:

- `SSH` on port `22` from `My IP`
- `Custom TCP` on port `8000` from `0.0.0.0/0`

## Server Setup

SSH into the instance:

```bash
ssh -i /path/to/key.pem ubuntu@<ec2-public-ip>
```

Install Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl unzip
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker ubuntu
newgrp docker
```

Install the AWS CLI if needed:

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws configure
```

## Pull The Images

```bash
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 874041194383.dkr.ecr.ap-south-1.amazonaws.com
docker pull 874041194383.dkr.ecr.ap-south-1.amazonaws.com/llm-router-chatbot-web:latest
docker pull 874041194383.dkr.ecr.ap-south-1.amazonaws.com/llm-router-chatbot-mock-server:latest
```

## Deploy The Stack

Copy the repo to the EC2 box and use these files:

- `docker-compose.ec2.yml`
- `.env.ec2.example`

Prepare env vars:

```bash
cp .env.ec2.example .env.ec2
```

Edit `.env.ec2` and set:

- `POSTGRES_PASSWORD`
- `SESSION_HMAC_SECRET`
- `OLLAMA_API_KEY`

Start infrastructure:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml up -d postgres redis mock-server
```

Run migrations:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml --profile ops run --rm migrate
```

Start the app:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml up -d web
```

Check health:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml ps
curl http://127.0.0.1:8000/health
```

Public URL:

```text
http://<ec2-public-ip>:8000/
```

## Useful Commands

Tail logs:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml logs -f web
```

Restart:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml restart web
```

Stop everything:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml down
```

Stop everything and delete data:

```bash
docker compose --env-file .env.ec2 -f docker-compose.ec2.yml down -v
```

## Notes

- This path ignores the RDS and ElastiCache resources already created.
- Everything runs on one machine, so this is for POC/demo use only.
- Data persists only as long as the Docker volumes on the EC2 instance are kept.
