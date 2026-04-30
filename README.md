# MMR Pipeline

게임 내전(커스텀 매치) 결과로부터 길드별 MMR을 자동 산출하는 **이벤트 드리븐 데이터 파이프라인**.

매치 종료 → 백엔드 INSERT → 1분 안에 MMR 계산 + 결과 HTML이 S3에 자동 생성된다.

---

## 도메인

- **서비스**: LoL 클랜 내부 내전 매치 관리 시스템
- **MMR 범위**: 길드(클랜)별 MMR. 같은 사람도 다른 길드에선 다른 MMR
- **유저 식별자**: Riot puuid

## 해결하는 문제

- 공식 클라이언트 솔로랭크에는 MMR이 있지만 **내전엔 객관적 실력 지표 없음**
- 내전 팀짜기 / 클랜 내부 대회 평가 / 시즌 누적 성과 추적이 어려움
- 백엔드 인라인 MMR 계산은 게임 서버 부하 전파 + 로직 변경 시 백엔드 재배포 필요

## 아키텍처

```
[유저 매치 종료]
       │
       ▼
[EC2 백엔드] 리플 파싱
   custom_match INSERT + match_outbox INSERT (단일 트랜잭션)
       │
       ▼
[RDS PostgreSQL]
       │
       ▼ (1분 주기)
[EventBridge Rule] ─trigger─▶ [Outbox Poller Lambda] (VPC 안)
                                  │ unpublished 행 폴링
                                  │ SQS 발행 + published=true 마킹
                                  ▼
                          [SQS Standard Queue]
                                  │
                          ┌───────┴───────┐
                          │ (3회 실패)     │ (성공)
                          ▼               ▼
                        [DLQ]    [MMR Calculator Lambda] (VPC 안)
                          │            │
                          │            ├─ 멱등성 체크 (processed_matches)
                          │            ├─ 현재 MMR 조회 (player_mmr)
                          │            ├─ ELO + KDA personal_factor 계산
                          │            ├─ player_mmr UPSERT
                          │            ├─ S3 raw 아카이브 (matches/)
                          │            └─ S3 결과 HTML PUT (results/)
                          ▼
                  [CloudWatch 알람]
                          ▼
                      [SNS] → 이메일
```

## 핵심 패턴

| 패턴 | 적용 |
|---|---|
| **Outbox 패턴** | 백엔드 트랜잭션 안에서 매치 + outbox 동시 INSERT → 데이터 유실 방지 |
| **멱등성** | `processed_matches` PK 충돌 → 같은 매치 두 번 와도 한 번만 처리 |
| **DLQ + 알람** | 3회 실패 메시지 자동 격리 + 이메일 알림 |
| **데이터 레이크** | S3에 raw 매치 JSON 영구 보존 → 재처리/분석 가능 |
| **VPC 격리** | RDS는 Private Subnet, Lambda는 VPC 안 ENI로 접근 |

---

## AWS 리소스 구성

### 리전
**eu-central-1 (프랑크푸르트)**

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
| `mmr-backend-sg` | SSH(본인 IP), HTTP/HTTPS, 3000, 19901 | EC2 백엔드 |
| `mmr-rds-sg` | 5432 (backend SG, lambda SG 참조) | RDS |
| `mmr-lambda-sg` | 없음 (outbound only) | Lambda VPC 연결용 |

### 컴퓨팅 / 데이터
| 리소스 | 사양 | 용도 |
|---|---|---|
| EC2 | t3.small, Amazon Linux 2023, 30GB gp3 | Frontend/Bot/Backend (Node.js + pm2) |
| Elastic IP | 52.59.124.66 | EC2 고정 공인 IP |
| RDS | db.t3.micro, PostgreSQL 16, 20GB gp3 | 매치 + outbox + MMR 저장 |
| Secrets Manager | RDS 비밀번호 자동 관리 | `manage_master_user_password` |

### 메시징 / 큐
| 리소스 | 이름 | 설정 |
|---|---|---|
| SQS Main Queue | `mmr-match-queue` | Standard, visibility 60s, retention 14일 |
| SQS DLQ | `mmr-match-dlq` | retention 14일, maxReceiveCount=3 |
| EventBridge Rule | `de-ai-01-mmr-outbox-poller-schedule` | rate(1 minute) |

### Lambda
| 함수 | 위치 | 트리거 | 역할 |
|---|---|---|---|
| `de-ai-01-mmr-outbox-poller` | VPC 안 | EventBridge (1분) | outbox 폴링 → SQS 발행 |
| `de-ai-01-mmr-mmr-calculator` | VPC 안 | SQS Event Source | MMR 계산 + S3 결과 생성 |

### 스토리지
| 리소스 | 이름 | 용도 |
|---|---|---|
| S3 Bucket | `de-ai-01-su1-mmr` | raw 매치 + MMR 결과 HTML |
| S3 prefix `matches/` | `year=YYYY/month=MM/day=DD/` | 원본 매치 JSON 아카이브 |
| S3 prefix `results/` | `year=YYYY/month=MM/day=DD/` | MMR 결과 HTML |

### 모니터링
| 리소스 | 용도 |
|---|---|
| CloudWatch Alarm `mmr-dlq-has-messages` | DLQ에 메시지 1건이라도 쌓이면 발동 |
| SNS Topic `mmr-alarms` | 알람 → 이메일 라우팅 |
| CloudWatch Logs | 각 Lambda별 7일 보존 |

### 신규 DB 테이블
| 테이블 | 역할 |
|---|---|
| `match_outbox` | 백엔드가 매치 INSERT 시 같이 INSERT, Poller가 폴링 |
| `processed_matches` | 멱등성 (이미 처리된 매치 추적) |
| `player_mmr` | 길드별 플레이어 누적 MMR (UNIQUE puuid+guild_id) |
| `mmr_history` | 매치별 MMR 변동 이력 (UNIQUE custom_match_id+puuid) |

---

## MMR 계산 알고리즘

원본 ML 파이프라인을 단순화한 **ELO 기반 절충안**.

```python
# 1. 같은 포지션 상대팀 플레이어와 비교
expected = 1 / (1 + 10 ** ((opp_mmr - my_mmr) / 400))   # ELO 표준
actual = 1 if win else 0

# 2. KDA 기반 개인 기여 보정 (0.5 ~ 2.0)
personal = clip((kill + assist) / max(death, 1) / 3, 0.5, 2.0)

# 3. K-factor (높은 MMR일수록 변동량 감소, 최소 0.35)
k = max(1.0 - max(0, mmr - 1500) * 0.002, 0.35)

# 4. delta 계산 + 클램핑
if win:
    delta = clip(int(20 * personal * k * (1 + actual - expected)), 12, 30)
else:
    delta = clip(int(-15 * personal * k * (1 + expected - actual)), -25, -12)
```

**상수**: `INITIAL_MMR=1300`, `BASE_WIN=20`, `BASE_LOSS=-15`, `MMR_K_DECAY_START=1500`

운영용 ML 파이프라인 (RandomForest 가중치 + QuantileTransformer 분포 정규화)은 별도 배치 시스템에서 처리하는 것이 적절. 실시간 처리에는 단순화 버전이 적합.

---

## IaC

전체 인프라 Terraform 관리.

```
terraform/
├── versions.tf                    # Terraform/Provider 버전
├── providers.tf                   # AWS Provider, default tags
├── variables.tf                   # 입력 변수
├── vpc.tf                         # VPC, 서브넷, IGW, NAT, 라우팅
├── security_groups.tf             # SG 3개
├── ec2.tf                         # EC2 + EIP + user_data
├── rds.tf                         # RDS + DB Subnet Group
├── sqs.tf                         # 메인 큐 + DLQ + redrive
├── s3.tf                          # 데이터 버킷 + 라이프사이클
├── monitoring.tf                  # SNS + CloudWatch 알람
├── iam.tf                         # Lambda 실행 역할
├── lambda_outbox_poller.tf        # Outbox Poller Lambda
├── lambda_mmr_calculator.tf       # MMR Calculator + SQS 트리거
├── eventbridge.tf                 # 1분 주기 스케줄
├── outputs.tf                     # 출력값
├── .gitignore
└── lambdas/
    ├── outbox_poller/
    │   ├── handler.py             # 폴링 로직
    │   ├── requirements.txt
    │   └── build.ps1              # Windows 패키징
    └── mmr_calculator/
        ├── handler.py             # 메인 오케스트레이션
        ├── mmr.py                 # ELO + K-factor + personal_factor
        ├── champions.py           # 챔피언 ID → 한글 이름 매핑
        ├── html_template.py       # Jinja2 결과 HTML
        ├── requirements.txt
        └── build.ps1
```

### 주요 명령

```powershell
# 인프라 생성
cd terraform
terraform init
terraform plan
terraform apply

# Lambda 패키징 (코드 변경 시)
cd lambdas/outbox_poller
.\build.ps1
cd ../mmr_calculator
.\build.ps1

# 코드 갱신 후 재배포
cd ../..
terraform apply

# 비용 절약 (RDS 데이터는 사라짐)
terraform destroy
```

---

## 의사결정 기록

| 결정 | 선택 | 근거 |
|---|---|---|
| 처리 방식 | 이벤트 드리븐 (Lambda) | 매치 종료 즉시 MMR 반영, 게임 서버 부하 분리 |
| 이벤트 캡처 | Outbox 패턴 | 트랜잭션 일관성, 백엔드 코드 변경 최소 (한 줄 INSERT) |
| 폴링 주기 | EventBridge Rule 1분 | EventBridge 표준 최소 주기, 학습 환경 충분 |
| MMR 결과 저장 | DB UPDATE + S3 HTML | DB는 다음 매칭 즉시 활용, S3는 시연·재처리·아카이브 |
| Multi-AZ | 비활성화 | 학습 비용 절감 |
| 비밀번호 관리 | RDS Managed (Secrets Manager) | tfstate 평문 노출 방지 |
| 계정 구성 | 단일 AWS 계정 | Cross-account 복잡도 회피 |
| Lambda 패키징 | zip + manylinux2014 | 컨테이너 이미지보다 단순, 콜드 스타트 빠름 |
| MMR 알고리즘 | ELO + KDA 단순화 | 매치 1건만으로 의미 있는 계산 가능 (원본 ML은 배치 전제) |
| 도메인 | nip.io 사용 | 도메인 비용 0, 쿠키 도메인 매칭 가능 |

---

## 비용 (대략, eu-central-1 기준)

| 리소스 | 시간당 | 24시간 |
|---|---|---|
| NAT Gateway | ~$0.052 | ~$1.25 |
| EC2 t3.small | ~$0.024 | ~$0.58 |
| RDS db.t3.micro | ~$0.025 | ~$0.60 |
| EBS (EC2 30GB + RDS 20GB) | ~$0.007 | ~$0.17 |
| Secrets Manager | - | ~$0.013 |
| Lambda × 2 | 호출당 과금 | ~$0 (저트래픽) |
| SQS / S3 / SNS | 요청·저장 기반 | ~$0 |
| CloudWatch Alarm | 알람당 $0.10/월 | - |
| **합계** | **~$0.108** | **~$2.6** |

작업 외 시간엔 `terraform destroy`로 비용 0 가능.

---

## 검증된 흐름 (End-to-End)

다음 시나리오를 끝까지 검증 완료:

1. DBeaver에서 `match_outbox` 더미 INSERT (매치 1건, 참가자 10명)
2. 1분 안에 EventBridge가 Outbox Poller 호출
3. Outbox Poller가 SQS에 발행 + `published=true` 마킹
4. SQS Event Source Mapping으로 MMR Calculator 즉시 호출
5. MMR Calculator가 ELO 기반 MMR 계산
6. `player_mmr` UPSERT (누적), `mmr_history` INSERT (매치별 변동), `processed_matches` INSERT (멱등성)
7. S3 `matches/`에 raw JSON, `results/`에 HTML 떨어짐
8. HTML 다운로드해서 브라우저에서 확인 → 매치 결과 + MMR 변동 시각화

---

## 한계 및 향후 개선

학습 목적상 단순화한 부분:

- **MMR 알고리즘**: 운영 코드는 RandomForest + QuantileTransformer 기반 ML. 미니프로젝트는 단순 ELO + KDA로 대체. 운영용은 배치(Airflow)로 모델 학습, 추론만 Lambda
- **EC2 Public Subnet 배치**: 운영은 Private Subnet + ALB + Bastion이 best practice
- **IAM 권한**: 학습용은 광범위. 운영은 최소 권한 원칙 (서비스별 IAM 정책 분리)
- **SQS Standard 사용**: FIFO + MessageGroupId로 더 강한 순서 보장 가능
- **자동 재처리 미구현**: DLQ 알람만, 운영은 DLQ → 메인 큐 자동 redrive Lambda 추가
- **단일 AZ NAT Gateway**: 운영은 AZ별 NAT로 가용성 확보
- **EventBridge Rule 1분**: 운영은 EventBridge Scheduler로 10초 이하 가능
- **도메인 nip.io**: 운영은 Route 53 + ACM 인증서 + ALB로 HTTPS 도메인
- **챔피언 매핑 하드코딩**: 운영은 DB의 `champion` 테이블 또는 캐시에서 조회

향후 확장 가능:

- OpenSearch 연동으로 실시간 로그 대시보드 (별도 작업)
- Athena로 S3 매치 데이터 직접 분석
- Airflow로 시즌 정산 / 리포트 / MMR 알고리즘 백필 배치
- Discord 봇 통합으로 매치 직후 결과 알림
