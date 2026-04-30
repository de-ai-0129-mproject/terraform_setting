# ─────────────────────────────────────────────
# S3 버킷 (raw 매치 + MMR 결과)
# ─────────────────────────────────────────────
resource "aws_s3_bucket" "data" {
  bucket = "de-ai-01-su1-mmr"

  tags = {
    Name = "${var.project_name}-data"
  }
}

# ─────────────────────────────────────────────
# 퍼블릭 액세스 차단 (보안)
# ─────────────────────────────────────────────
resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ─────────────────────────────────────────────
# 서버 사이드 암호화 (AWS 관리 키)
# ─────────────────────────────────────────────
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ─────────────────────────────────────────────
# 라이프사이클: 30일 후 자동 삭제
# ─────────────────────────────────────────────
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    id     = "delete-after-30-days"
    status = "Enabled"

    filter {}  # 모든 객체 대상

    expiration {
      days = 30
    }

    # 미완료 멀티파트 업로드도 7일 후 정리
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}