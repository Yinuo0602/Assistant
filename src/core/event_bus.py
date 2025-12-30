from typing import Callable, Dict, List, Any
from src.utils.logger import get_logger

logger = get_logger()

class EventType:
    DANMU = "danmu"           # 弹幕消息
    GIFT = "gift"             # 礼物
    LIKE = "like"             # 点赞
    WELCOME = "welcome"       # 进场
    FOLLOW = "follow"         # 关注
    SPEECH = "speech"         # 主播说话识别结果
    LOG = "log"               # 系统日志（用于UI显示）

class EventBus:
    _subscribers: Dict[str, List[Callable]] = {}

    @classmethod
    def subscribe(cls, event_type: str, handler: Callable):
        """订阅事件"""
        if event_type not in cls._subscribers:
            cls._subscribers[event_type] = []
        cls._subscribers[event_type].append(handler)
        logger.debug(f"Handler subscribed to {event_type}")

    @classmethod
    def publish(cls, event_type: str, data: Any = None):
        """发布事件"""
        if event_type in cls._subscribers:
            for handler in cls._subscribers[event_type]:
                try:
                    handler(data)
                except Exception as e:
                    logger.error(f"Error handling event {event_type}: {e}")
