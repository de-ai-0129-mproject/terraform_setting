# ─────────────────────────────────────────────
# EventBridge Rule: 1분마다 Outbox Poller 호출
# ─────────────────────────────────────────────
resource "aws_cloudwatch_event_rule" "outbox_poller_schedule" {
  name                = "${var.name_prefix}-${var.project_name}-outbox-poller-schedule"
  description         = "Trigger Outbox Poller Lambda every 1 minute"
  schedule_expression = "rate(1 minute)"

  tags = {
    Name = "${var.name_prefix}-${var.project_name}-outbox-poller-schedule"
  }
}

# ─────────────────────────────────────────────
# Rule이 호출할 대상 = Outbox Poller Lambda
# ─────────────────────────────────────────────
resource "aws_cloudwatch_event_target" "outbox_poller_target" {
  rule      = aws_cloudwatch_event_rule.outbox_poller_schedule.name
  target_id = "outbox-poller"
  arn       = aws_lambda_function.outbox_poller.arn
}

# ─────────────────────────────────────────────
# Lambda가 EventBridge로부터 호출되도록 권한 부여
# ─────────────────────────────────────────────
resource "aws_lambda_permission" "allow_eventbridge_outbox" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.outbox_poller.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.outbox_poller_schedule.arn
}