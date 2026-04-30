# ─────────────────────────────────────────────
# MMR Calculator Lambda
# ─────────────────────────────────────────────
resource "aws_lambda_function" "mmr_calculator" {
  function_name = "${var.name_prefix}-${var.project_name}-mmr-calculator"
  role          = aws_iam_role.mmr_calculator.arn

  filename         = "${path.module}/lambdas/mmr_calculator/mmr_calculator.zip"
  source_code_hash = filebase64sha256("${path.module}/lambdas/mmr_calculator/mmr_calculator.zip")

  handler = "handler.lambda_handler"
  runtime = "python3.12"

  timeout     = 60
  memory_size = 512  # Jinja2 + DB 연결 + S3 업로드 여유롭게

  # VPC 안 (RDS 접근 필요)
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      SECRET_ARN = aws_db_instance.main.master_user_secret[0].secret_arn
      RDS_HOST   = aws_db_instance.main.address
      RDS_DB     = aws_db_instance.main.db_name
      S3_BUCKET  = aws_s3_bucket.data.id
    }
  }

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-mmr-calculator"
  }

  depends_on = [
    aws_iam_role_policy_attachment.mmr_calculator_logs,
    aws_iam_role_policy_attachment.mmr_calculator_vpc,
    aws_iam_role_policy.mmr_calculator_app,
  ]
}

# ─────────────────────────────────────────────
# CloudWatch Log Group
# ─────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "mmr_calculator" {
  name              = "/aws/lambda/${aws_lambda_function.mmr_calculator.function_name}"
  retention_in_days = 7

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-mmr-calculator-logs"
  }
}

# ─────────────────────────────────────────────
# SQS → Lambda 연동 (Event Source Mapping)
# 메인 큐의 메시지를 Lambda가 자동으로 받아 처리
# ─────────────────────────────────────────────
resource "aws_lambda_event_source_mapping" "sqs_to_mmr_calculator" {
  event_source_arn = aws_sqs_queue.match_queue.arn
  function_name    = aws_lambda_function.mmr_calculator.arn

  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  enabled                            = true

  # Partial batch failure 응답 형식 (Lambda 코드의 batchItemFailures와 매칭)
  function_response_types = ["ReportBatchItemFailures"]
}