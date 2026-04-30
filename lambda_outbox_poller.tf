# ─────────────────────────────────────────────
# Outbox Poller Lambda
# ─────────────────────────────────────────────
resource "aws_lambda_function" "outbox_poller" {
  function_name = "${var.name_prefix}-${var.project_name}-outbox-poller"
  role          = aws_iam_role.outbox_poller.arn

  filename         = "${path.module}/lambdas/outbox_poller/outbox_poller.zip"
  source_code_hash = filebase64sha256("${path.module}/lambdas/outbox_poller/outbox_poller.zip")

  handler = "handler.lambda_handler"
  runtime = "python3.12"

  timeout     = 60
  memory_size = 256

  # VPC 안에 배치 (RDS 접근 위해)
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      SECRET_ARN    = aws_db_instance.main.master_user_secret[0].secret_arn
      RDS_HOST      = aws_db_instance.main.address
      RDS_DB        = aws_db_instance.main.db_name
      SQS_URL       = aws_sqs_queue.match_queue.url
      DEFAULT_LIMIT = "100"
    }
  }

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-outbox-poller"
  }

  depends_on = [
    aws_iam_role_policy_attachment.outbox_poller_logs,
    aws_iam_role_policy_attachment.outbox_poller_vpc,
    aws_iam_role_policy.outbox_poller_app,
  ]
}

# ─────────────────────────────────────────────
# CloudWatch Log Group (로그 보존 기간 명시)
# ─────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "outbox_poller" {
  name              = "/aws/lambda/${aws_lambda_function.outbox_poller.function_name}"
  retention_in_days = 7

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-outbox-poller-logs"
  }
}