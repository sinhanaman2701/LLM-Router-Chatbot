# AWS ECS/Fargate Deploy Assets

This folder contains concrete templates for the beta AWS deployment:

- `ecs-task-definition.template.json`
- `ecs-service.template.json`

These are templates, not ready-to-apply files. Replace the `__PLACEHOLDER__`
values with your AWS-specific values before running `aws ecs` commands.

## Recommended Beta Topology

- 1 ECS cluster on Fargate
- 1 ECS service
- 1 task definition with 2 containers:
  - `web` on port `8000`
  - `mock-server` on port `3000`
- 1 public ALB targeting `web:8000`
- 1 RDS PostgreSQL instance
- 1 node-based ElastiCache Redis/Valkey cluster
- 2 ECR repos:
  - `llm-router-chatbot-web`
  - `llm-router-chatbot-mock-server`

## Files

### `ecs-task-definition.template.json`

Registers the ECS task definition for the application containers, including:

- container ports
- CloudWatch log groups
- health checks
- application env vars

### `ecs-service.template.json`

Creates the ECS service that runs the task definition behind an ALB.

## Deployment Flow

1. Create ECR repositories.
2. Build and push the `web` and `mock-server` images.
3. Create CloudWatch log groups referenced in the task definition.
4. Replace placeholders in `ecs-task-definition.template.json`.
5. Register the task definition.
6. Replace placeholders in `ecs-service.template.json`.
7. Create the ECS service.
8. Run a one-off migration task using the `web` image and `bash scripts/release_web.sh`.

## Example Commands

Register the task definition:

```bash
aws ecs register-task-definition \
  --cli-input-json file://ops/aws/ecs-task-definition.json
```

Create the service:

```bash
aws ecs create-service \
  --cli-input-json file://ops/aws/ecs-service.json
```

Run migrations as a one-off Fargate task:

```bash
aws ecs run-task \
  --cluster <cluster-name> \
  --launch-type FARGATE \
  --task-definition <task-definition-family>:<revision> \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-1,subnet-2],securityGroups=[sg-ecs-task],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"web","command":["bash","scripts/release_web.sh"]}]}'
```

## Notes

- Keep `MOCK_SERVER_URL` as `http://127.0.0.1:3000` because both containers run in the same task.
- Use node-based ElastiCache, not ElastiCache Serverless, because the app relies on Redis `WATCH/MULTI/EXEC`.
- The mock server is in-memory. Restarting the task clears mock bookings.
