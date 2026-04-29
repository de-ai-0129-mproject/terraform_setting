## AWS 리소스 구성

### 리전
- **eu-central-1 (프랑크푸르트)**

### 네트워크
| 리소스 | 이름 | 비고 |
|---|---|---|
| VPC | `mmr-vpc` | CIDR `10.0.0.0/16` |
| Public Subnet × 2 | `mmr-public-eu-central-1a/b` | `10.0.1.0/24`, `10.0.2.0/24` |
| Private Subnet × 2 | `mmr-private-eu-central-1a/b` | `10.0.11.0/24`, `10.0.12.0/24` |
| Internet Gateway | `mmr-igw` | VPC 외부 통신 |
| NAT Gateway | `mmr-nat` | Public Subnet 1에 배치 |

### 보안 그룹
| 이름 | 인바운드 | 용도 |
|---|---|---|
| `mmr-backend-sg` | SSH(본인 IP), HTTP/HTTPS, 3000, 8080 | EC2 백엔드 |
| `mmr-rds-sg` | 5432 (backend SG, lambda SG 참조) | RDS |
| `mmr-lambda-sg` | 없음 (outbound only) | Lambda VPC 연결용 |

### 컴퓨팅 / 데이터
| 리소스 | 사양 | 용도 |
|---|---|---|
| EC2 | t3.small, Amazon Linux 2023, 20GB gp3 | Frontend/Bot/Backend (Node.js + pm2) |
| RDS | db.t3.micro, PostgreSQL 16, 20GB gp3 | matches, match_outbox |
| Secrets Manager | RDS 비밀번호 자동 관리 | `manage_master_user_password` |

### 메시징 / 스토리지 (예정)
- SQS Standard Queue + DLQ
- S3 Bucket (matches/, results/)
- Lambda × 2 (Outbox Poller, MMR Calculator)
- EventBridge Rule (10초 주기)

## 의사결정 기록

| 결정 | 선택 | 근거 |
|---|---|---|
| 처리 방식 | 이벤트 드리븐 (Lambda) | 매치 종료 즉시 MMR 반영 필요 |
| 이벤트 캡처 | Outbox 패턴 | 트랜잭션 일관성, 백엔드 코드 변경 최소 |
| MMR 결과 저장 | S3 (HTML/Excel) | 학습 단계 단순화. 운영에선 DB UPDATE |
| Multi-AZ | 비활성화 | 학습 비용 절감 |
| 비밀번호 관리 | RDS Managed (Secrets Manager) | tfstate 평문 노출 방지 |
| 계정 구성 | 단일 AWS 계정 | Cross-account 복잡도 회피 |

## 한계 및 개선 여지

학습 목적상 단순화한 부분:

- **MMR 결과를 파일로**: 실운영은 DB UPDATE 또는 ElastiCache로 매치메이킹 서비스가 즉시 조회
- **EC2 Public Subnet 배치**: 운영은 Private Subnet + ALB + Bastion이 best practice
- **IAM AdministratorAccess**: 운영은 최소 권한 원칙 적용 (서비스별 IAM 정책 분리)
- **SQS Standard 사용**: FIFO + MessageGroupId로 더 강한 순서 보장 가능
- **자동 재처리 미구현**: DLQ 알람만, 운영은 DLQ 재처리 Lambda 추가

## IaC

전체 인프라는 Terraform으로 관리됩니다.

```
terraform/
├── versions.tf            # Terraform/Provider 버전 고정
├── providers.tf           # AWS Provider, default tags
├── variables.tf           # 입력 변수
├── vpc.tf                 # VPC, 서브넷, IGW, NAT, 라우팅
├── security_groups.tf     # SG 3개
├── ec2.tf                 # EC2 + EIP + user_data
├── rds.tf                 # RDS + DB Subnet Group
├── outputs.tf             # 출력값
└── .gitignore
```

### 주요 명령

```powershell
cd terraform
terraform init        # 초기화 (provider 다운로드)
terraform plan        # 변경 사항 미리보기
terraform apply       # 실제 생성
terraform destroy     # 비용 절약을 위한 정리
```

## 비용 (대략, eu-central-1 기준)

| 리소스 | 시간당 | 24시간 |
|---|---|---|
| NAT Gateway | ~$0.052 | ~$1.25 |
| EC2 t3.small | ~$0.024 | ~$0.58 |
| RDS db.t3.micro | ~$0.025 | ~$0.60 |
| EBS 20GB × 2 | ~$0.006 | ~$0.14 |
| Secrets Manager | - | ~$0.013 |
| **합계** | **~$0.107** | **~$2.6** |

작업 외 시간엔 `terraform destroy`로 비용 0 가능.

