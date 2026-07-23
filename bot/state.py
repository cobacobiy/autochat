from dataclasses import dataclass, field


@dataclass
class BotState:
    daily_reply_date: str = ""
    daily_reply_counter: int = 0
    daily_skip_count: int = 0
    daily_unanswered_count: int = 0
    daily_ai_replied_count: int = 0
    has_setup_tabs: bool = False
    replied_cache: dict[str, float] = field(default_factory=dict)
    sent_messages: dict[str, set[str]] = field(default_factory=dict)
    knowledge_base: str = ""
    knowledge_answers: dict = field(default_factory=dict)

bot_state = BotState()
