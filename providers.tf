provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "mmr-pipeline"
      ManagedBy   = "terraform"
      Environment = "dev"
    }
  }
}