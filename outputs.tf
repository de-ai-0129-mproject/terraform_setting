output "backend_sg_id" {
  description = "백엔드 EC2 보안 그룹 ID"
  value       = aws_security_group.backend.id
}

output "rds_sg_id" {
  description = "RDS 보안 그룹 ID"
  value       = aws_security_group.rds.id
}

output "lambda_sg_id" {
  description = "Lambda 보안 그룹 ID"
  value       = aws_security_group.lambda.id
}

output "my_ip" {
  description = "현재 SSH가 허용된 본인 IP"
  value       = local.my_ip_cidr
}

output "backend_public_ip" {
  description = "백엔드 EC2 공인 IP (Elastic IP)"
  value       = aws_eip.backend.public_ip
}

output "backend_private_ip" {
  description = "백엔드 EC2 내부 IP"
  value       = aws_instance.backend.private_ip
}

output "ssh_command" {
  description = "SSH 접속 명령어"
  value       = "ssh -i ~/.ssh/mmr-keypair.pem ec2-user@${aws_eip.backend.public_ip}"
}

output "rds_endpoint" {
  description = "RDS 엔드포인트 (host:port 형식)"
  value       = aws_db_instance.main.endpoint
}

output "rds_address" {
  description = "RDS 호스트명만"
  value       = aws_db_instance.main.address
}

output "rds_port" {
  description = "RDS 포트"
  value       = aws_db_instance.main.port
}

output "rds_db_name" {
  description = "초기 DB 이름"
  value       = aws_db_instance.main.db_name
}

output "rds_username" {
  description = "RDS 마스터 사용자명"
  value       = aws_db_instance.main.username
}

output "rds_secret_arn" {
  description = "RDS 비밀번호 Secrets Manager ARN (백엔드/Lambda가 이걸로 비번 가져감)"
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}