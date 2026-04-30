# ─────────────────────────────────────────────
# SNS 토픽 (이메일 알림용)
# ─────────────────────────────────────────────
resource "aws_sns_topic" "alarms" {
  name = "${var.project_name}-alarms"

  tags = {
    Name = "${var.project_name}-alarms"
  }
}

# ─────────────────────────────────────────────
# SNS 이메일 구독
# ─────────────────────────────────────────────
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ─────────────────────────────────────────────
# CloudWatch 알람: DLQ에 메시지 1건이라도 쌓이면 알림
# ─────────────────────────────────────────────
resource "aws_cloudwatch_metric_alarm" "dlq_messages" {
  alarm_name          = "${var.project_name}-dlq-has-messages"
  alarm_description   = "DLQ에 처리 실패 메시지가 쌓였습니다"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.match_dlq.name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]

  tags = {
    Name = "${var.project_name}-dlq-alarm"
  }
}