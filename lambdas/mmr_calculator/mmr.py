"""
MMR 계산 모듈

ELO 기반 + KDA personal_factor + K-factor 절충안.
원본 ML 파이프라인을 단순화한 버전.
"""

# ─────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────
INITIAL_MMR = 1300
BASE_WIN = 20
BASE_LOSS = -15
MMR_MIN_CHANGE = -25
MMR_MAX_CHANGE = 30
MMR_K_DECAY_START = 1500
MMR_K_DECAY_RATE = 0.002
MMR_K_MIN = 0.35


def expected_score(my_mmr: int, opp_mmr: int) -> float:
  """ELO 표준 기대 승률."""
  return 1 / (1 + 10 ** ((opp_mmr - my_mmr) / 400))


def k_factor(mmr: int) -> float:
  """MMR이 높을수록 변동량 감소, 최소 K_MIN."""
  if mmr <= MMR_K_DECAY_START:
    return 1.0
  k = 1.0 - (mmr - MMR_K_DECAY_START) * MMR_K_DECAY_RATE
  return max(k, MMR_K_MIN)


def personal_factor(kill: int, death: int, assist: int) -> float:
  """KDA 기반 개인 기여 보정 (0.5 ~ 2.0)."""
  kda = (kill + assist) / max(death, 1)
  factor = kda / 3.0
  return max(0.5, min(2.0, factor))


def find_opponent(me: dict, participants: list[dict]) -> dict | None:
  """같은 포지션의 반대 팀 참가자."""
  for p in participants:
    if p['puuid'] == me['puuid']:
      continue
    if p['position'] == me['position'] and p['game_team'] != me['game_team']:
      return p
  return None


def calculate_mmr_change(
  me: dict,
  participants: list[dict],
  current_mmrs: dict[str, int],
) -> dict:
  """
  한 참가자의 MMR 변동 계산.

  Returns:
      {
          "puuid": str,
          "pre_mmr": int,
          "post_mmr": int,
          "delta": int,
          "expected": float,
          "actual": int,
          "personal_factor": float,
          "k": float,
      }
  """
  my_mmr = current_mmrs.get(me['puuid'], INITIAL_MMR)

  opponent = find_opponent(me, participants)
  if opponent is None:
    opp_mmr = INITIAL_MMR
  else:
    opp_mmr = current_mmrs.get(opponent['puuid'], INITIAL_MMR)

  is_win = me['game_result'] == '승'
  actual = 1 if is_win else 0
  expected = expected_score(my_mmr, opp_mmr)

  pf = personal_factor(me['kill'], me['death'], me['assist'])
  k = k_factor(my_mmr)

  if is_win:
    # 기대 이상 승리 → 더 큼, 기대 이하 승리 → 더 작음
    delta = int(BASE_WIN * pf * k * (1 + (actual - expected)))
    delta = max(12, min(MMR_MAX_CHANGE, delta))
  else:
    # 기대 이상 패배 → 덜 잃음, 기대 이하 패배 → 더 잃음
    delta = int(BASE_LOSS * pf * k * (1 + (expected - actual)))
    delta = max(MMR_MIN_CHANGE, min(-12, delta))

  return {
    'puuid': me['puuid'],
    'pre_mmr': my_mmr,
    'post_mmr': my_mmr + delta,
    'delta': delta,
    'expected': round(expected, 4),
    'actual': actual,
    'personal_factor': round(pf, 4),
    'k': round(k, 4),
  }


def calculate_match_mmr(
  participants: list[dict],
  current_mmrs: dict[str, int],
) -> list[dict]:
  """매치 전체의 모든 참가자 MMR 변동 계산."""
  return [calculate_mmr_change(p, participants, current_mmrs) for p in participants]
