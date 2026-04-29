# ─────────────────────────────────────────────
# Amazon Linux 2023 최신 AMI 자동 조회
# ─────────────────────────────────────────────
data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ─────────────────────────────────────────────
# Elastic IP (공인 IP 고정용)
# ─────────────────────────────────────────────
resource "aws_eip" "backend" {
  domain   = "vpc"
  instance = aws_instance.backend.id

  tags = {
    Name = "${var.project_name}-backend-eip"
  }

  depends_on = [aws_internet_gateway.main]
}

# ─────────────────────────────────────────────
# EC2 백엔드 인스턴스
# ─────────────────────────────────────────────
resource "aws_instance" "backend" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.ec2_instance_type
  key_name                    = var.key_pair_name
  subnet_id                   = aws_subnet.public[0].id
  vpc_security_group_ids      = [aws_security_group.backend.id]
  associate_public_ip_address = true

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = <<-EOF
    #!/bin/bash
    set -e

    # 시스템 업데이트
    dnf update -y

    # 필수 도구 설치
    dnf install -y git htop

    # Node.js 20 LTS 설치 (NodeSource)
    curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
    dnf install -y nodejs

    # pm2 전역 설치 (ec2-user 권한으로)
    npm install -g pm2

    # pm2 부팅 시 자동 시작 설정
    sudo -u ec2-user bash -c 'pm2 startup systemd -u ec2-user --hp /home/ec2-user' | tail -1 | sudo bash

    # 작업 디렉터리
    sudo -u ec2-user mkdir -p /home/ec2-user/apps

    # 환경 표시
    echo "Node.js: $(node -v)" > /home/ec2-user/setup_done.txt
    echo "npm: $(npm -v)" >> /home/ec2-user/setup_done.txt
    echo "pm2: $(sudo -u ec2-user pm2 -v)" >> /home/ec2-user/setup_done.txt
  EOF

  user_data_replace_on_change = false

  tags = {
    Name = "${var.project_name}-backend-ec2"
  }
}