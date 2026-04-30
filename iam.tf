# ─────────────────────────────────────────────
# Outbox Poller Lambda 실행 역할
# ─────────────────────────────────────────────
resource "aws_iam_role" "outbox_poller" {
  name = "${var.name_prefix}-${var.project_name}-outbox-poller-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-outbox-poller-role"
  }
}

# CloudWatch Logs 쓰기 권한
resource "aws_iam_role_policy_attachment" "outbox_poller_logs" {
  role       = aws_iam_role.outbox_poller.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# VPC 안에 들어가는 Lambda는 ENI 관리 권한 필요
resource "aws_iam_role_policy_attachment" "outbox_poller_vpc" {
  role       = aws_iam_role.outbox_poller.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# 인라인 정책: Secrets Manager 읽기 + SQS 발행
resource "aws_iam_role_policy" "outbox_poller_app" {
  name = "${var.name_prefix}-${var.project_name}-outbox-poller-app"
  role = aws_iam_role.outbox_poller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_db_instance.main.master_user_secret[0].secret_arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = aws_sqs_queue.match_queue.arn
      }
    ]
  })
}