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

# ─────────────────────────────────────────────
# MMR Calculator Lambda 실행 역할
# ─────────────────────────────────────────────
resource "aws_iam_role" "mmr_calculator" {
  name = "${var.name_prefix}-${var.project_name}-mmr-calculator-role"

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
    Name = "${var.name_prefix}-${var.project_name}-mmr-calculator-role"
  }
}

# CloudWatch Logs 권한
resource "aws_iam_role_policy_attachment" "mmr_calculator_logs" {
  role       = aws_iam_role.mmr_calculator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# VPC ENI 관리 권한
resource "aws_iam_role_policy_attachment" "mmr_calculator_vpc" {
  role       = aws_iam_role.mmr_calculator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# 인라인 정책: Secrets + SQS 수신 + S3 PUT
resource "aws_iam_role_policy" "mmr_calculator_app" {
  name = "${var.name_prefix}-${var.project_name}-mmr-calculator-app"
  role = aws_iam_role.mmr_calculator.id

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
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = aws_sqs_queue.match_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.match_dlq.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.data.arn}/*"
      }
    ]
  })
}