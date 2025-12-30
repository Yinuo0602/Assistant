import time
import threading
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from src.core.event_bus import EventBus, EventType
from src.core.auth import AuthManager
from src.utils.logger import get_logger

logger = get_logger()

class DouyinLiveListener:
    def __init__(self, room_url: str, executor=None):
        self.room_url = room_url
        self.executor = executor
        self.running = False
        self.page: Page = None
        self.auth_manager = AuthManager()
        self.last_msg_ids = set() # 用内容或hash去重，DOM监听比较简陋

    def start(self, browser_context):
        """
        开始监听。需要在独立线程运行，因为会阻塞。
        browser_context: 由 AuthManager 创建的 context
        """
        self.running = True
        try:
            self.page = browser_context.new_page()
            
            # 如果有 executor，注入 page
            if self.executor:
                self.executor.set_page(self.page)

            logger.info(f"Navigating to live room: {self.room_url}")
            self.page.goto(self.room_url, timeout=60000)
            
            # 等待进入直播间，处理可能出现的弹窗
            time.sleep(5) 
            
            logger.info("Started listening loop...")
            while self.running:
                self._scan_chat_messages()
                time.sleep(1) # 轮询间隔
                
        except Exception as e:
            logger.error(f"Listener error: {e}")
        finally:
            logger.info("Listener stopped.")
            if self.page:
                self.page.close()

    def stop(self):
        self.running = False

    def _scan_chat_messages(self):
        """
        扫描聊天区域。
        注意：这是一个简化实现。抖音的 class 是混淆的。
        通常寻找包含特定属性或结构的元素。
        """
        if not self.page:
            return

        try:
            # 尝试定位聊天容器。
            # 策略：查找含有 data-e2e="chat-message" 或者类似的稳定属性
            # 如果没有稳定属性，可能需要根据结构 text content 查找
            # 这里使用一个假设的通用选择器，实际需要根据当前抖音网页版调整
            
            # 2024/2025 抖音网页版常见结构：
            # 消息容器通常在右侧侧边栏
            # 每一行消息是一个 div
            
            # 这是一个示例选择器，实际开发中需要使用 DevTools 确认
            # 我们会抓取所有的 chat rows，然后只处理最后几条新的
            
            # 假设 class 虽然混淆，但 text content 结构是 "用户名: 消息内容"
            # 我们可以抓取 .webcast-chatroom__content 下的子元素
            
            # 使用 evaluate 执行 JS 获取文本效率更高
            new_messages = self.page.evaluate("""() => {
                // 试图找到聊天列表容器
                // 这个 selector 是个猜测，实际必须调试。
                // 经常变动的 class： .webcast-chatroom___list
                // 比较稳妥的是找 role="log" 或者包含特定文本的容器
                
                const chatRows = document.querySelectorAll('.webcast-chatroom__item'); 
                const results = [];
                // 取最后 10 条
                const start = Math.max(0, chatRows.length - 10);
                for (let i = start; i < chatRows.length; i++) {
                    results.push(chatRows[i].innerText);
                }
                return results;
            }""")
            
            for msg_text in new_messages:
                # 简单去重
                msg_hash = hash(msg_text)
                if msg_hash in self.last_msg_ids:
                    continue
                
                self.last_msg_ids.add(msg_hash)
                # 保持 set 大小
                if len(self.last_msg_ids) > 1000:
                    self.last_msg_ids.clear()
                
                self._parse_and_publish(msg_text)

        except Exception as e:
            # 页面可能关闭或网络问题
            pass

    def _parse_and_publish(self, text: str):
        """
        解析文本并发布事件。
        格式通常是: 
        "Level User: Content" 
        "User came in"
        """
        text = text.strip()
        if not text:
            return

        # 简单的关键字匹配逻辑
        if "来了" in text:
            EventBus.publish(EventType.WELCOME, {"raw": text})
            logger.debug(f"[WELCOME] {text}")
        elif "送出了" in text:
            EventBus.publish(EventType.GIFT, {"raw": text})
            logger.debug(f"[GIFT] {text}")
        elif "关注了" in text:
            EventBus.publish(EventType.FOLLOW, {"raw": text})
            logger.debug(f"[FOLLOW] {text}")
        else:
            # 假设是弹幕
            # 尝试分离用户名和内容
            # 这是一个非常粗糙的解析，实际需要更复杂的正则
            parts = text.split("：", 1) # 中文冒号
            if len(parts) == 2:
                user = parts[0]
                content = parts[1]
                EventBus.publish(EventType.DANMU, {"user": user, "content": content})
                logger.info(f"[DANMU] {user}: {content}")
            else:
                pass
