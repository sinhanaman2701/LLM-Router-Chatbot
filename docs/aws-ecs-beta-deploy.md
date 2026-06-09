# AWS ECS/Fargate Beta Deploy

This repo is ready to run on AWS without a custom domain name.

## Recommended Shape

- **ECR repo 1**: `anacity-chatbot-web`
- **ECR repo 2**: `anacity-chatbot-mock-server`
- **ECS cluster**: Fargate
- **Task definition**: 2 containers in one task
  - `web` on port `8000`
  - `mock-server` on port `3000`
- **Service**: 1 ECS service
- **ALB**: public, forwarding to `web:8000`
- **RDS PostgreSQL**
- **ElastiCache node-based Valkey/Redis**
- **CloudWatch Logs**

This keeps `web` talking to `mock-server` via `http://127.0.0.1:3000`.

## Build And Push

Build web image from repo root:

```bash
docker build -t anacity-chatbot-web .
```

Build mock-server image:

```bash
docker build -t anacity-chatbot-mock-server ./mock-server
```

Tag and push both images to ECR:

```bash
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com

docker tag anacity-chatbot-web <account>.dkr.ecr.<region>.amazonaws.com/anacity-chatbot-web:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/anacity-chatbot-web:latest

docker tag anacity-chatbot-mock-server <account>.dkr.ecr.<region>.amazonaws.com/anacity-chatbot-mock-server:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/anacity-chatbot-mock-server:latest
```

## ECS Task Definition

Use one Fargate task definition with:

- CPU: `1024`
- Memory: `2048`
- Network mode: `awsvpc`
- OS: Linux

Container `web`:

- Image: ECR `anacity-chatbot-web:latest`
- Port mapping: `8000`
- Command: default image command
- Health check:

```bash
CMD-SHELL,curl -f http://127.0.0.1:8000/health || exit 1
```

Container `mock-server`:

- Image: ECR `anacity-chatbot-mock-server:latest`
- Port mapping: `3000`
- Health check:

```bash
CMD-SHELL,curl -f http://127.0.0.1:3000/health || exit 1
```

## Environment Variables For `web`

Set these on the `web` container:

- `APP_ENV=production`
- `DATABASE_URL=<RDS connection string>`
- `REDIS_URL=<ElastiCache connection string>`
- `SESSION_HMAC_SECRET=<long random secret>`
- `MOCK_SERVER_URL=http://127.0.0.1:3000`
- `MOCK_SERVER_EMAIL=dn.user.a@gmail.com`
- `MOCK_SERVER_PASSWORD=password`
- `OLLAMA_API_KEY=<your key>`
- `OLLAMA_BASE_URL=https://ollama.com`
- `OLLAMA_MODEL=gemma4:31b-cloud`

## Release / Migration

Before first production traffic, run:

```bash
docker run --rm \
  -e APP_ENV=production \
  -e DATABASE_URL=<RDS connection string> \
  -e REDIS_URL=<ElastiCache connection string> \
  -e SESSION_HMAC_SECRET=<long random secret> \
  -e MOCK_SERVER_URL=http://127.0.0.1:3000 \
  -e MOCK_SERVER_EMAIL=dn.user.a@gmail.com \
  -e MOCK_SERVER_PASSWORD=password \
  -e OLLAMA_API_KEY=<your key> \
  -e OLLAMA_BASE_URL=https://ollama.com \
  -e OLLAMA_MODEL=gemma4:31b-cloud \
  <account>.dkr.ecr.<region>.amazonaws.com/anacity-chatbot-web:latest \
  bash scripts/release_web.sh
```

For repeatable operations, this should become an ECS one-off task using the same image and env vars.

## Networking

- Put ECS, RDS, and ElastiCache in the same VPC
- Allow ECS task security group outbound to RDS and ElastiCache
- Allow RDS inbound from ECS task security group on PostgreSQL port
- Allow ElastiCache inbound from ECS task security group on Redis/Valkey port
- ALB should forward public traffic only to container `web` on port `8000`

## Public Access

You do **not** need a domain name for beta.

Use the ALB DNS name, for example:

```text
http://my-beta-alb-123456.ap-south-1.elb.amazonaws.com
```

## Notes

- This repo uses Redis optimistic transactions (`WATCH/MULTI/EXEC`), so prefer **node-based ElastiCache Valkey/Redis**, not ElastiCache Serverless.
- `mock-server` is still an in-memory demo backend. Restarting its container resets its state.
