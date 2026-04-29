# ─────────────────────────────────────────────
# DB Subnet Group
# RDS는 최소 2개의 AZ에 걸친 서브넷이 필요함 (Multi-AZ 비활성화 상태에서도)
# ─────────────────────────────────────────────
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-db-subnet-group"
  }
}

# ─────────────────────────────────────────────
# RDS PostgreSQL 인스턴스
# ─────────────────────────────────────────────
resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-postgres"

  # 엔진
  engine         = "postgres"
  engine_version = var.db_engine_version
  instance_class = var.db_instance_class

  # 스토리지
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = 0  # 자동 확장 끔
  storage_type          = "gp3"
  storage_encrypted     = true

  # DB 설정
  db_name  = var.db_name
  username = var.db_username

  # 비밀번호: RDS Managed (Secrets Manager 자동 연동)
  manage_master_user_password = true

  # 네트워크
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false

  # 가용성
  multi_az = false

  # 백업
  backup_retention_period = 1
  backup_window           = "03:00-04:00"  # UTC
  maintenance_window      = "sun:04:00-sun:05:00"  # UTC

  # 학습 환경 설정
  skip_final_snapshot       = true
  deletion_protection       = false
  delete_automated_backups  = true

  # 로깅 (선택)
  enabled_cloudwatch_logs_exports = ["postgresql"]

  # 자동 마이너 버전 업그레이드
  auto_minor_version_upgrade = true

  tags = {
    Name = "${var.project_name}-postgres"
  }
}