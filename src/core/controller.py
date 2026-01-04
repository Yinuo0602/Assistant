import time
import random
import json
import threading
import queue
from src.core.event_bus import EventBus, EventType
from src.core.ai import AIGateway
from src.core.executor import DouyinExecutor
from src.utils.logger import get_logger

logger = get_logger()

class LiveController:
    def __init__(self, executor: DouyinExecutor, config_path="config/config.json", speech_path="config/speech_templates.json"):
        self.executor = executor
        self.config = self._load_json(config_path)
        self.speech_templates = self._load_json(speech_path)
        
        
        self.ai = AIGateway(config_path)
        
        # Init attributes BEFORE starting threads
        self.msg_queue = queue.Queue()
        self.running = True
        self.features = self.config.get("features", {})
        self.anchor_name = self.config.get("douyin", {}).get("anchor_name", "")
        self.processed_cache = {}
        self.global_cache = {} # Global event throttling
        
        # Start Threads
        threading.Thread(target=self._enqueue_thread, daemon=True).start()
        
        self.last_active_time = time.time()
        threading.Thread(target=self._bg_task_loop, daemon=True).start()

        # Subscribe to Events
        logger.info("LiveController subscribing to events...")
        EventBus.subscribe(EventType.DANMU, self._handle_danmu)
        EventBus.subscribe(EventType.WELCOME, self._handle_welcome)
        EventBus.subscribe(EventType.GIFT, self._handle_gift)
        EventBus.subscribe(EventType.FOLLOW, self._handle_follow)
        EventBus.subscribe("like", self._handle_like)
        EventBus.subscribe(EventType.SPEECH, self._handle_speech)

        
    def _load_json(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load JSON {path}: {e}")
            return {}

    def reload_config(self):
        """重新加载配置和模板"""
        logger.info("Reloading configuration...")
        self.config = self._load_json("config/config.json")
        self.speech_templates = self._load_json("config/speech_templates.json")
        
        # Reload AI templates
        self.ai.reload_templates("config/speech_templates.json")
        
        # Update features from config
        self.features = self.config.get("features", {})
        self.anchor_name = self.config.get("douyin", {}).get("anchor_name", "")
        
        logger.info("Configuration reloaded successfully.")
        
        self.msg_queue = queue.Queue() # 发送队列，防止发送太快
        self.running = True
        
        # 功能开关
        self.features = self.config.get("features", {})
        self.anchor_name = self.config.get("douyin", {}).get("anchor_name", "") # Default empty
        
        self.processed_cache = {} # Key: "type:user" -> timestamp
        self.global_cache = {}

        # 启动消费者线程
        threading.Thread(target=self._msg_consumer, daemon=True).start()
        
        # 订阅事件 (不需要重新订阅，因为 handler 还是同一个实例的方法)
        # self._subscribe_events()

    def _load_json(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}

    def _subscribe_events(self):
        EventBus.subscribe(EventType.DANMU, self._handle_danmu)
        EventBus.subscribe(EventType.WELCOME, self._handle_welcome)
        EventBus.subscribe(EventType.GIFT, self._handle_gift)
        EventBus.subscribe(EventType.FOLLOW, self._handle_follow)
        EventBus.subscribe("like", self._handle_like)
        EventBus.subscribe(EventType.SPEECH, self._handle_speech)

    def _unsubscribe_events(self):
        EventBus.unsubscribe(EventType.DANMU, self._handle_danmu)
        EventBus.unsubscribe(EventType.WELCOME, self._handle_welcome)
        EventBus.unsubscribe(EventType.GIFT, self._handle_gift)
        EventBus.unsubscribe(EventType.FOLLOW, self._handle_follow)
        EventBus.unsubscribe("like", self._handle_like)
        EventBus.unsubscribe(EventType.SPEECH, self._handle_speech)

    def _msg_consumer(self):
        """
        消息发送消费者，控制发送频率。
        """
        while self.running:
            try:
                # 阻塞获取，超时 1 秒以便检查 running
                msg = self.msg_queue.get(timeout=1)
                self.executor.send_danmu(msg)
                # 随机间隔 2-5 秒，防止封控
                time.sleep(random.uniform(2, 5))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Consumer error: {e}")

    def stop(self):
        self.running = False
        self._unsubscribe_events()
        
    def update_ai_config(self, provider, api_key, model_name):
        self.ai.update_config(provider, api_key, model_name)
        # Also update internal config dict if needed
        self.config["ai"]["provider"] = provider
        if provider not in self.config["ai"]: 
            self.config["ai"][provider] = {}
        self.config["ai"][provider]["api_key"] = api_key
        self.config["ai"][provider]["model"] = model_name

    def set_feature(self, feature_name, enabled: bool):
        self.features[feature_name] = enabled
        logger.info(f"功能状态更新: '{feature_name}' -> {enabled}")

    def set_anchor_name(self, name):
        self.anchor_name = name
        logger.info(f"主播助理昵称已更新: {name}")

    # --- Event Handlers ---

    def _is_self(self, user):
        if not user or not self.anchor_name:
            return False
        # Normalize
        u = user.strip().replace("：", "").replace(":", "").replace("@", "")
        a = self.anchor_name.strip().replace("：", "").replace(":", "").replace("@", "")
        return u == a

    def _handle_danmu(self, data: dict):
        """
        处理弹幕：关键词 -> AI
        """
        user = data.get("user", "")
        content = data.get("content", "")
        
        # 0. 忽略主播助理自己的消息
        if self._is_self(user):
            logger.info(f"忽略自身消息: {user}: {content}")
            return

        logger.info(f"收到弹幕 [用户:{user}] [内容:{content}]")
        
        self.last_active_time = time.time() # Update active time for Idle Check
        
        # 1. 检查 @我 
        # 逻辑优化: 
        # A. 包含 "主播", "助理" 等关键词
        # B. 包含 "@主播", "@失心疯" (需配置)
        # C. 简单的 "@" 判断 (如果想这就触发的话)
        
        # 1. Get Keywords (Prioritize douyin section)
        at_keywords = self.config.get("douyin", {}).get("at_keywords", [])
        if not at_keywords:
            at_keywords = self.config.get("features", {}).get("at_keywords", ["主播", "助理"])
            
        is_at_me = False
        match_reason = ""
        
        for k in at_keywords:
            if k and k in content:
                is_at_me = True
                match_reason = f"Keyword match: {k}"
                break
        
        # 动态昵称判断
        if not is_at_me and self.anchor_name and self.anchor_name in content:
            is_at_me = True
            match_reason = f"Anchor name match: {self.anchor_name}"

        # 2. 关键词匹配 (示例，实际应从 Config 读取)
        # TODO: Implement keyword matcher
        
        # 3. AI 回复
        # 触发条件：@我 (enable_at_reply) OR 随机概率 (enable_ai_reply)
        # 必须先开启总开关
        if not self.features.get("enable_ai_global", True):
             return

        should_reply_ai = False
        reply_prob = self.config.get("ai", {}).get("reply_probability", 0.3)
        
        # Check specific feature flags (Default AT reply to True to match UI expectation if missing)
        enable_at = self.features.get("enable_at_reply", True) 
        enable_random = self.features.get("enable_ai_reply", False)
        
        # DEBUG LOG
        logger.info(f"[AI Check] IsAt:{is_at_me} (Reason:{match_reason}) | EnGlobal:{self.features.get('enable_ai_global', True)} | EnAt:{enable_at} | EnRand:{enable_random}")

        if is_at_me and enable_at:
            should_reply_ai = True
            logger.info(f"触发 @回复 ({match_reason})，对象: {user}")
        elif enable_random and random.random() < reply_prob:
            should_reply_ai = True
            
        if should_reply_ai:
            # 异步调用 AI 防止阻塞
            threading.Thread(target=self._async_ai_reply, args=(content, user)).start()

    def _trigger_ai_event(self, event_type, user, extra=""):
        """
        触发 AI 对特定事件的回复
        """
        prompts = self.speech_templates.get("ai_prompts", {})
        prompt = ""
        
        if event_type == "welcome":
            tpl = prompts.get("event_welcome", "观众 {user} 进入了直播间，请用热情风趣幽默的方式欢迎他。回复必须包含他的完整昵称“{user}")
            prompt = tpl.format(user=user)
        elif event_type == "gift":
            tpl = prompts.get("event_gift", "观众 {user} 送出了 {gift_name}礼物，这是真金白银的支持，请用最热情的语气感谢他，并夸赞他大气。回复必须包含他的完整昵称“{user}")
            prompt = tpl.format(user=user, gift_name=extra)
        elif event_type == "follow":
            tpl = prompts.get("event_follow", "观众 {user} 关注了主播，请感谢他的关注，并表示欢迎加入大家庭。回复必须包含他的完整昵称“{user}”")
            prompt = tpl.format(user=user)
        elif event_type == "like":
            tpl = prompts.get("event_like", "观众 {user} 为主播点赞了，请感谢他的支持。回复必须包含他的完整昵称“{user}”")
            prompt = tpl.format(user=user)
        
        if prompt:
             logger.info(f"Triggering AI event reply for {event_type} - {user}")
             threading.Thread(target=self._async_ai_event_reply, args=(prompt,)).start()

    def _async_ai_event_reply(self, prompt):
        # 专门用于事件的 AI 回复线程
        prompts = self.speech_templates.get("ai_prompts", {})
        sys_prompt = prompts.get("system_event", "你是一个高情商的直播间助理，负责回复各种事件（欢迎、感谢送礼等）。回答要简短（20字以内），语气热情风趣幽默活泼。")
        reply = self.ai.chat(prompt, system_prompt=sys_prompt)
        if reply:
            self._enqueue_msg(reply)

    def _async_ai_reply(self, content, user):
        logger.info(f"Thread: Starting AI reply for {user}")
        
        prompts = self.speech_templates.get("ai_prompts", {})
        anchor_name = self.anchor_name if self.anchor_name else "我"
        
        # 构造 prompt
        tpl = prompts.get("reply_anchor", "你是主播助理【{anchor_name}】。观众【{user}】对你说：“{content}”。请以主播助理的身份直接回复他，不要以第三人称称呼主播。回复必须包含他的完整昵称“@{user}”。")
        # 安全 format，防止 tpl 不包含 key
        try:
            prompt = tpl.format(anchor_name=anchor_name, user=user, content=content)
        except Exception as e:
            logger.warning(f"Format prompt parsing failed: {e}")
            prompt = f"你是主播助理【{anchor_name}】。观众【{user}】对你说：“{content}”。请以主播助理的身份直接回复他，不要以第三人称称呼主播。回复必须包含他的完整昵称“@{user}”。"

        # Get system prompt
        sys_prompt = prompts.get("system_default", None)

        reply = self.ai.chat(prompt, system_prompt=sys_prompt)
        if reply:
            self._enqueue_msg(reply)
        else:
            logger.warning("AI did not return a reply.")

    def _allow_event(self, event_type, user, ttl=5):
        """
        每人去重逻辑 (Per-user duplication check)
        """
        if not user: return False
        key = f"{event_type}:{user}"
        now = time.time()
        last = self.processed_cache.get(key, 0)
        if now - last < ttl:
            return False
        self.processed_cache[key] = now
        # Simple cleanup
        if len(self.processed_cache) > 2000:
            self.processed_cache.clear()
        return True

    def _check_global_throttle(self, event_type, interval):
        """
        全局频率限制 (Global throttling)
        return True if allowed, False if throttled
        """
        now = time.time()
        last = self.global_cache.get(event_type, 0)
        
        if now - last < interval:
            return False
            
        self.global_cache[event_type] = now
        return True

    def _should_block_rule_event(self):
        # 如果关联了 rules，且总开关关闭，则阻断
        if self.features.get("link_ai_to_rules", False):
            if not self.features.get("enable_ai_global", True):
                return True
        return False

    def _handle_welcome(self, data: dict):
        if not self.features.get("enable_welcome", True):
            return

        username = data.get("user")
        if not username:
             raw = data.get("raw", "")
             username = raw.replace("来了", "").replace("进入直播间", "").strip()

        # 1. Per-User De-dupe (防止同一个人反复刷屏，TTL 固定 60s)
        if not self._allow_event("welcome", username, ttl=60):
            return

        # 2. Global Throttle (全局限流，使用配置的 welcome_interval)
        ttl = float(self.config.get("ai", {}).get("welcome_interval", 4))
        if not self._check_global_throttle("welcome", interval=ttl):
            logger.debug(f"Welcome globally throttled (Interval: {ttl}s)")
            return

        # Check AI Linking
        if self.features.get("link_ai_to_rules", False):
            if self.features.get("enable_ai_global", True):
                self._trigger_ai_event("welcome", username)
                return
            else:
                return

        logger.info(f"Preparing welcome for user: {username}")
        tpls = self.speech_templates.get("welcome", [])
        if tpls and username:
            tpl = random.choice(tpls)
            msg = tpl.format(username=username)
            self._enqueue_msg(msg)

    def _handle_gift(self, data: dict):
        if not self.features.get("enable_thanks_gift", True):
            return
            
        gift_name = "礼物"
        user = data.get("user", "")
        
        # Per-User Throttle (Only limit the same user, others can trigger freely)
        ttl = float(self.config.get("ai", {}).get("gift_interval", 5))
        if not self._allow_event("gift", user, ttl=ttl):
            return

        # Check AI Linking
        if self.features.get("link_ai_to_rules", False):
            if self.features.get("enable_ai_global", True):
                self._trigger_ai_event("gift", user, gift_name)
                return
            else:
                return

        raw = data.get("raw", "")
        try:
            if "送出" in raw:
                suffix = raw.split("送出")[-1]
                suffix = suffix.replace("了", "", 1).strip()
                count_str = ""
                if "×" in suffix:
                    parts = suffix.split("×")
                    gift_name = parts[0].strip()
                    if len(parts) > 1: count_str = "×" + parts[1].strip()
                elif "x" in suffix:
                    parts = suffix.split("x")
                    gift_name = parts[0].strip()
                    if len(parts) > 1: count_str = "×" + parts[1].strip()
                else:
                    gift_name = suffix
                if not gift_name:
                    gift_name = f"小心意 {count_str}".strip()
        except:
            pass

        tpls = self.speech_templates.get("thanks_gift", [])
        if tpls:
            tpl = random.choice(tpls)
            self._enqueue_msg(tpl.format(username=user, gift_name=gift_name))
        else:
            self._enqueue_msg(f"谢谢宝宝送的礼物！爱你！")

    def _handle_follow(self, data: dict):
        if not self.features.get("enable_thanks_follow", True):
            return
            
        user = data.get("user", "")
        if self._is_self(user): return
        
        # Per-User Throttle
        ttl = float(self.config.get("ai", {}).get("follow_interval", 6))
        if not self._allow_event("follow", user, ttl=ttl):
            return

        if self.features.get("link_ai_to_rules", False):
            if self.features.get("enable_ai_global", True):
                self._trigger_ai_event("follow", user)
                return
            else:
                return

        tpls = self.speech_templates.get("thanks_follow", [])
        if tpls:
            tpl = random.choice(tpls)
            self._enqueue_msg(tpl.format(username=user))
        else:
            self._enqueue_msg("感谢关注，也就是最好的相遇~")

    def _handle_like(self, data: dict):
        if not self.features.get("enable_thanks_like", True):
            return
            
        username = data.get("user", "")
        if not username:
             raw = data.get("raw", "")
             # Clean up the raw text to extract username
             username = raw.replace("为主播点赞了", "").replace("点赞了", "").strip()
             # Fix: Remove trailing colons that might be left over
             username = username.rstrip("：").rstrip(":")
             
        if self._is_self(username): return
        
        # Per-User Throttle
        ttl = float(self.config.get("ai", {}).get("like_interval", 7))
        if not self._allow_event("like", username, ttl=ttl):
            return

        if self.features.get("link_ai_to_rules", False):
            if self.features.get("enable_ai_global", True):
                self._trigger_ai_event("like", username)
                return
            else:
                return

        tpls = self.speech_templates.get("thanks_like", [])
        if tpls:
            tpl = random.choice(tpls)
            msg = tpl.format(username=username)
            self._enqueue_msg(msg)
        
    def _handle_speech(self, data: dict):
        # 主播说话触发的逻辑
        text = data.get("text", "")
        # 例如主播说“给大家发个福袋”，助手回复“福袋已上，大家快抢”
        # 简单回显
        logger.info(f"主播语音识别: {text}")
        if "欢迎" in text:
             self._enqueue_msg("欢迎大家，还没关注的点点关注！")

    def _enqueue_msg(self, text):
        # 策略更改: 总是保留最新消息
        # 如果队列满了 (>=10)，移除旧消息直到腾出空间
        while self.msg_queue.qsize() >= 10:
            try:
                dropped = self.msg_queue.get_nowait()
                # logger.debug(f"Queue full, dropped old msg: {dropped}")
            except queue.Empty:
                break
        
        self.msg_queue.put(text)

    def _enqueue_thread(self):
        """消费消息队列，发送弹幕"""
        while self.running:
            try:
                msg = self.msg_queue.get(timeout=1)
                self.executor.send_danmu(msg)
                # 模拟人工间隔
                time.sleep(random.uniform(0.5, 1.2))
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Send Daemon Error: {e}")

    def _bg_task_loop(self):
        """后台定时任务：暖场等"""
        last_fixed_warmup = time.time()
        
        while True:
            time.sleep(5)
            try:
                now = time.time()
                
                # 1. Fixed Auto Warmup (Periodic, Independent of idle)
                if self.features.get("enable_auto_warmup", False):
                    interval = self.config.get("ai", {}).get("warmup_interval", 180)
                    if now - last_fixed_warmup > interval:
                        self._trigger_warmup()
                        last_fixed_warmup = now

                # 2. AI Idle Warmup (Dependent on idle time)
                if self.features.get("enable_ai_warmup", False) and self.features.get("enable_ai_global", True):
                    ai_interval = self.config.get("ai", {}).get("ai_warmup_interval", 120)
                    if now - self.last_active_time > ai_interval:
                        self._trigger_ai_warmup()
                        self.last_active_time = now # Reset idle timer to avoid spam
                        
            except Exception as e:
                logger.error(f"BG Task Error: {e}")

    def _trigger_warmup(self):
        tpls = self.speech_templates.get("interactive", [])
        if tpls:
            msg = random.choice(tpls)
            self._enqueue_msg(msg)
            logger.info(f"Auto Warmup triggered: {msg}")

    def _trigger_ai_warmup(self):
        prompts = self.speech_templates.get("ai_prompts", {})
        prompt = prompts.get("event_idle", "现在直播间冷场了，请说句话活跃气氛。")
        logger.info("Triggering AI Idle Warmup...")
        threading.Thread(target=self._async_ai_event_reply, args=(prompt,)).start()

