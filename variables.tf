variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "프로젝트 이름 (리소스 이름 prefix)"
  type        = string
  default     = "mmr"
}

variable "vpc_cidr" {
  description = "VPC CIDR 블록"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "사용할 가용 영역"
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]
}

variable "public_subnet_cidrs" {
  description = "Public 서브넷 CIDR 블록"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "Private 서브넷 CIDR 블록"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

variable "key_pair_name" {
  description = "EC2 SSH 키페어 이름 (콘솔에서 미리 생성)"
  type        = string
  default     = "de-ai-01-app-keypair"
}

variable "ec2_instance_type" {
  description = "EC2 인스턴스 타입"
  type        = string
  default     = "t3.small"
}

variable "db_instance_class" {
  description = "RDS 인스턴스 클래스"
  type        = string
  default     = "db.t3.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL 엔진 버전"
  type        = string
  default     = "16"
}

variable "db_name" {
  description = "초기 DB 이름"
  type        = string
  default     = "mmrdb"
}

variable "db_username" {
  description = "RDS 마스터 사용자명"
  type        = string
  default     = "mmradmin"
}

variable "db_allocated_storage" {
  description = "RDS 스토리지 (GB)"
  type        = number
  default     = 20
}

