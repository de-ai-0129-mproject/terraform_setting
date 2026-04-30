# ─────────────────────────────────────────────
# DLQ (먼저 만들어야 함, 메인 큐가 참조)
# ─────────────────────────────────────────────
resource "aws_sqs_queue" "match_dlq" {
  name                      = "${var.project_name}-match-dlq"
  message_retention_seconds = 1209600  # 14일

  tags = {
    Name = "${var.project_name}-match-dlq"
  }
}

# ─────────────────────────────────────────────
# 메인 큐
# ─────────────────────────────────────────────
resource "aws_sqs_queue" "match_queue" {
  name                       = "${var.project_name}-match-queue"
  message_retention_seconds  = 1209600  # 14일
  visibility_timeout_seconds = 60       # Lambda 처리 시간

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.match_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Name = "${var.project_name}-match-queue"
  }
}

# ─────────────────────────────────────────────
# DLQ → 메인 큐로 redrive 허용 (재처리용)
# ─────────────────────────────────────────────
resource "aws_sqs_queue_redrive_allow_policy" "dlq_redrive" {
  queue_url = aws_sqs_queue.match_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.match_queue.arn]
  })
}