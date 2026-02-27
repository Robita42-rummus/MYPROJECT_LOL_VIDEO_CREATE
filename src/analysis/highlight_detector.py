"""
マッチタイムラインからハイライトシーンを検出する

Riot Match Timeline API が返すイベントを解析して、
録画する価値のある瞬間（時刻）を特定する。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger


class HighlightType(Enum):
    FIRST_BLOOD    = "ファーストブラッド"
    DOUBLE_KILL    = "ダブルキル"
    TRIPLE_KILL    = "トリプルキル"
    QUADRA_KILL    = "クアドラキル"
    PENTA_KILL     = "ペンタキル"
    BARON_KILL     = "バロン撃破"
    DRAGON_KILL    = "ドラゴン撃破"
    RIFT_HERALD    = "リフトヘラルド"
    INHIBITOR      = "インヒビター破壊"
    TEAMFIGHT      = "チームファイト"
    TURRET_KILL    = "タワー破壊"


@dataclass
class Highlight:
    """1つのハイライトシーン"""
    type: HighlightType
    game_time: float           # ゲーム内時刻 (秒)
    priority: int              # 優先度 (高いほど重要)
    participant_id: int = 0    # 主役プレイヤー
    champion_name: str = ""    # チャンピオン名
    extra: dict = field(default_factory=dict)  # 追加情報

    @property
    def label(self) -> str:
        return self.type.value

    def __repr__(self):
        mins = int(self.game_time // 60)
        secs = int(self.game_time % 60)
        return f"[{mins:02d}:{secs:02d}] {self.label} ({self.champion_name})"


# ハイライトタイプ別の優先度
PRIORITY_MAP = {
    HighlightType.PENTA_KILL:    100,
    HighlightType.QUADRA_KILL:   80,
    HighlightType.BARON_KILL:    70,
    HighlightType.TRIPLE_KILL:   60,
    HighlightType.TEAMFIGHT:     55,
    HighlightType.INHIBITOR:     50,
    HighlightType.DRAGON_KILL:   45,
    HighlightType.RIFT_HERALD:   40,
    HighlightType.DOUBLE_KILL:   30,
    HighlightType.FIRST_BLOOD:   35,
    HighlightType.TURRET_KILL:   20,
}


class HighlightDetector:
    """
    マッチタイムラインデータからハイライトを検出する

    タイムラインAPIのイベント例:
    {
      "timestamp": 600000,  # ミリ秒
      "type": "CHAMPION_KILL",
      "killerId": 3,
      "victimId": 7,
      "assistingParticipantIds": [1, 5],
      "multiKillLength": 3,  # トリプルキルなど
      "killType": "KILL_FIRST_BLOOD",
    }
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.include_triple   = self.config.get("include_triplekills", True)
        self.include_quadra   = self.config.get("include_quadrakills", True)
        self.include_penta    = self.config.get("include_pentakills", True)
        self.include_baron    = self.config.get("include_baron", True)
        self.include_dragon   = self.config.get("include_dragon", True)
        self.include_inhibitor = self.config.get("include_inhibitor", True)
        self.include_first_blood = self.config.get("include_first_blood", True)
        self.teamfight_kill_count = self.config.get("teamfight_kill_count", 3)
        self.teamfight_time_window = self.config.get("teamfight_time_window", 15)

    def detect(self, timeline: dict, participants: list[dict] = None) -> list[Highlight]:
        """
        タイムラインデータからハイライト一覧を取得

        Args:
            timeline: Riot API の match timeline レスポンス
            participants: 参加者情報 (championName のマッピング用)
        Returns:
            Highlight のリスト (優先度降順)
        """
        highlights: list[Highlight] = []

        # 参加者ID → チャンピオン名マップ
        champion_map = self._build_champion_map(participants or [])

        frames = timeline.get("info", {}).get("frames", [])
        all_events = []
        for frame in frames:
            for event in frame.get("events", []):
                all_events.append(event)

        # マルチキル / ファーストブラッド
        for event in all_events:
            h = self._parse_champion_kill(event, champion_map)
            if h:
                highlights.append(h)

        # オブジェクト (バロン、ドラゴン、etc)
        for event in all_events:
            h = self._parse_elite_monster_kill(event, champion_map)
            if h:
                highlights.append(h)

        # インヒビター
        for event in all_events:
            h = self._parse_building_kill(event, champion_map)
            if h:
                highlights.append(h)

        # チームファイト検出
        teamfight_highlights = self._detect_teamfights(all_events, champion_map)
        highlights.extend(teamfight_highlights)

        # 重複除去 (同一時刻付近の重複を削除)
        highlights = self._deduplicate(highlights)

        # 優先度順ソート
        highlights.sort(key=lambda h: h.priority, reverse=True)

        logger.info(f"ハイライト検出: {len(highlights)} 件")
        for h in highlights:
            logger.info(f"  {h}")

        return highlights

    def _ts_to_seconds(self, timestamp_ms: int) -> float:
        """ミリ秒 → 秒"""
        return timestamp_ms / 1000.0

    def _build_champion_map(self, participants: list) -> dict:
        """participantId → championName のマップ"""
        result = {}
        for p in participants:
            pid = p.get("participantId", 0)
            champ = p.get("championName", "")
            result[pid] = champ
        return result

    def _parse_champion_kill(self, event: dict, champion_map: dict) -> Optional[Highlight]:
        """キルイベントを解析"""
        if event.get("type") != "CHAMPION_KILL":
            return None

        game_time = self._ts_to_seconds(event.get("timestamp", 0))
        killer_id = event.get("killerId", 0)
        multi_kill = event.get("multiKillLength", 1)
        kill_type  = event.get("killType", "")
        champion_name = champion_map.get(killer_id, f"P{killer_id}")

        # ファーストブラッド
        if kill_type == "KILL_FIRST_BLOOD" and self.include_first_blood:
            return Highlight(
                type=HighlightType.FIRST_BLOOD,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.FIRST_BLOOD],
                participant_id=killer_id,
                champion_name=champion_name,
            )

        # マルチキル
        if multi_kill >= 5 and self.include_penta:
            return Highlight(
                type=HighlightType.PENTA_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.PENTA_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
                extra={"multi_kill": multi_kill},
            )
        if multi_kill == 4 and self.include_quadra:
            return Highlight(
                type=HighlightType.QUADRA_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.QUADRA_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
            )
        if multi_kill == 3 and self.include_triple:
            return Highlight(
                type=HighlightType.TRIPLE_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.TRIPLE_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
            )
        if multi_kill == 2:
            return Highlight(
                type=HighlightType.DOUBLE_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.DOUBLE_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
            )

        return None

    def _parse_elite_monster_kill(self, event: dict, champion_map: dict) -> Optional[Highlight]:
        """エリートモンスター撃破イベント"""
        if event.get("type") != "ELITE_MONSTER_KILL":
            return None

        game_time = self._ts_to_seconds(event.get("timestamp", 0))
        killer_id = event.get("killerId", 0)
        monster_type = event.get("monsterType", "")
        champion_name = champion_map.get(killer_id, f"P{killer_id}")

        if monster_type == "BARON_NASHOR" and self.include_baron:
            return Highlight(
                type=HighlightType.BARON_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.BARON_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
            )
        if monster_type in ("DRAGON", "HEXTECH_DRAGON", "CHEMTECH_DRAGON") and self.include_dragon:
            sub_type = event.get("monsterSubType", "FIRE_DRAGON")
            return Highlight(
                type=HighlightType.DRAGON_KILL,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.DRAGON_KILL],
                participant_id=killer_id,
                champion_name=champion_name,
                extra={"dragon_type": sub_type},
            )
        if monster_type == "RIFTHERALD":
            return Highlight(
                type=HighlightType.RIFT_HERALD,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.RIFT_HERALD],
                participant_id=killer_id,
                champion_name=champion_name,
            )
        return None

    def _parse_building_kill(self, event: dict, champion_map: dict) -> Optional[Highlight]:
        """建物破壊イベント"""
        if event.get("type") != "BUILDING_KILL":
            return None

        game_time = self._ts_to_seconds(event.get("timestamp", 0))
        killer_id = event.get("killerId", 0)
        building_type = event.get("buildingType", "")
        champion_name = champion_map.get(killer_id, f"P{killer_id}")

        if building_type == "INHIBITOR_BUILDING" and self.include_inhibitor:
            return Highlight(
                type=HighlightType.INHIBITOR,
                game_time=game_time,
                priority=PRIORITY_MAP[HighlightType.INHIBITOR],
                participant_id=killer_id,
                champion_name=champion_name,
            )
        if building_type == "TOWER_BUILDING":
            tower_type = event.get("towerType", "")
            if tower_type == "NEXUS_TURRET":  # ネクサスタワーは高優先
                return Highlight(
                    type=HighlightType.TURRET_KILL,
                    game_time=game_time,
                    priority=PRIORITY_MAP[HighlightType.TURRET_KILL] + 10,
                    champion_name=champion_name,
                )
        return None

    def _detect_teamfights(self, events: list, champion_map: dict) -> list[Highlight]:
        """
        短時間に複数のキルが発生するチームファイトを検出
        """
        kill_events = [
            e for e in events
            if e.get("type") == "CHAMPION_KILL"
        ]

        teamfights = []
        used_indices = set()

        for i, e in enumerate(kill_events):
            if i in used_indices:
                continue

            t = self._ts_to_seconds(e.get("timestamp", 0))
            window_kills = [i]

            for j, e2 in enumerate(kill_events):
                if j <= i or j in used_indices:
                    continue
                t2 = self._ts_to_seconds(e2.get("timestamp", 0))
                if abs(t2 - t) <= self.teamfight_time_window:
                    window_kills.append(j)

            if len(window_kills) >= self.teamfight_kill_count:
                # チームファイト検出: 中心時刻を採用
                times = [self._ts_to_seconds(kill_events[k].get("timestamp", 0)) for k in window_kills]
                center_time = sum(times) / len(times)

                teamfights.append(Highlight(
                    type=HighlightType.TEAMFIGHT,
                    game_time=center_time,
                    priority=PRIORITY_MAP[HighlightType.TEAMFIGHT],
                    extra={"kill_count": len(window_kills)},
                ))
                used_indices.update(window_kills)

        return teamfights

    def _deduplicate(self, highlights: list[Highlight], min_gap: float = 20.0) -> list[Highlight]:
        """
        近い時刻のハイライトをまとめる (min_gap 秒以内は重複とみなす)
        優先度の高い方を残す
        """
        if not highlights:
            return []

        highlights.sort(key=lambda h: h.game_time)
        result = [highlights[0]]
        for h in highlights[1:]:
            if h.game_time - result[-1].game_time >= min_gap:
                result.append(h)
            elif h.priority > result[-1].priority:
                result[-1] = h  # より優先度の高いものに置き換え

        return result

    def limit_by_duration(
        self,
        highlights: list[Highlight],
        max_duration_minutes: float,
        clip_duration: float = 13.0,  # pre(8) + post(5)
    ) -> list[Highlight]:
        """
        最大動画時間に収まるようにハイライトを間引く
        優先度の高いものを残す
        """
        max_clips = int((max_duration_minutes * 60) / clip_duration)
        if len(highlights) <= max_clips:
            return highlights

        # 優先度順でトップNを選択
        selected = sorted(highlights, key=lambda h: h.priority, reverse=True)[:max_clips]
        # 時系列順に戻す
        selected.sort(key=lambda h: h.game_time)
        logger.info(f"クリップ数を {len(highlights)} → {len(selected)} に削減")
        return selected
